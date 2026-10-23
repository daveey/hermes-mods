"""cross_task: relays task_* requests through the session-mentions outbox, and @task refs in the
user message get a pre_llm_call hint naming the right tool."""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("sm_plugin_task", Path(__file__).resolve().parent.parent / "__init__.py")
sm = importlib.util.module_from_spec(_spec)
sys.modules["sm_plugin_task"] = sm
_spec.loader.exec_module(sm)  # type: ignore[union-attr]


@pytest.fixture
def relay(tmp_path, monkeypatch):
    sent = []
    for sub in ("outbox", "claimed", "results"):
        (tmp_path / sub).mkdir()
    monkeypatch.setattr(sm.rf, "root", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HUB_STATE", str(tmp_path / "no-hub"))  # never the real hermes-hub replica
    monkeypatch.setattr(sm, "_sender_info", lambda sid: {"session_id": sid or ""})
    stop = threading.Event()

    def courier():
        while not stop.is_set():
            for f in (tmp_path / "outbox").glob("*.json"):
                req = json.loads(f.read_text())
                f.unlink()
                sent.append(req)
                (tmp_path / "results" / f.name).write_text(json.dumps({"ok": True, "echo": req["action"]}))
            time.sleep(0.05)

    t = threading.Thread(target=courier, daemon=True)
    t.start()
    yield sent
    stop.set()


def test_read_relays_task_read_with_aliased_target(relay):
    out = json.loads(sm.handle_task({"action": "read", "task_id": "@task:bb@mac-mini/t_bbbb2222"}, session_id="s1"))
    assert out == {"ok": True, "echo": "task_read"}
    assert relay[0]["args"] == {"target": "@task:bb@mac-mini/t_bbbb2222"}


def test_list_passes_status_filter(relay):
    json.loads(sm.handle_task({"action": "list", "status": "running,blocked", "query": "wake"}))
    assert relay[0]["action"] == "task_list"
    assert relay[0]["args"] == {"status": "running,blocked", "query": "wake"}


def test_comment_requires_message_and_target():
    assert "message is required" in sm.handle_task({"action": "comment", "target": "t_aaaa1111"})
    assert "target is required" in sm.handle_task({"action": "read"})
    assert "action must be" in sm.handle_task({"action": "send", "target": "t_aaaa1111"})


def test_hint_routes_remote_refs_to_cross_task_and_local_refs_to_kanban_show():
    ctx = sm._pre_llm_call(user_message="see @task:bb@mac-mini/t_bbbb2222 and @task:default/t_aaaa1111")["context"]
    assert "cross_task" in ctx and "@task:bb@mac-mini/t_bbbb2222" in ctx
    assert "kanban_show(task_id=t_aaaa1111, board=default)" in ctx
    assert sm._pre_llm_call(user_message="no refs here, t_aaaa1111 alone") is None



# --- cross_task read/comment on a card on ANOTHER gateway shepherds it (hermes-hub) ---

import sqlite3


def _replica(tmp_path, me="macbook", rows=(("hub", "default/t_cccc3333"), ("macbook", "default/t_aaaa1111"))):
    db = tmp_path / "hub.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);"
                    "CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, data TEXT);")
    c.execute("INSERT INTO meta VALUES ('gateway', ?)", (me,))
    c.executemany("INSERT INTO replica VALUES (?, 'tasks', ?, '{}')", rows)
    c.commit()
    c.close()
    return db


@pytest.fixture
def hub(tmp_path, monkeypatch):
    spawned = []
    db = _replica(tmp_path)
    real = sm._shepherd.maybe_shepherd

    def fake(target, session_id, profile, ledger_dir, **kw):
        return real(target, session_id, profile, ledger_dir, db=db, cli="/bin/hermes-hub", spawn=spawned.append)

    monkeypatch.setattr(sm._shepherd, "maybe_shepherd", fake)
    return spawned


def test_read_and_comment_each_queue_one_deduped_shepherd_request(relay, hub):
    out = json.loads(sm.handle_task({"action": "read", "target": "@task:default@hermes-hub/t_cccc3333"}, session_id="s_mac"))
    assert out == {"ok": True, "echo": "task_read"}
    sm.handle_task({"action": "read", "target": "t_cccc3333"}, session_id="s_mac")  # same (session, task): deduped
    assert len(hub) == 1
    argv = hub[0]
    assert argv[:6] == ["/bin/hermes-hub", "request", "hub", "tasks", "default/t_cccc3333", "shepherd"]
    assert "session=s_mac" in argv and "profile=default" in argv and argv[argv.index("--id") + 1].startswith("shep-")
    sm.handle_task({"action": "comment", "target": "hub:t_cccc3333", "message": "hi"}, session_id="s_other")
    assert len(hub) == 2 and "session=s_other" in hub[1]
    sm.handle_task({"action": "comment", "target": "hub:t_cccc3333", "message": "again"}, session_id="s_other")
    assert len(hub) == 2


def test_local_unknown_and_list_never_shepherd(relay, hub):
    sm.handle_task({"action": "read", "target": "t_aaaa1111"}, session_id="s_mac")  # our own card
    sm.handle_task({"action": "read", "target": "t_ffff9999"}, session_id="s_mac")  # not in the replica
    sm.handle_task({"action": "list", "query": "t_cccc3333"}, session_id="s_mac")
    sm.handle_task({"action": "read", "target": "t_cccc3333"}, session_id=None)
    assert hub == []


def test_shepherd_failure_never_changes_the_tool_output(relay, tmp_path, monkeypatch):
    db = _replica(tmp_path)

    def boom(argv):
        raise OSError("hermes-hub missing")

    real = sm._shepherd.maybe_shepherd
    monkeypatch.setattr(sm._shepherd, "maybe_shepherd",
                        lambda t, s, p, d, **kw: real(t, s, p, d, db=db, cli="/bin/hermes-hub", spawn=boom))
    out = json.loads(sm.handle_task({"action": "read", "target": "t_cccc3333"}, session_id="s_mac"))
    assert out == {"ok": True, "echo": "task_read"}
    assert not (tmp_path / sm._shepherd.LEDGER_NAME).exists()  # not marked sent, so a later call retries

    def explode(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(sm._shepherd, "maybe_shepherd", explode)
    out = json.loads(sm.handle_task({"action": "comment", "target": "t_cccc3333", "message": "x"}, session_id="s_mac"))
    assert out == {"ok": True, "echo": "task_comment"}


def test_missing_replica_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HUB_STATE", str(tmp_path / "no-hub"))
    assert sm._shepherd.maybe_shepherd("t_cccc3333", "s1", "default", tmp_path, db=None, cli="/x",
                                       spawn=lambda a: 1 / 0) is None
