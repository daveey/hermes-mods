"""A watch park is not the chat's open work, as core counts it. [t_01a1d556ac92f0d7]

``kanban_schedule(watch=true)`` parks a card ``scheduled`` with ``watch: true`` on the park event. Core's
``session_tasks`` (``hermes kanban chat-tasks``) and the chat counts leave it out (``hermes_cli.kanban_watch``).
ask-gate's live list counted every ``scheduled`` card as open, so it printed "18 more open cards" where chat-tasks
listed 17 beyond the cap. The watch stays listed, marked, so the agent names the pending check.

The core-backed tests build a real board with core's own writers and compare against core's ``session_tasks``
read; without core (public CI) they skip via conftest. The fallback test needs no core.
"""
import json
import sqlite3
import sys
import time

import pytest

CHAT = "20261026_235722_bef6c6"
WATCH_MARK = "(watch: a pending check"


@pytest.fixture
def board(gate, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_PROFILE", "default")
    for var in ("HERMES_SESSION_ID", "HERMES_KANBAN_RUN_ID", "HERMES_SESSION_KEY"):
        monkeypatch.delenv(var, raising=False)
    import os
    for var in [k for k in os.environ if k.startswith("HERMES_KANBAN_")]:  # a worker's pins name the LIVE board
        monkeypatch.delenv(var, raising=False)
    from pathlib import Path as _Path
    monkeypatch.setattr(_Path, "home", lambda: tmp_path)
    from hermes_cli import kanban_db as kb
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return kb


def _card(kb, title, created):
    """A card the chat created, with its 'create' tui sub (what kanban_create writes)."""
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_notify as kbn
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title=title, assignee="default", session_id=CHAT)
        kbn.add_notify_sub(conn, task_id=tid, platform="tui", chat_id=CHAT, delivery_metadata={"reason": "create"})
        conn.execute("update tasks set created_at = ? where id = ?", (created, tid))
        conn.commit()
    return tid


def _schedule(tid, **extra):
    from tools import kanban_tools as kt
    wake = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 72 * 3600))
    out = json.loads(kt._handle_schedule({
        "task_id": tid, "reason": f"SCHEDULED_UNTIL={wake} re-check the deploy", "clock_only": True,
        "awaiting": {"kind": "clock", "ref": "deploy soak"}, **extra}))
    assert out.get("status") == "scheduled", out
    return out


def _core_open(kb, monkeypatch):
    """Core's own answer: the chat's open cards (the close-out gate / chat-tasks read)."""
    monkeypatch.delitem(sys.modules, "hermes_cli.kanban_session_activity", raising=False)  # conftest's stub
    from hermes_cli import kanban_session_activity as ksa
    return {r["id"] for r in ksa._read_session_tasks(kb.kanban_db_path(), [CHAT], ksa.ACTIVE_STATUSES)}


def _fill(gate, kb, n):
    return [_card(kb, f"open card {i}", 1000.0 + i) for i in range(n)]


def test_watch_park_is_not_open_work_but_stays_listed(gate, board, monkeypatch):
    n = gate.core.LINKED_CAP + 2  # past the cap, so more_open is printed
    opens = _fill(gate, board, n)
    watch = _card(board, "re-check the deploy after the soak", 5000.0)  # newest: ranks first if counted open
    assert _schedule(watch, watch=True).get("watch") is True
    core_open = _core_open(board, monkeypatch)
    assert core_open == set(opens), "core leaves the watch park out of the chat's open cards"

    db = board.kanban_db_path()
    cards, complete = gate.core.linked_state(CHAT, db_paths=[db])
    assert complete
    listed = {x["id"]: x for x in cards}
    open_listed = [x for x in cards if gate.core._is_open(x)]
    assert len(open_listed) + cards.more_open == len(core_open) == n
    assert cards.more_open == n - gate.core.LINKED_CAP
    assert {x["id"] for x in open_listed} <= core_open
    # The watch is still listed and marked, and is no ask for David.
    assert watch in listed and listed[watch]["watch"] is True and not listed[watch]["pinned"]
    assert not gate.core._is_open(listed[watch])
    block = gate.core.l1_block(CHAT, None, db_paths=[db])
    line = next(x for x in block.splitlines() if watch in x)
    assert "[scheduled]" in line and WATCH_MARK in line and "do not say everything is finished" in line
    assert f"{n - gate.core.LINKED_CAP} more open cards" in block
    assert not gate.core.waiting_on_david(cards)


def test_watch_park_with_human_request_is_never_pinned(gate, board, monkeypatch):
    """A watch park that still carries a human_request is no pinned ask: core ANDs the not-watch predicate over the
    whole open-work expression, so pinning it would take a LINKED_CAP slot and put ``more_open`` one off
    chat-tasks (review 5893091490)."""
    n = gate.core.LINKED_CAP + 2
    opens = _fill(gate, board, n)
    watch = _card(board, "re-check the deploy after the soak", 500.0)  # oldest: outside any recency window
    assert _schedule(watch, watch=True).get("watch") is True
    db = board.kanban_db_path()
    hr = {"action": "Approve the deploy in this chat. Unblock = ship; archive = drop.", "why_human": "approval",
          "interaction": "approval", "scope": "deploy", "evidence": "soak log"}
    c = sqlite3.connect(db)
    c.execute("update tasks set human_request = ? where id = ?", (json.dumps(hr), watch))
    c.commit()
    c.close()
    core_open = _core_open(board, monkeypatch)
    assert core_open == set(opens), "core leaves a watch park out of open work, human_request or not"

    cards, complete = gate.core.linked_state(CHAT, db_paths=[db])
    assert complete
    listed = {x["id"]: x for x in cards}
    assert watch in listed and listed[watch]["watch"] is True and not listed[watch]["pinned"]
    assert listed[watch]["human_request"]  # read, but not a pin
    assert watch not in {x["id"] for x in gate.core.waiting_on_david(cards)}
    open_listed = [x for x in cards if gate.core._is_open(x)]
    assert len(open_listed) == gate.core.LINKED_CAP
    assert cards.more_open == len(core_open) - len(open_listed) == n - gate.core.LINKED_CAP
    block = gate.core.l1_block(CHAT, None, db_paths=[db])
    line = next(x for x in block.splitlines() if watch in x)
    assert WATCH_MARK in line
    assert f"{n - gate.core.LINKED_CAP} more open cards" in block


def test_plain_scheduled_card_still_counts_as_open(gate, board, monkeypatch):
    n = gate.core.LINKED_CAP + 2
    opens = _fill(gate, board, n)
    parked = _card(board, "plain park", 5000.0)
    assert not _schedule(parked).get("watch")
    core_open = _core_open(board, monkeypatch)
    assert core_open == set(opens) | {parked}
    cards, _ = gate.core.linked_state(CHAT, db_paths=[board.kanban_db_path()])
    open_listed = [x for x in cards if gate.core._is_open(x)]
    assert len(open_listed) + cards.more_open == len(core_open) == n + 1
    row = next(x for x in cards if x["id"] == parked)
    assert row["watch"] is False and gate.core._is_open(row)
    assert WATCH_MARK not in gate.core.render_cards(cards, replica=lambda gw: None)


def test_no_watch_predicate_means_no_watches(gate, tmp_path, monkeypatch):
    """An older core (no ``hermes_cli.kanban_watch``): every scheduled card counts, the read still works."""
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_watch", None)  # import raises ImportError
    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    c.executescript("""create table tasks(id text, title text, status text, assignee text, created_at real,
        completed_at real, result text, session_id text, block_kind text, human_request text);
        create table kanban_notify_subs(task_id text, platform text, chat_id text);""")
    c.execute("insert into tasks values (?,?,?,?,?,?,?,?,?,?)",
              ("t_01a1d0a6b9097a68", "parked", "scheduled", "default", 1.0, None, "", CHAT, None, None))
    c.commit()
    c.close()
    cards, _ = gate.core.linked_state(CHAT, db_paths=[db])
    assert [x["id"] for x in cards] == ["t_01a1d0a6b9097a68"]
    assert cards[0]["watch"] is False and gate.core._is_open(cards[0])
