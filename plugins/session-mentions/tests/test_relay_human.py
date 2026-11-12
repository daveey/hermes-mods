"""kanban-d1b921f32e: cross_task comment relays_user_message is proven on the origin gateway before it is sent."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("sm_plugin_relay", Path(__file__).resolve().parent.parent / "__init__.py")
sm = importlib.util.module_from_spec(_spec)
sys.modules["sm_plugin_relay"] = sm
_spec.loader.exec_module(sm)  # type: ignore[union-attr]


@pytest.fixture
def relay(tmp_path, monkeypatch):
    sent = []
    for sub in ("outbox", "claimed", "results"):
        (tmp_path / sub).mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(sm.rf, "root", lambda: tmp_path)
    monkeypatch.setattr(sm.rf, "_hermes_home", lambda: home)
    monkeypatch.setenv("HERMES_HUB_STATE", str(tmp_path / "no-hub"))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setattr(sm, "_sender_info", lambda sid: {"session_id": sid or ""})
    stop = threading.Event()

    def courier():
        while not stop.is_set():
            for f in (tmp_path / "outbox").glob("*.json"):
                req = json.loads(f.read_text())
                f.unlink()
                sent.append(req)
                (tmp_path / "results" / f.name).write_text(json.dumps({"ok": True}))
            time.sleep(0.05)

    threading.Thread(target=courier, daemon=True).start()
    yield sent, home
    stop.set()


def _user_message(home: Path, session: str, at: float) -> int:
    db = sqlite3.connect(home / "state.db")
    db.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, "
               "role TEXT, content TEXT, timestamp REAL)")
    cur = db.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', 'left', ?)",
                     (session, at))
    db.commit()
    db.close()
    return int(cur.lastrowid or 0)


TARGET = "@task:default@macbook/t_01a1cc1a8bb39958"


def test_relay_is_stamped_once_per_user_message(relay):
    sent, home = relay
    mid = _user_message(home, "chat-1", 1700.5)
    out = json.loads(sm.handle_task({"action": "comment", "target": TARGET, "message": "David: left ring",
                                     "relays_user_message": True}, session_id="chat-1"))
    assert out == {"ok": True}
    assert sent[0]["args"]["relayed_human"] == {"session": "chat-1", "message_id": mid, "at": 1700}
    again = sm.handle_task({"action": "comment", "target": TARGET, "message": "and again",
                            "relays_user_message": True}, session_id="chat-1")
    assert "already relayed" in again and len(sent) == 1
    _user_message(home, "chat-1", 1800.0)  # David's next reply may be relayed
    assert json.loads(sm.handle_task({"action": "comment", "target": TARGET, "message": "next",
                                      "relays_user_message": True}, session_id="chat-1")) == {"ok": True}


def test_worker_and_sessionless_relays_are_refused(relay, monkeypatch):
    sent, home = relay
    _user_message(home, "chat-1", 1700.0)
    assert "interactive chat" in sm.handle_task({"action": "comment", "target": TARGET, "message": "x",
                                                 "relays_user_message": True}, session_id=None)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_worker")
    assert "Kanban worker" in sm.handle_task({"action": "comment", "target": TARGET, "message": "x",
                                              "relays_user_message": True}, session_id="chat-1")
    assert sent == []


def test_session_without_user_message_is_refused(relay):
    sent, _home = relay
    assert "no user message" in sm.handle_task({"action": "comment", "target": TARGET, "message": "x",
                                                "relays_user_message": True}, session_id="chat-1")
    assert sent == []


def test_model_cannot_forge_the_proof(relay):
    sent, _home = relay
    json.loads(sm.handle_task({"action": "comment", "target": TARGET, "message": "x",
                               "relayed_human": {"session": "chat-1", "message_id": 1, "at": 1}}, session_id="chat-1"))
    assert "relayed_human" not in sent[0]["args"]
