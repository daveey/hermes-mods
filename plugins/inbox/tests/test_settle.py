"""Producer settlement uses real temporary Inbox and SessionDB stores only."""
import json
import sqlite3
import os
import subprocess
import sys

import pytest

from test_store import env, store, HERE
from test_conditional import cli

pytestmark = pytest.mark.needs_core


def post(inbox):
    return inbox.post("Draft", "Options", "Review options", key="draft:example", poster="cli")["session_id"]


def settle(inbox, sid, **kw):
    return inbox.settle(sid, **dict(dict(key="draft:example", poster="cli", outcome="posted", body="Result"), **kw))


@pytest.mark.parametrize("accepted", [False, True, "visible"])
@pytest.mark.parametrize("outcome", ["posted", "vetoed", "expired", "failed"])
def test_settle_and_retry(env, accepted, outcome):
    inbox, db, _ = env
    sid = post(inbox)
    if accepted == "visible":
        db.set_session_hidden(sid, False)
    elif accepted:
        inbox.accept(sid)
    result = settle(inbox, sid, outcome=outcome)
    assert result == dict(status="resolved", session_id=sid, key="draft:example", state="resolved",
                          outcome=outcome, resolved_by="producer:cli")
    before = inbox.outcome("draft:example")
    assert before["resolved_by"] == "producer:cli" and before["resolved_at"]
    assert not inbox.list()
    session = db.get_session(sid)
    assert bool(session["archived"]) == (not accepted)
    assert bool(session["hidden"]) == (not accepted)
    messages = db.get_messages(sid)
    assert len(messages) == 2 and "**Update" in messages[-1]["content"] and "Result" in messages[-1]["content"]
    assert settle(inbox, sid, outcome=outcome, body="Different retry body")["status"] == "already-settled"
    assert inbox.outcome("draft:example") == before
    assert db.get_messages(sid) == messages


@pytest.mark.parametrize("kw,code", [({"key":"wrong"}, "key_mismatch"), ({"poster":"CLI"}, "poster_mismatch"),
                                      ({"outcome":"approved"}, "invalid_outcome"), ({"body":""}, "invalid_body")])
def test_mismatch_untouched(env, kw, code):
    inbox, db, _ = env
    sid = post(inbox)
    before = inbox.outcome("draft:example")
    with pytest.raises(store.SettlementError) as exc:
        settle(inbox, sid, **kw)
    assert exc.value.code == code
    assert inbox.outcome("draft:example") == before


def test_handled_archive_veto_receipt_preserves_archive_and_stops_noise_streak(env):
    inbox, db, _ = env
    ids = []
    for i in range(2):
        sid = inbox.post("Draft", "Options", "Review", key=f"draft:{i}", poster="cli")["session_id"]
        with sqlite3.connect(inbox.path) as c:
            snap = {"name": "review", "actions": [{"id": "veto", "kind": "prompt", "prompt": "Veto this draft"}]}
            c.execute("UPDATE items SET actions_json=? WHERE session_id=?", (json.dumps(snap), sid))
        inbox.archive(sid)
        ids.append(sid)
    before = [inbox.get(sid) for sid in ids]
    messages = [db.get_messages(sid) for sid in ids]
    side = inbox._side()
    assert inbox._ignored_streak(side, db, "cli", inbox.clock()) == 2
    side.close()
    for i, sid in enumerate(ids):
        result = inbox.settle(sid, key=f"draft:{i}", poster="cli", outcome="vetoed", body="Veto honored")
        assert result["status"] == "skipped" and result["state"] == "archived"
        assert result.get("handled_archive") is True
        assert inbox.settle(sid, key=f"draft:{i}", poster="cli", outcome="vetoed", body="Retry") == result
    assert [inbox.get(sid) for sid in ids] == before
    assert [db.get_messages(sid) for sid in ids] == messages
    side = inbox._side()
    assert inbox._ignored_streak(side, db, "cli", inbox.clock()) == 0
    assert inbox._archived_key(side, db, "draft:1", inbox.clock()) is not None
    side.execute("INSERT INTO junk_mutes VALUES ('cli','draft',?,?)", (inbox.clock(), ids[-1]))
    side.commit()
    assert inbox._junk_muted(side, "cli", "draft:new")
    side.close()
    # A real Junk instruction is an independent hold, never cleared by settlement.
    inbox._task_creator = lambda **kw: "owner-task"
    with pytest.raises(store.PosterMuted, match="Junk"):
        inbox.post("Draft", "Options", "Review", key="draft:new", poster="cli")


@pytest.mark.parametrize("outcome", ["posted", "failed", "expired", "vetoed"])
def test_generic_archives_never_become_handled_vetoes(env, outcome):
    inbox, db, _ = env
    sid = post(inbox)
    inbox.archive(sid)
    before = inbox.get(sid)
    assert not settle(inbox, sid, outcome=outcome).get("handled_archive")
    assert inbox.get(sid) == before
    side = inbox._side()
    assert inbox._ignored_streak(side, db, "cli", inbox.clock()) == 1
    side.close()


@pytest.mark.parametrize("change", ["visible", "wrong_key", "wrong_poster", "not_veto"])
def test_handled_archive_requires_identity_hidden_veto(env, change):
    inbox, db, _ = env
    sid = post(inbox)
    with sqlite3.connect(inbox.path) as c:
        snap = {"name": "review", "actions": [{"id": "veto", "kind": "prompt", "prompt": "Veto"}]}
        c.execute("UPDATE items SET actions_json=? WHERE session_id=?", (json.dumps(snap), sid))
    if change == "visible":
        db.set_session_hidden(sid, False)
    inbox.archive(sid)
    kw = {"outcome": "vetoed"}
    if change == "wrong_key": kw["key"] = "other"
    if change == "wrong_poster": kw["poster"] = "other"
    if change == "not_veto": kw["outcome"] = "failed"
    if change.startswith("wrong"):
        with pytest.raises(store.SettlementError): settle(inbox, sid, **kw)
    else:
        assert not settle(inbox, sid, **kw).get("handled_archive")
    with sqlite3.connect(inbox.path) as c:
        assert c.execute("SELECT COUNT(*) FROM handled_archives").fetchone()[0] == 0


def test_missing(env):
    inbox, db, _ = env
    with pytest.raises(store.SettlementError, match="not_found"):
        settle(inbox, "missing")
    assert not inbox.list()
    sid = post(inbox)
    db.delete_session(sid)
    with pytest.raises(store.SettlementError, match="session_missing"):
        settle(inbox, sid)


@pytest.mark.parametrize("change", ["archive", "core_archive", "user_resolve", "queuing"])
def test_user_dispositions_untouched(env, change):
    inbox, db, _ = env
    sid = post(inbox)
    if change == "archive":
        inbox.archive(sid)
    elif change == "core_archive":
        db.set_session_archived(sid, True)
    else:
        with sqlite3.connect(inbox.path) as conn:
            conn.execute("UPDATE items SET state=?,outcome='user-choice' WHERE session_id=?",
                         ("resolved" if change == "user_resolve" else "queuing", sid))
    before = inbox.outcome("draft:example")
    if change == "queuing":
        with pytest.raises(store.SettlementError, match="pending_action"):
            settle(inbox, sid)
    else:
        assert settle(inbox, sid)["status"] == "skipped"
    assert inbox.outcome("draft:example") == before


def test_correction_and_immutable_posted(env):
    inbox, db, _ = env
    sid = post(inbox)
    settle(inbox, sid, outcome="failed")
    assert settle(inbox, sid, outcome="vetoed", body="Policy veto")["status"] == "resolved"
    assert len(db.get_messages(sid)) == 3
    assert settle(inbox, sid, outcome="vetoed")["status"] == "already-settled"
    with pytest.raises(store.SettlementError, match="outcome_conflict"):
        settle(inbox, sid)
    sid2 = inbox.post("Other", "Options", "Review", key="other", poster="cli")["session_id"]
    settle(inbox, sid2, key="other")
    before = inbox.outcome("other")
    with pytest.raises(store.SettlementError, match="outcome_conflict"):
        settle(inbox, sid2, key="other", outcome="failed")
    assert inbox.outcome("other") == before


@pytest.mark.parametrize("stage", ["append", "finalize"])
def test_failure_rolls_back_then_retry(env, monkeypatch, stage):
    inbox, db, _ = env
    sid = post(inbox)
    before = inbox.outcome("draft:example")
    original = type(db).append_message
    def fail(*a, **kw):
        original(*a, **kw)
        raise RuntimeError("after append")
    if stage == "append":
        monkeypatch.setattr(type(db), "append_message", fail)
    else:
        db._execute_write(lambda c: c.execute("CREATE TRIGGER fail_settle BEFORE UPDATE OF archived ON sessions "
                                             "BEGIN SELECT RAISE(ABORT, 'finalize failure'); END"))
    with pytest.raises(Exception):
        settle(inbox, sid)
    assert inbox.outcome("draft:example") == before
    monkeypatch.setattr(type(db), "append_message", original)
    if stage == "finalize":
        db._execute_write(lambda c: c.execute("DROP TRIGGER fail_settle"))
    assert settle(inbox, sid)["status"] == "resolved"
    assert len(db.get_messages(sid)) == 2


def test_process_crash_before_commit_recovers(env):
    inbox, db, _ = env
    sid = post(inbox)
    before = inbox.outcome("draft:example")
    script = '''import importlib.util, os, sys
from hermes_state import SessionDB
spec=importlib.util.spec_from_file_location("settle_store",sys.argv[1])
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
original=SessionDB.append_message
def crash(*a,**kw):
    original(*a,**kw)
    os._exit(73)
SessionDB.append_message=crash
m.Inbox().settle(sys.argv[2],key="draft:example",poster="cli",outcome="posted",body="Result")
'''
    run = subprocess.run([sys.executable, "-c", script, str(HERE / "store.py"), sid],
                         env=dict(os.environ, HERMES_HOME=str(inbox.home)), capture_output=True, timeout=40)
    assert run.returncode == 73, run.stderr
    assert inbox.outcome("draft:example") == before
    assert settle(inbox, sid)["status"] == "resolved"
    assert len(db.get_messages(sid)) == 2


def test_cli(env):
    inbox, db, _ = env
    usage = cli(inbox.home, "settle")
    assert usage.returncode == 2 and json.loads(usage.stdout)["error"] == "invalid_arguments"
    sid = post(inbox)
    args = ["settle", sid, "--key", "draft:example", "--poster", "cli", "--body", "Result"]
    bad = cli(inbox.home, *args, "--outcome", "approved")
    assert bad.returncode != 0 and json.loads(bad.stdout)["error"] == "invalid_outcome"
    for expected in ["resolved", "already-settled"]:
        run = cli(inbox.home, *args, "--outcome", "posted")
        assert run.returncode == 0, run.stderr
        assert json.loads(run.stdout)["status"] == expected
    read = cli(inbox.home, "outcome", "draft:example")
    assert json.loads(read.stdout)["resolved_by"] == "producer:cli"


def test_concurrent_cli_settlement_once(env):
    from concurrent.futures import ThreadPoolExecutor
    inbox, db, _ = env
    sid = post(inbox)
    def run(_):
        return cli(inbox.home, "settle", sid, "--key", "draft:example", "--poster", "cli",
                   "--outcome", "posted", "--body", "Result")
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, range(3)))
    assert all(r.returncode == 0 for r in results), [(r.stdout, r.stderr) for r in results]
    assert sorted(json.loads(r.stdout)["status"] for r in results) == ["already-settled", "already-settled", "resolved"]
    assert len(db.get_messages(sid)) == 2


def test_active_turn_guard_is_atomic_and_retryable(env):
    inbox, db, _ = env
    sid = post(inbox)
    inbox.accept(sid)
    assert db.acquire_session_turn_lease(sid, "settlement-test", wait_seconds=0)
    before = inbox.outcome("draft:example")
    run = cli(inbox.home, "settle", sid, "--key", "draft:example", "--poster", "cli",
              "--outcome", "posted", "--body", "Result")
    assert run.returncode == 1
    error = json.loads(run.stdout)
    assert error["error"] == "settlement_failed" and "lease" in error["message"].lower()
    assert inbox.outcome("draft:example") == before
    db.release_session_turn_lease(sid, "settlement-test")
    assert settle(inbox, sid)["status"] == "resolved"


def test_correction_preserves_unrelated_messages(env):
    inbox, db, _ = env
    sid = post(inbox)
    inbox.accept(sid)
    settle(inbox, sid, outcome="failed")
    db.append_message(sid, "user", "Keep discussing the next draft")
    before = db.get_messages(sid)
    settle(inbox, sid, outcome="vetoed", body="Policy correction")
    assert db.get_messages(sid)[:-1] == before
    settle(inbox, sid, outcome="vetoed")
    assert len(db.get_messages(sid)) == len(before) + 1
    assert not db.get_session(sid)["archived"]


def test_archived_accepted_correction_is_skipped(env):
    inbox, db, _ = env
    sid = post(inbox)
    inbox.accept(sid)
    settle(inbox, sid, outcome="failed")
    db.set_session_archived(sid, True)
    before = inbox.outcome("draft:example")
    assert settle(inbox, sid, outcome="vetoed")["status"] == "skipped"
    assert inbox.outcome("draft:example") == before


def test_existing_sidecar_migration(env):
    inbox, db, _ = env
    sid = post(inbox)
    with sqlite3.connect(inbox.path) as conn:
        conn.execute("ALTER TABLE items DROP COLUMN resolved_by")
    assert settle(inbox, sid)["resolved_by"] == "producer:cli"

