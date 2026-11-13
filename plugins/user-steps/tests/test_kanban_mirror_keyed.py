"""Change-keyed reconcile (design t_01a1d563324d7e8e card L): an unchanged input key skips the pass; every input
change (board, store, sessions), a delete, a replaced file and the FULL_EVERY_S ceiling run it in full."""

import shutil
import sqlite3
import sys
import types

import pytest

from test_kanban_mirror import fixture, decide, OWNER, core


@pytest.fixture
def keyed(tmp_path, monkeypatch):
    """The fixture's mirror with the production resolvers (only those are keyed) over a real sessions table."""
    state = tmp_path / "state.db"
    with sqlite3.connect(state) as c:
        c.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, parent_session_id TEXT, end_reason TEXT, "
                  "source TEXT, started_at REAL)")
        c.executemany("INSERT INTO sessions VALUES (?, NULL, NULL, 'desktop', 0)", [("session-a",), ("session-b",)])
    monkeypatch.setattr(core, "_state_db", lambda: state)
    chain = types.SimpleNamespace(_CHAIN_STEP_SQL="SELECT id FROM sessions WHERE id=?",
                                  _walk_compression_chain=lambda sid, step: [sid])
    monkeypatch.setitem(sys.modules, "hermes_state_compression", chain)
    mirror, store, path, hr = fixture(tmp_path)
    mirror.session_exists = mirror._user_session
    mirror.session_present = mirror._stored_session
    mirror.tip = mirror._tip
    mod = type(mirror).reconcile.__globals__  # the module loaded under a test name, not in sys.modules
    passes = []
    full = mirror._reconcile
    monkeypatch.setattr(mirror, "_reconcile", lambda owner, fenced: passes.append(1) or full(owner, fenced))
    return types.SimpleNamespace(mirror=mirror, store=store, path=path, state=state, mod=mod, passes=passes)


def settle(k):
    """Reconcile until a pass is skipped (a pass that wrote the store runs once more); return the full passes."""
    for _ in range(4):
        before = len(k.passes)
        k.mirror.reconcile(OWNER)
        if len(k.passes) == before:
            return before
    raise AssertionError("never settled")


def runs(k):
    before = len(k.passes)
    k.mirror.reconcile(OWNER)
    return len(k.passes) > before


def board(k, sql, *args):
    with sqlite3.connect(k.path) as c:
        c.execute(sql, args)


def test_unchanged_inputs_skip_and_unrelated_board_churn_still_skips(keyed):
    k = keyed
    settle(k)
    assert len(k.store.requests(OWNER)) == 1
    assert not runs(k)
    board(k, "INSERT INTO tasks VALUES ('t_other','x','running','session-a',NULL,NULL)")
    board(k, "INSERT INTO task_events(task_id,kind) VALUES ('t_other','heartbeat')")
    assert not runs(k)  # the board committed, but no row a pass reads moved


def test_board_change_is_picked_up(keyed):
    k = keyed
    settle(k)
    board(k, "UPDATE tasks SET status='ready'")
    assert runs(k)
    assert k.store.requests(OWNER) == []


def test_new_lifecycle_event_is_picked_up(keyed):
    k = keyed
    settle(k)
    first = k.store.requests(OWNER)[0]["requestId"]
    board(k, "INSERT INTO task_events(id,task_id,kind) VALUES (2,'t_test','unblocked')")
    board(k, "INSERT INTO task_events(id,task_id,kind) VALUES (3,'t_test','blocked')")
    assert runs(k)
    assert [r["requestId"] for r in k.store.requests(OWNER)] != [first]


def test_store_change_is_picked_up(keyed):
    k = keyed
    settle(k)
    decide(k.store, k.store.requests(OWNER)[0])
    assert runs(k)


def test_new_session_is_picked_up(keyed):
    k = keyed
    settle(k)
    with sqlite3.connect(k.state) as c:
        c.execute("INSERT INTO sessions VALUES ('session-c', NULL, NULL, 'desktop', 0)")
    assert runs(k)


def test_deleted_task_falls_back_to_a_full_pass(keyed):
    k = keyed
    settle(k)
    board(k, "DELETE FROM tasks")
    assert runs(k)
    assert k.store.requests(OWNER) == []


def test_deleted_session_falls_back_to_a_full_pass(keyed):
    k = keyed
    settle(k)
    with sqlite3.connect(k.state) as c:
        c.execute("DELETE FROM sessions WHERE id='session-a'")
    assert runs(k)
    assert k.store.requests(OWNER) == []  # its owner's session is gone: the ask is withdrawn


def test_board_rewound_to_an_older_file_falls_back(keyed, tmp_path):
    k = keyed
    shutil.copy(k.path, tmp_path / "older.db")  # the ask as it is now
    settle(k)
    board(k, "UPDATE tasks SET status='ready'")
    settle(k)
    assert k.store.requests(OWNER) == []
    (tmp_path / "older.db").replace(k.path)  # restore a backup: a new inode, the ask is blocked again
    assert runs(k)
    assert len(k.store.requests(OWNER)) == 1


def test_fallback_ceiling_runs_a_full_pass(keyed, monkeypatch):
    k = keyed
    settle(k)
    assert not runs(k)
    monkeypatch.setitem(k.mod, "FULL_EVERY_S", 0.0)
    assert runs(k) and runs(k)


def test_a_deferred_pass_is_not_memoized(keyed, monkeypatch):
    k = keyed
    settle(k)
    real = k.mirror._reconcile_binding
    monkeypatch.setattr(k.mirror, "_reconcile_binding", lambda *a: (_ for _ in ()).throw(OSError("busy")))
    monkeypatch.setitem(k.mod, "FULL_EVERY_S", 0.0)  # force one full pass on an unchanged key
    assert runs(k)
    monkeypatch.setitem(k.mod, "FULL_EVERY_S", 60.0)
    monkeypatch.setattr(k.mirror, "_reconcile_binding", real)
    assert runs(k)  # the deferred binding is retried although no input moved since
    assert not runs(k)


def test_receipt_on_a_bound_card_that_left_blocked_moves_the_key(keyed):
    k = keyed
    settle(k)
    board(k, "UPDATE tasks SET status='ready'")
    settle(k)
    before = k.mirror._key(OWNER, True)
    board(k, "INSERT INTO task_events(task_id,kind,payload) VALUES ('t_test','archived','{}')")  # _receipt reads it
    assert k.mirror._key(OWNER, True) != before


def test_injected_resolver_is_never_skipped(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)  # session_exists and tip injected: no key covers them
    mod = type(mirror).reconcile.__globals__  # the module loaded under a test name, not in sys.modules
    assert mirror._key(OWNER, True) is None
    calls = []
    full = mirror._reconcile
    mirror._reconcile = lambda owner, fenced: calls.append(1) or full(owner, fenced)
    for _ in range(3):
        mirror.reconcile(OWNER)
    assert len(calls) == 3 and mod["_MEMO"] == {}


def test_connections_are_kept_across_passes(keyed, monkeypatch):
    k = keyed
    opened = []
    real = k.mod["_open_ro"]
    monkeypatch.setitem(k.mod, "_open_ro", lambda p: opened.append(str(p)) or real(p))
    settle(k)
    board(k, "UPDATE tasks SET title='changed'")
    for _ in range(5):
        k.mirror.reconcile(OWNER)
    assert sorted(set(opened)) == sorted(opened)  # one open per file
    assert set(opened) <= {str(k.path), str(k.state), str(k.store.path)}
