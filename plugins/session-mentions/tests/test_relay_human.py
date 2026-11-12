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


REPLIES: list = []  # queued courier results for the next requests; {"ok": True} when empty


@pytest.fixture
def relay(tmp_path, monkeypatch):
    sent = []
    REPLIES.clear()
    for sub in ("outbox", "claimed", "results"):
        (tmp_path / sub).mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(sm.rf, "root", lambda: tmp_path)
    monkeypatch.setattr(sm.rf, "_hermes_home", lambda: home)
    monkeypatch.setenv("HERMES_HUB_STATE", str(tmp_path / "no-hub"))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setattr(sm, "_sender_info", lambda sid: {"session_id": sid or ""})
    monkeypatch.setattr(sm.bt, "enabled", lambda: False)  # the courier path, whatever this host's config says
    stop = threading.Event()

    def courier():
        while not stop.is_set():
            for f in (tmp_path / "outbox").glob("*.json"):
                req = json.loads(f.read_text())
                f.unlink()
                sent.append(req)
                reply = REPLIES.pop(0) if REPLIES else {"ok": True}
                (tmp_path / "results" / f.name).write_text(json.dumps(reply))
            time.sleep(0.05)

    threading.Thread(target=courier, daemon=True).start()
    yield sent, home
    stop.set()


def _user_message(home: Path, session: str, at: float, content: str = "left", display_kind: str | None = None,
                  compressed: int = 0) -> int:
    db = sqlite3.connect(home / "state.db")
    db.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, "
               "role TEXT, content TEXT, timestamp REAL, display_kind TEXT, _compressed_summary INTEGER DEFAULT 0)")
    cur = db.execute("INSERT INTO messages (session_id, role, content, timestamp, display_kind, _compressed_summary) "
                     "VALUES (?, 'user', ?, ?, ?, ?)", (session, content, at, display_kind, compressed))
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


def _comment(session="chat-1", message="David: left ring"):
    return sm.handle_task({"action": "comment", "target": TARGET, "message": message, "relays_user_message": True},
                          session_id=session)


SYNTHETIC = [  # (content, display_kind, _compressed_summary): role='user' rows nobody typed
    ("[kanban] t_x is blocked: needs_input", "kanban_notice", 0),
    ("scaffold", "hidden", 0),
    ("process finished", "process_complete", 0),
    ("child done", "async_delegation_complete", 0),
    ("note", "internal_notification", 0),
    ("continue", "auto_continue", 0),
    ("summary of earlier turns", None, 1),
    ("  [System: the gateway restarted]", None, 0),
    ("[Message from another Hermes session @session:default@mini/abc]", None, 0),
]


def test_kanban_notice_after_the_reply_is_not_the_reply(relay):
    """Review 5878485674: the card's own block notice lands after the ask; the relay must name the user's reply."""
    sent, home = relay
    reply = _user_message(home, "chat-1", 1700.0)
    for i, (content, kind, compressed) in enumerate(SYNTHETIC):
        _user_message(home, "chat-1", 1701.0 + i, content, kind, compressed)
    assert json.loads(_comment()) == {"ok": True}
    assert sent[0]["args"]["relayed_human"]["message_id"] == reply


def test_only_a_notice_after_an_old_reply_names_the_old_reply(relay):
    sent, home = relay
    old = _user_message(home, "chat-1", 1600.0)
    assert json.loads(_comment()) == {"ok": True}
    _user_message(home, "chat-1", 1700.0, "[kanban] t_x is blocked again", "kanban_notice")
    # No new typed message: the notice cannot stand in for a fresh reply.
    assert "already relayed" in _comment(message="again") and len(sent) == 1
    assert sent[0]["args"]["relayed_human"]["message_id"] == old


def test_steer_counts_and_synthetic_rows_alone_are_no_reply(relay):
    sent, home = relay
    for i, (content, kind, compressed) in enumerate(SYNTHETIC):
        _user_message(home, "chat-1", 1600.0 + i, content, kind, compressed)
    assert "no user message" in _comment() and sent == []
    steer = _user_message(home, "chat-1", 1700.0, "use the left one", "steer")
    assert json.loads(_comment()) == {"ok": True}
    assert sent[0]["args"]["relayed_human"]["message_id"] == steer


def test_failed_delivery_leaves_the_message_relayable(relay):
    sent, home = relay
    mid = _user_message(home, "chat-1", 1700.0)
    REPLIES.append({"ok": False, "error": "502: gateway unreachable"})
    assert json.loads(_comment())["error"] == "502: gateway unreachable"
    assert json.loads(_comment()) == {"ok": True}  # the retry is not refused as "already relayed"
    assert [r["args"]["relayed_human"]["message_id"] for r in sent] == [mid, mid]
    assert "already relayed" in _comment() and len(sent) == 2


def test_card_gateway_refusal_is_a_refusal_not_a_transport_error(relay):
    sent, home = relay
    _user_message(home, "chat-1", 1700.0)
    detail = "relayed_human refused: the newest user message in session chat-1 predates t_x's latest ask"
    REPLIES.append({"ok": False, "error": "400: " + json.dumps({"detail": detail})})
    out = json.loads(_comment())
    assert out["ok"] is False and out["rejection"] == "relayed_human_refused"
    assert "predates t_x's latest ask" in out["error"] and "{" not in out["error"]
    assert json.loads(_comment()) == {"ok": True}  # nothing was recorded for the refused relay
    assert len(sent) == 2
