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


def _local_db(tmp_path, n_done=0, running=True):
    db = tmp_path / "kanban.db"
    c = sqlite3.connect(db)
    c.executescript("""create table tasks(id text, title text, status text, assignee text, created_at real,
        completed_at real, result text, session_id text, block_kind text, human_request text);
        create table kanban_notify_subs(task_id text, platform text, chat_id text);""")
    if running:
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


class _Ctx:
    def get_config(self, key, default=None):
        return default

    def register_hook(self, name, fn):
        pass

    def on_unload(self, fn):
        pass


def _slow_first_core(monkeypatch, rows, first_s=2.5):
    """A cold process: the first session_tasks call takes ``first_s`` (2.53 s live on the MacBook), later ones are
    instant. Returns the list of each call's session ids."""
    calls = []
    mod = types.ModuleType(ACTIVITY)

    def session_tasks(session_ids, statuses=(), *, stale_ok=False):
        calls.append(list(session_ids))
        if len(calls) == 1:
            time.sleep(first_s)
        return [dict(r) for r in rows if r["session"] in session_ids]
    mod.session_tasks = session_tasks
    monkeypatch.setitem(sys.modules, ACTIVITY, mod)
    return calls


def _wait_idle(gate, limit=6.0):
    deadline = time.monotonic() + limit
    while gate.core._remote_busy.locked() and time.monotonic() < deadline:
        time.sleep(0.01)


def test_cold_first_read_without_warmup_times_out(tmp_path, gate, monkeypatch):
    """The bug: a fresh process's first turn pays the cold read itself and falls back to local cards only."""
    _boards(gate, monkeypatch, _local_db(tmp_path))
    _slow_first_core(monkeypatch, [_remote_row()])
    try:
        assert gate.core.linked_state("chat", limit=50)[1] is False
    finally:
        _wait_idle(gate)


def test_register_warms_projection_so_first_turn_is_complete(tmp_path, gate, monkeypatch):
    """With the register-time join off, the warm-up runs in the background; a turn arriving while it is still in
    flight waits it out within its own 2 s budget and then reads warm."""
    monkeypatch.delenv("ASK_GATE_NO_PREWARM", raising=False)
    monkeypatch.setenv("ASK_GATE_WARM_JOIN_S", "0")
    _boards(gate, monkeypatch, _local_db(tmp_path))
    calls = _slow_first_core(monkeypatch, [_remote_row()])
    gate.register(_Ctx())
    try:
        time.sleep(1.0)  # the gateway's first turn lands mid warm-up (the cold read takes 2.5 s)
        assert gate.core._remote_busy.locked() and calls == [[gate.core.WARM_SESSION]]
        t0 = time.monotonic()
        cards, complete = gate.core.linked_state("chat", limit=50)
        took = time.monotonic() - t0
        assert complete is True and REMOTE in [x["id"] for x in cards]
        assert took <= gate.core.REMOTE_TIMEOUT_S + 0.25  # the turn bound holds
        assert calls[1][0] == "chat"
    finally:
        _wait_idle(gate)


def test_turn_right_after_register_is_complete(tmp_path, gate, monkeypatch):
    """Live on the MacBook (9d087d01): a fresh process whose first turn came right after register still timed out,
    because the 2.5 s cold read outlived the 2 s turn budget. register now pays it at load (bounded join)."""
    monkeypatch.delenv("ASK_GATE_NO_PREWARM", raising=False)
    monkeypatch.delenv("ASK_GATE_WARM_JOIN_S", raising=False)
    _boards(gate, monkeypatch, _local_db(tmp_path))
    calls = _slow_first_core(monkeypatch, [_remote_row()])
    try:
        gate.register(_Ctx())
        t0 = time.monotonic()
        cards, complete = gate.core.linked_state("chat", limit=50)
        assert complete is True and REMOTE in [x["id"] for x in cards]
        assert time.monotonic() - t0 < 0.5 and calls[0] == [gate.core.WARM_SESSION]
    finally:
        _wait_idle(gate)


def test_register_join_is_bounded_when_warmup_hangs(tmp_path, gate, monkeypatch):
    monkeypatch.delenv("ASK_GATE_NO_PREWARM", raising=False)
    monkeypatch.setenv("ASK_GATE_WARM_JOIN_S", "0.3")
    _boards(gate, monkeypatch, _local_db(tmp_path))
    _slow_first_core(monkeypatch, [_remote_row()], first_s=1.5)
    try:
        t0 = time.monotonic()
        gate.register(_Ctx())
        assert time.monotonic() - t0 < 1.0 and gate.core._remote_busy.locked()
    finally:
        _wait_idle(gate)


def test_turn_after_warmup_reads_warm_and_fast(tmp_path, gate, monkeypatch):
    monkeypatch.delenv("ASK_GATE_NO_PREWARM", raising=False)
    _boards(gate, monkeypatch, _local_db(tmp_path))
    _slow_first_core(monkeypatch, [_remote_row()])
    gate._warm_projection()  # what register() starts
    time.sleep(0.05)
    _wait_idle(gate)
    t0 = time.monotonic()
    assert gate.core.linked_state("chat", limit=50)[1] is True and time.monotonic() - t0 < 0.5
    log = (tmp_path / "logs" / "ask-gate.jsonl").read_text()
    assert '"linked_remote_warm"' in log and '"ok": true' in log


def test_warmup_is_off_in_workers(tmp_path, gate, monkeypatch):
    monkeypatch.delenv("ASK_GATE_NO_PREWARM", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_01a1d4dbf509af73")
    calls = _slow_first_core(monkeypatch, [], first_s=0)
    gate._warm_projection()
    time.sleep(0.1)
    assert calls == []


def test_turn_waits_out_a_running_read_within_its_budget(tmp_path, gate, monkeypatch):
    """'previous read still running' is no longer an instant fallback: the turn waits for the in-flight read for
    the rest of its budget, then reads itself."""
    _boards(gate, monkeypatch, _local_db(tmp_path))
    _slow_first_core(monkeypatch, [_remote_row()], first_s=0.6)
    monkeypatch.setattr(gate.core, "REMOTE_TIMEOUT_S", 0.3)
    assert gate.core.linked_state("chat", limit=50)[1] is False  # 0.6 s cold read > 0.3 s budget
    monkeypatch.setattr(gate.core, "REMOTE_TIMEOUT_S", 2.0)
    assert gate.core.linked_state("chat", limit=50)[1] is True  # waits ~0.3 s for it, then reads warm


def _open_remote(n, created0=100.0):
    return [_remote_row(created=created0 + i, tid=f"t_0b0e{i:012x}") for i in range(n)]


def test_open_cards_rank_ahead_of_done_and_overflow_is_named(tmp_path, gate, monkeypatch):
    """33 open remote cards + 10 newer done local rows: the 25-card list holds open cards only (the newest 25),
    and the block says how many open cards it cut and where to list them all."""
    _boards(gate, monkeypatch, _local_db(tmp_path, n_done=10, running=False))
    rows = _open_remote(33)
    _fake_core(monkeypatch, rows)
    cards, complete = gate.core.linked_state("chat", limit=50)
    cap = gate.core.LINKED_CAP
    assert complete and len(cards) == cap
    assert not [x for x in cards if x["status"] == "done"]
    assert {x["id"] for x in cards} == {r["id"] for r in rows[-cap:]}  # the oldest open ones are the cut
    assert cards.more_open == 33 - cap
    out = gate.core.l1_block("chat", None)
    assert f"{33 - cap} more open cards: run `hermes kanban chat-tasks chat`" in out
    assert "finished" not in out


def test_open_cards_beyond_limit_fill_to_cap_and_pins_survive(tmp_path, gate, monkeypatch):
    """The turn's block reads with the default limit (15): open cards still fill to LINKED_CAP, an old card blocked
    on David is kept, and done rows only fill what the open ones leave of the limit window."""
    _boards(gate, monkeypatch, _local_db(tmp_path, n_done=20))
    _fake_core(monkeypatch, _open_remote(30, created0=2000.0) + [_remote_row("blocked", human=True, created=1.0)])
    cards, _ = gate.core.linked_state("chat")
    cap = gate.core.LINKED_CAP
    assert REMOTE in [x["id"] for x in cards] and len(cards) == cap
    assert not [x for x in cards if x["status"] == "done"]
    assert cards.more_open == 31 - (cap - 1)  # 30 remote + the local running card; one slot is the pin
    _fake_core(monkeypatch, _open_remote(3, created0=2000.0))
    cards, _ = gate.core.linked_state("chat")
    assert sum(x["status"] == "done" for x in cards) == 15 - 4 and cards.more_open == 0
    assert "more open cards" not in gate.core.l1_block("chat", None)
