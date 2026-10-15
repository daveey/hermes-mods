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
