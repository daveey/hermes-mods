"""The live list read only this gateway's board files, so a card the chat placed on another gateway (placement=auto
sends most new cards to the hub or the pool) never showed in "Cards linked to this chat", and a remote card blocked
on David never reached "Waiting on David now". Core's chat projection (``kanban_session_activity.session_tasks``,
what ``hermes kanban chat-tasks`` and the Desktop task panel read) lists them; the ask-gate merges it, and says
"local cards only" when it cannot read it."""
import sqlite3
import sys
import threading
import time
import types

ACTIVITY = "hermes_cli.kanban_session_activity"
REMOTE = "t_01a1d4920ce3cbf4"
ASK = "Reply A or B on this card: which host runs the repair. Unblock = run it there; archive = drop it."


def _local_db(tmp_path, n_done=0):
    db = tmp_path / "kanban.db"
    c = sqlite3.connect(db)
    c.executescript("""create table tasks(id text, title text, status text, assignee text, created_at real,
        completed_at real, result text, session_id text, block_kind text, human_request text);
        create table kanban_notify_subs(task_id text, platform text, chat_id text);""")
    c.execute("insert into tasks values (?,?,?,?,?,?,?,?,?,?)",
              ("t_01a1d4765254ee52", "local card", "running", "default", 500.0, None, "", "chat", None, None))
    for i in range(n_done):
        c.execute("insert into tasks values (?,?,?,?,?,?,?,?,?,?)",
                  (f"t_done{i:012x}", f"finished {i}", "done", "default", 1000.0 + i, 2000.0 + i, "", "chat",
                   None, None))
    c.commit()
    c.close()
    return db


def _remote_row(status="running", human=False, created=100.0, tid=REMOTE):
    return {"id": tid, "title": "remote card on the hub", "status": status, "assignee": "default",
            "created_at": created, "started_at": None, "priority": 0,
            "block_kind": "needs_input" if human else None,
            "human_request": {"action": ASK, "why_human": "only David knows"} if human else None,
            "board": "default", "gateway": "hub", "key": f"hub/default/default/{tid}", "session": "chat",
            "session_id": "chat", "origin_session": "chat", "via": "origin", "remote": True, "stale": False}


def _fake_core(monkeypatch, rows, calls=None):
    mod = types.ModuleType(ACTIVITY)
    mod.LISTED_STATUSES = ("todo", "ready", "running", "scheduled", "review", "blocked", "triage")

    def session_tasks(session_ids, statuses=mod.LISTED_STATUSES, *, stale_ok=False):
        if calls is not None:
            calls.append((list(session_ids), tuple(statuses), stale_ok))
        return [dict(r) for r in rows if r["session"] in session_ids]
    mod.session_tasks = session_tasks
    monkeypatch.setitem(sys.modules, ACTIVITY, mod)


def _boards(gate, monkeypatch, db):
    monkeypatch.setattr(gate.core, "boards", lambda: [db])


def test_remote_card_of_the_chat_is_listed(tmp_path, gate, monkeypatch):
    _boards(gate, monkeypatch, _local_db(tmp_path))
    calls = []
    _fake_core(monkeypatch, [_remote_row()], calls)
    cards = gate.core.linked_cards("chat", limit=50)
    ids = [x["id"] for x in cards]
    assert REMOTE in ids and "t_01a1d4765254ee52" in ids
    assert calls and calls[0][0][0] == "chat" and calls[0][2] is True  # bounded: the stale-ok projection
    out = gate.core.l1_block("chat", None)
    assert f"hub/default/default/{REMOTE} [running]" in out  # owner-qualified, so nobody looks for it locally
    assert "local cards only" not in out


def test_remote_card_blocked_on_david_is_waiting_and_pinned(tmp_path, gate, monkeypatch):
    _boards(gate, monkeypatch, _local_db(tmp_path, n_done=40))
    _fake_core(monkeypatch, [_remote_row("blocked", human=True, created=1.0)])  # oldest card of the chat
    cards = gate.core.linked_cards("chat", limit=50)
    assert len(cards) <= gate.core.LINKED_CAP + 1
    hit = next(x for x in cards if x["id"] == REMOTE)
    assert hit["pinned"]
    assert [x["id"] for x in gate.core.waiting_on_david(cards)] == [REMOTE]
    out = gate.core.l1_block("chat", None)
    sec = out.split("Waiting on David now:")[1]
    assert REMOTE in sec and "Reply A or B on this card" in sec


def test_remote_duplicate_of_a_local_card_is_listed_once(tmp_path, gate, monkeypatch):
    _boards(gate, monkeypatch, _local_db(tmp_path))
    _fake_core(monkeypatch, [_remote_row(tid="t_01a1d4765254ee52")])
    cards = gate.core.linked_cards("chat", limit=50)
    assert [x["id"] for x in cards].count("t_01a1d4765254ee52") == 1


def test_core_import_failure_falls_back_to_local_rows_with_marker(tmp_path, gate, monkeypatch):
    db = _local_db(tmp_path, n_done=3)
    _boards(gate, monkeypatch, db)
    local = gate.core.linked_cards("chat", limit=50, db_paths=[db])  # today's read: explicit boards only
    monkeypatch.setitem(sys.modules, ACTIVITY, None)  # import fails
    assert gate.core.linked_cards("chat", limit=50) == local
    out = gate.core.l1_block("chat", None)
    assert "local cards only" in out


def test_core_without_session_tasks_or_raising_falls_back(tmp_path, gate, monkeypatch):
    db = _local_db(tmp_path)
    _boards(gate, monkeypatch, db)
    monkeypatch.setitem(sys.modules, ACTIVITY, types.ModuleType(ACTIVITY))  # an older core
    assert "local cards only" in gate.core.l1_block("chat", None)
    mod = types.ModuleType(ACTIVITY)

    def boom(*a, **k):
        raise RuntimeError("replica unreadable")
    mod.session_tasks = boom
    monkeypatch.setitem(sys.modules, ACTIVITY, mod)
    cards, complete = gate.core.linked_state("chat", limit=50)
    assert not complete and [x["id"] for x in cards] == ["t_01a1d4765254ee52"]


def test_hung_projection_is_bounded(tmp_path, gate, monkeypatch):
    _boards(gate, monkeypatch, _local_db(tmp_path))
    release = threading.Event()
    mod = types.ModuleType(ACTIVITY)
    mod.session_tasks = lambda *a, **k: release.wait(10) and []
    monkeypatch.setitem(sys.modules, ACTIVITY, mod)
    monkeypatch.setattr(gate.core, "REMOTE_TIMEOUT_S", 0.2)
    try:
        t0 = time.monotonic()
        cards, complete = gate.core.linked_state("chat", limit=50)
        assert time.monotonic() - t0 < 2 and not complete and [x["id"] for x in cards] == ["t_01a1d4765254ee52"]
        t0 = time.monotonic()  # the hung read is still running: the next turn does not stack another
        assert gate.core.linked_state("chat", limit=50)[1] is False and time.monotonic() - t0 < 1
    finally:
        release.set()
    deadline = time.monotonic() + 5  # once the read returns, the next turn reads the projection again
    while gate.core._remote_busy.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert gate.core.linked_state("chat", limit=50)[1] is True
