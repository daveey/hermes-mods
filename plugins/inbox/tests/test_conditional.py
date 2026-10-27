"""Conditional posting: real SQLite stores, subprocess CLI and core-only writers."""
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_store import HERE, env, store


def test_conditional_cap_and_validation(env, monkeypatch):
    inbox, db, _ = env
    monkeypatch.setattr(store, "MAX_OPEN", 1)
    first = post(inbox, if_absent=True)
    db.set_session_read(first["session_id"], True)
    inbox.post("Other", "Body", "Review", key="other", if_absent=True)
    assert inbox.outcome("draft:example")["state"] == "archived"
    assert len(inbox.list()) == 1
    with pytest.raises(ValueError):
        inbox.post("Draft", "Body", "Review", if_absent=True)
    with pytest.raises(ValueError):
        post(inbox, if_absent=True, expected=first["snapshot"])
    with pytest.raises(ValueError):
        post(inbox, expected=None)


def test_active_core_turn_lease_prevents_refresh(env):
    inbox, db, _ = env
    first = post(inbox, if_absent=True)
    assert db.acquire_session_turn_lease(first["session_id"], "test-worker", wait_seconds=0)
    before = inbox.outcome("draft:example")
    with pytest.raises(Exception, match="lease|turn"):
        post(inbox, expected=before)
    assert inbox.outcome("draft:example") == before


def test_typed_edit_action_prevents_refresh(env):
    inbox, _, _ = env
    inbox.types.define("draft-choice", {
        "label": "Draft choice", "fields": {"text": {"required": True}},
        "actions": [{"id": "edit", "label": "Edit", "kind": "resolve",
                     "input": {"field": "text"}}]})
    first = inbox.post("Options", "Choose one", key="draft:example", type="draft-choice",
                       fields={"text": "Original"}, if_absent=True)
    inbox.act(first["session_id"], "edit", "User revision")
    result = inbox.post("Options", "Refreshed", key="draft:example", type="draft-choice",
                        fields={"text": "Replacement"}, expected=first["snapshot"])
    assert not result["applied"]
    assert result["snapshot"]["fields"] == {"text": "User revision"}


def post(inbox, **condition):
    return inbox.post("Draft", "Options", "Review options", key="draft:example", **condition)


def test_create_refresh_and_stale_replay(env):
    inbox, db, _ = env
    first = post(inbox, if_absent=True)
    assert first["applied"] and not first["updated"]
    expected = inbox.outcome("draft:example")
    assert expected == first["snapshot"]
    assert not post(inbox, if_absent=True)["applied"]
    refreshed = post(inbox, expected=expected)
    assert refreshed["applied"] and refreshed["updated"]
    assert refreshed["snapshot"]["revision"] != expected["revision"]
    assert not post(inbox, expected=expected)["applied"]
    assert len(db.get_messages(first["session_id"])) == 2


@pytest.mark.parametrize("change", ["archive", "accept", "core_archive", "core_accept", "message",
                                    "edit", "fields", "title", "queuing", "resolved", "delete", "missing_session"])
def test_stale_snapshot_never_overwrites_user_changes(env, change):
    inbox, db, _ = env
    first = post(inbox, if_absent=True)
    sid, expected = first["session_id"], first["snapshot"]
    if change == "archive":
        inbox.archive(sid)
    elif change == "accept":
        inbox.accept(sid)
    elif change == "core_archive":
        db.set_session_archived(sid, True)
    elif change == "core_accept":
        db.set_session_hidden(sid, False)
    elif change == "message":
        db.append_message(sid, "user", "Use my revised text")
    elif change == "edit":
        db._execute_write(lambda c: c.execute("UPDATE messages SET content='Edited draft' WHERE session_id=?", (sid,)))
    elif change == "title":
        db.set_session_title(sid, "User title")
    elif change == "missing_session":
        db.delete_session(sid)
    elif change == "delete":
        db._execute_write(lambda c: c.execute("DELETE FROM messages WHERE session_id=?", (sid,)))
    else:
        with sqlite3.connect(inbox.path) as c:
            if change == "fields":
                c.execute("UPDATE items SET fields_json=? WHERE session_id=?", ('{"draft":"User edit"}', sid))
            else:
                c.execute("UPDATE items SET state=? WHERE session_id=?", (change, sid))
    before = inbox.outcome("draft:example")
    assert not post(inbox, expected=expected)["applied"]
    assert not post(inbox, if_absent=True)["applied"]
    assert inbox.outcome("draft:example") == before
    if before["state"] != "open" or before["item_state"] != "open":
        assert not post(inbox, expected=before)["applied"]


@pytest.mark.parametrize("refresh", [False, True])
def test_exception_rolls_back_both_databases(env, monkeypatch, refresh):
    inbox, db, _ = env
    before = post(inbox, if_absent=True)["snapshot"] if refresh else None
    def fail(*args, **kw):
        raise RuntimeError("injected failure after sidecar write")
    monkeypatch.setattr(type(db), "append_message", fail)
    with pytest.raises(RuntimeError):
        post(inbox, **({"expected": before} if refresh else {"if_absent": True}))
    assert inbox.outcome("draft:example") == before
    with sqlite3.connect(db.db_path) as c:
        assert c.execute("SELECT count(*) FROM sessions").fetchone()[0] == int(refresh)


# Exercise the actual plugin argparse/CLI entry in a fresh interpreter without loading
# unrelated enabled plugins or accessing the user's profile.
CLI = '''import argparse, importlib.util, sys
spec = importlib.util.spec_from_file_location("inbox_cli", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
p = argparse.ArgumentParser(); m._setup_cli(p)
sys.exit(m._run_cli(p.parse_args(sys.argv[2:])))
'''


def cli(home, *args):
    return subprocess.run([sys.executable, "-c", CLI, str(HERE / "__init__.py"), *args],
                          env=dict(os.environ, HERMES_HOME=str(home)), capture_output=True, text=True,
                          timeout=40)


@pytest.mark.parametrize("refresh", [False, True])
def test_competing_processes_have_exactly_one_winner(env, refresh):
    inbox, db, _ = env
    condition = ["--if-absent"]
    if refresh:
        condition = ["--expected", json.dumps(post(inbox, if_absent=True)["snapshot"])]
    def run(_):
        return cli(inbox.home, "post", "Draft", "--prompt", "Review", "--key", "draft:example", *condition)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(4)))
    assert sorted(r.returncode for r in results) == [0, 3, 3, 3], [(r.stdout, r.stderr) for r in results]
    assert sum(json.loads(r.stdout)["applied"] for r in results) == 1
    actual = inbox.outcome("draft:example")
    assert actual["message_count"] == (2 if refresh else 1)
    with sqlite3.connect(inbox.path) as c:
        assert c.execute("SELECT count(*) FROM items").fetchone()[0] == 1


@pytest.mark.parametrize("target", ["state", "sidecar"])
def test_transaction_blocks_independent_process_writer(env, target):
    inbox, db, _ = env
    first = post(inbox, if_absent=True)
    path = db.db_path if target == "state" else inbox.path
    sql = "UPDATE sessions SET archived=1" if target == "state" else "UPDATE items SET state='accepted'"
    code = "import sqlite3,sys; c=sqlite3.connect(sys.argv[1],timeout=0.1); c.execute(sys.argv[2]); c.commit()"
    with inbox._atomic() as (conn, _):
        # This is the critical interval: snapshot checked, transaction not yet committed.
        assert inbox._snapshot(conn, "draft:example") == first["snapshot"]
        result = subprocess.run([sys.executable, "-c", code, str(path), sql], capture_output=True, text=True)
        assert result.returncode != 0 and "database is locked" in result.stderr
    result = subprocess.run([sys.executable, "-c", code, str(path), sql], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not post(inbox, expected=first["snapshot"])["applied"]


def test_cli_validation_and_outcome(env):
    inbox, _, _ = env
    for condition in (["--expected", "null"], ["--expected", "{}"], ["--expected", "invalid"],
                      ["--if-absent", "--expected", "{}"]):
        result = cli(inbox.home, "post", "Draft", "--prompt", "Review", "--key", "draft:example", *condition)
        assert result.returncode in (1, 2)
    first = post(inbox, if_absent=True)
    result = cli(inbox.home, "outcome", "draft:example")
    assert result.returncode == 0
    assert json.loads(result.stdout)["revision"] == first["snapshot"]["revision"]
