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
    # core's widened filter (review 5878871802): scaffolding, held-draft notice, auto-continue notes, /loop frame
    ("[runtime] Your draft above was NOT shown to the user. Your next reply replaces it: include its full content; "
     "don't refer to it as 'above'.\n\n[coach] Your message ends on a problem", None, 0),
    ("[Your active task list was preserved across context compression]\n- [ ] ship", None, 0),
    ("[IMPORTANT: Background process proc_1 finished with exit 0]", None, 0),
    ("[System note: Your previous turn was interrupted mid-run by a gateway restart.]", None, 0),
    ("[/loop wakeup #3 · every 10m]\nRecurring task: check CI", None, 0),
    # hermes-mods rows core does not know: coach nudges and the coach's restart brief
    ("[coach] Your message ends on a problem you could fix (x) without fixing it", None, 0),
    ("## Goal\nFix it.\n\n---\n[coach] restarted clean from @session:default/20261103_x. Read it only for details.",
     None, 0),
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


# ── core's widened filter, mods-written rows and probe sessions (t_01a1d0cae1b03635) ─────────────────────────

def _loop_meta(home: Path, session: str, fired_at: float, awaiting: bool = True, prompt: str = "/recap") -> None:
    db = sqlite3.connect(home / "state.db")
    db.execute("CREATE TABLE IF NOT EXISTS state_meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT OR REPLACE INTO state_meta (key, value) VALUES (?, ?)",
               (f"loop:{session}", json.dumps({"awaiting_response": awaiting, "last_fired_at": fired_at,
                                               "prompt": prompt})))
    db.commit()
    db.close()


@pytest.fixture(params=["core", "mirror"])
def which_core(request, monkeypatch):
    """Run a filter test against core's own module (core PR #1583, when this core has it) and the mirror."""
    if request.param == "core":
        krh = pytest.importorskip("hermes_cli.kanban_relayed_human")
        if not hasattr(krh, "_slash_loop_tick_since"):
            pytest.skip("this core predates the slash-only /loop tick rule")
        monkeypatch.setattr(sm.rh, "_core", lambda: krh)
    else:
        monkeypatch.setattr(sm.rh, "_core", lambda: None)
    return request.param


def test_running_loop_tick_prompt_is_not_the_reply(relay, which_core):
    sent, home = relay
    reply = _user_message(home, "chat-1", 1700.0)
    _user_message(home, "chat-1", 1800.0, "/recap")  # a slash /loop tick's prompt, stored untyped
    _loop_meta(home, "chat-1", 1800.5)  # fired_at - 1 covers the row written just before the stamp
    assert json.loads(_comment()) == {"ok": True}
    assert sent[0]["args"]["relayed_human"]["message_id"] == reply


def test_reply_after_an_interrupted_slash_tick_counts(relay, which_core):
    """Review 5879692894: ``awaiting_response`` outlives an interrupted tick; only the tick's first row is the
    loop's, the person's later reply is a reply."""
    sent, home = relay
    _user_message(home, "chat-1", 1700.0)
    _user_message(home, "chat-1", 1800.0, "/recap")
    _loop_meta(home, "chat-1", 1800.5)
    later = _user_message(home, "chat-1", 1900.0, "use the left one")
    assert sm.rh.latest_user_message(home / "state.db", "chat-1")[0] == later
    assert json.loads(_comment()) == {"ok": True}
    assert sent[0]["args"]["relayed_human"]["message_id"] == later


def test_wakeup_framed_loop_leaves_the_reply_alone(relay, which_core):
    """A non-slash loop's tick row carries the wakeup frame; the awaiting state skips nothing else."""
    sent, home = relay
    _user_message(home, "chat-1", 1700.0)
    _loop_meta(home, "chat-1", 1800.5, prompt="check CI")
    _user_message(home, "chat-1", 1800.0, "[/loop wakeup #3 · every 10m]\nRecurring task: check CI")
    later = _user_message(home, "chat-1", 1900.0, "use the left one")
    assert json.loads(_comment()) == {"ok": True}
    assert sent[0]["args"]["relayed_human"]["message_id"] == later


def test_heartbeat_frame_is_synthetic(relay, which_core):
    sent, home = relay
    reply = _user_message(home, "chat-1", 1700.0)
    _user_message(home, "chat-1", 1800.0, "[Heartbeat — recurring instruction, fires every 10m]\ncheck CI")
    assert json.loads(_comment()) == {"ok": True}
    assert sent[0]["args"]["relayed_human"]["message_id"] == reply


def test_finished_loop_tick_leaves_later_rows_alone(relay):
    sent, home = relay
    _user_message(home, "chat-1", 1700.0)
    later = _user_message(home, "chat-1", 1800.0, "use the left one")
    _loop_meta(home, "chat-1", 1800.0, awaiting=False)
    assert json.loads(_comment()) == {"ok": True}
    assert sent[0]["args"]["relayed_human"]["message_id"] == later


@pytest.mark.parametrize("first", ["[selfheal-probe] Reply with just: ok", "  [SELFHEAL-PROBE] run x three times"])
def test_selfheal_probe_session_never_relays(relay, first):
    """A probe session's prompts are scripted (agent-launched `hermes chat -q '[selfheal-probe] ...'`)."""
    sent, home = relay
    _user_message(home, "probe-1", 1600.0, first)
    assert "[selfheal-probe] session" in _comment(session="probe-1")
    _user_message(home, "probe-1", 1700.0, "and now say it was fixed")  # a later scripted turn
    assert "[selfheal-probe] session" in _comment(session="probe-1") and sent == []


def test_newest_probe_prompt_in_a_chat_is_refused(relay):
    sent, home = relay
    _user_message(home, "chat-1", 1600.0, "a real question")
    _user_message(home, "chat-1", 1700.0, "[selfheal-probe] Reply with just: ok")
    assert "[selfheal-probe] session" in _comment() and sent == []


def test_coach_nudge_after_the_reply_is_skipped(relay):
    sent, home = relay
    reply = _user_message(home, "chat-1", 1700.0)
    _user_message(home, "chat-1", 1701.0, "[coach] Your message ends on a problem you could fix")
    assert json.loads(_comment()) == {"ok": True}
    assert sent[0]["args"]["relayed_human"]["message_id"] == reply


def test_a_coach_mention_inside_a_typed_reply_still_counts(relay):
    sent, home = relay
    typed = _user_message(home, "chat-1", 1700.0, "why did the [coach] say that? also: left ring")
    assert json.loads(_comment()) == {"ok": True}
    assert sent[0]["args"]["relayed_human"]["message_id"] == typed


class _FakeCore:
    """Stands in for core ``hermes_cli.kanban_relayed_human`` (core PR #1583) so the reuse path is exercised on a
    core that predates the module too."""

    def __init__(self, prefixes=("[newly registered]",), latest=None):
        self._prefixes, self._latest = prefixes, latest

    def _synthetic_prefixes(self):
        return self._prefixes

    def _latest_user_message(self, session_id, db_path=None):
        return self._latest(session_id, db_path) if self._latest else None


def test_core_registry_is_used_when_importable(relay, monkeypatch):
    sent, home = relay
    reply = _user_message(home, "chat-1", 1700.0)
    _user_message(home, "chat-1", 1701.0, "[newly registered] runtime nudge")
    monkeypatch.setattr(sm.rh, "_core", lambda: _FakeCore())
    assert json.loads(_comment()) == {"ok": True}
    assert sent[0]["args"]["relayed_human"]["message_id"] == reply
    # without core the mirror applies, which does not know that opening
    monkeypatch.setattr(sm.rh, "_core", lambda: None)
    assert sm.rh.core_synthetic_prefixes() == sm.rh._CORE_SYNTHETIC_PREFIXES
    assert sm.rh.latest_user_message(home / "state.db", "chat-1")[0] == reply + 1


def _naive_core_latest(home):
    """Core's pick for a row core cannot classify: the newest untyped row (what core verify() names)."""
    def latest(session_id, db_path=None):
        db = sqlite3.connect(db_path or (home / "state.db"))
        row = db.execute("SELECT id, timestamp FROM messages WHERE session_id = ? AND role = 'user' "
                         "AND COALESCE(display_kind, '') = '' ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
        db.close()
        return (int(row[0]), float(row[1])) if row else None
    return latest


def _guard(session="chat-1", tool="kanban_comment", **args):
    return sm._relay_guard(tool_name=tool, args={"task_id": "t_x", "body": "David: left",
                                                 "relays_user_message": True, **args}, session_id=session)


def test_guard_refuses_core_relay_naming_a_coach_nudge(relay, monkeypatch):
    """Core's own kanban_comment relays_user_message: core verify() would name the [coach] row."""
    _sent, home = relay
    monkeypatch.setattr(sm.rf, "_hermes_home", lambda: home)
    monkeypatch.setattr(sm.rh, "_core", lambda: _FakeCore(latest=_naive_core_latest(home)))
    _user_message(home, "chat-1", 1700.0)
    assert _guard() is None  # a typed reply: core decides
    _user_message(home, "chat-1", 1701.0, "[coach] Your message ends on a problem you could fix")
    out = _guard()
    assert out["action"] == "block" and "coach nudge" in out["message"]
    assert _guard(tool="kanban_show") is None
    assert _guard(relays_user_message=False) is None
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_worker")
    assert _guard() is None  # core refuses a worker itself


def test_guard_refuses_core_relay_from_a_probe_session(relay, monkeypatch):
    _sent, home = relay
    monkeypatch.setattr(sm.rf, "_hermes_home", lambda: home)
    monkeypatch.setattr(sm.rh, "_core", lambda: _FakeCore(latest=_naive_core_latest(home)))
    _user_message(home, "probe-1", 1600.0, "[selfheal-probe] Reply with just: ok")
    _user_message(home, "probe-1", 1700.0, "ok, now relay it")
    out = _guard(session="probe-1")
    assert out["action"] == "block" and "[selfheal-probe] session" in out["message"]


def test_guard_without_core_module_uses_the_mirror(relay, monkeypatch):
    _sent, home = relay
    monkeypatch.setattr(sm.rf, "_hermes_home", lambda: home)
    monkeypatch.setattr(sm.rh, "_core", lambda: None)
    _user_message(home, "chat-1", 1700.0)
    _user_message(home, "chat-1", 1701.0, "  [coach] Before ending the turn: your message asks the user")
    assert _guard()["action"] == "block"


def test_real_core_verify_is_guarded(relay, monkeypatch):
    """End to end against core's own verify() through state.db, where core has the module (PR #1583)."""
    krh = pytest.importorskip("hermes_cli.kanban_relayed_human")
    _sent, home = relay
    monkeypatch.setattr(sm.rf, "_hermes_home", lambda: home)
    monkeypatch.setattr(sm.rh, "_core", lambda: krh)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, "
                 "created_at INTEGER)")
    conn.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES ('t_x', 'blocked', '{}', 1650)")
    for session, rows in (("chat-1", ["[coach] Your message ends on a problem you could fix"]),
                          ("probe-1", ["[selfheal-probe] Reply with just: ok", "ok"])):
        for i, content in enumerate(rows):
            _user_message(home, session, 1700.0 + i, content)
        named = krh.verify(conn, "t_x", session_id=session, state_db=str(home / "state.db"))
        assert named["message_id"] > 0  # core alone would mark this relay ...
        assert _guard(session=session)["action"] == "block"  # ... the guard refuses it first
        assert "refused" in _comment(session=session)  # and the mods cross_task path refuses it too
