"""Local half of the owner wake's ``parked_waits`` (REMOTE-GATE.md "parked waits"), on fork core's real board.

Native replay V2: ``parked_waits`` cost 84 ms / 4 calls, most of it a fresh guarded board open per board per pass.
The plugin keeps each board's clock-free parked rows on its retained observer, keyed on ``data_version``; wait ages
(stalled vs waiting) are judged at call time, and a failed read is never kept. Set HERMES_CORE_SOURCE to run.
"""
import importlib
import os
import sqlite3
import time

import pytest
from test_closeout import core, plugin


@pytest.fixture
def board(tmp_path, monkeypatch):
    source = os.environ.get('HERMES_CORE_SOURCE')
    if not source:
        pytest.skip('set HERMES_CORE_SOURCE for core SQLite integration')
    monkeypatch.syspath_prepend(source)
    for key in ('HERMES_KANBAN_TASK', 'HERMES_DELEGATED_CHILD_CONTEXT', 'HERMES_DELEGATED_CHILD_OWNER'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_DB', str(tmp_path / 'kanban.db'))
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'hub'))
    kb = importlib.import_module('hermes_cli.kanban_db')
    ksa = importlib.import_module('hermes_cli.kanban_session_activity')
    connect = importlib.import_module('hermes_cli.kanban_db_connect').connect_closing
    constants = importlib.import_module('hermes_constants')
    monkeypatch.setattr(constants, 'get_hermes_home', lambda: tmp_path)
    monkeypatch.setattr(ksa, '_board_db_paths', lambda: [tmp_path / 'kanban.db'])
    monkeypatch.setattr(ksa, '_hub_db_path', lambda: None)
    ksa._reset_cache_for_tests()
    plugin._gate_reset()
    core._structural_reads.clear()

    def park(title, session):
        with connect() as conn:
            tid = kb.create_task(conn, title=title, session_id=session)
            conn.execute("UPDATE tasks SET status='blocked', block_kind='capability' WHERE id=?", (tid,))
            kb._append_event(conn, tid, 'blocked', {'kind': 'capability', 'reason': f'{title} needs a role'})
            conn.commit()
        return tid

    reads = []
    real_rows = ksa._local_wait_rows

    def counted(conn, ids):
        reads.append(sorted(ids))
        return real_rows(conn, ids)
    monkeypatch.setattr(ksa, '_local_wait_rows', counted)

    def reference(now):
        """Core's own ``parked_waits`` (local only here), uncounted."""
        saved = list(reads)
        try:
            return ksa.parked_waits(now=now)
        finally:
            reads[:] = saved

    park('first', 's_one')
    world = {'ksa': ksa, 'park': park, 'reads': reads, 'reference': reference, 'kb': kb}
    try:
        yield world
    finally:
        plugin._gate_reset()
        core._structural_reads.clear()
        ksa._reset_cache_for_tests()


def _norm(rows):
    return sorted((r['key'], r['session'], r['title'], tuple(sorted(r['wait'].items()))) for r in rows)


def test_unchanged_board_reads_rows_once_and_judges_age_each_call(board):
    ksa, t = board['ksa'], time.time()
    stall = importlib.import_module('hermes_cli.kanban_wait_info').STALL_AFTER_S
    for now in (t, t + 30, t + stall + 60):  # the last pass crosses the stall cut-off with no board change
        got = plugin._parked_waits(ksa, now)
        assert _norm(got) == _norm(board['reference'](now)), now - t
        assert got and got[0]['title'] == 'first'
    assert len(board['reads']) == 1
    assert [r['wait']['wait_state'] for r in plugin._parked_waits(ksa, t)] == ['waiting']
    assert [r['wait']['wait_state'] for r in plugin._parked_waits(ksa, t + stall + 60)] == ['stalled']
    assert len(board['reads']) == 1


def test_board_commit_rereads_rows(board):
    ksa, t = board['ksa'], time.time()
    plugin._parked_waits(ksa, t)
    board['park']('second', 's_two')
    got = plugin._parked_waits(ksa, t)
    assert len(board['reads']) == 2
    assert sorted(r['title'] for r in got) == ['first', 'second']
    assert _norm(got) == _norm(board['reference'](t))


class _Faulty:
    """A board observer whose park-event query fails ``fail`` times (a transient read error core swallows)."""

    def __init__(self, conn, fail):
        self._conn, self._fail = conn, fail

    def execute(self, sql, *args):
        if 'task_events' in sql and 'MAX(id)' in sql and self._fail:
            self._fail.pop()
            raise sqlite3.OperationalError('disk I/O error')
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_transient_event_read_error_is_never_kept(board, monkeypatch):
    ksa, t = board['ksa'], time.time()
    fail = [1]
    real = core.ro_connect

    def faulty(path, *args, **kwargs):
        return _Faulty(real(path, *args, **kwargs), fail)
    monkeypatch.setattr(core, 'ro_connect', faulty)
    plugin._parked_waits(ksa, t)  # the event query fails once: whatever this pass answers is not stored
    assert not fail
    got = plugin._parked_waits(ksa, t)
    assert _norm(got) == _norm(board['reference'](t))
    assert got[0]['wait']['waiting_since'] is not None  # the park event was read, not the degraded answer


def test_board_read_failure_skips_board_like_core(board, monkeypatch):
    ksa, t = board['ksa'], time.time()

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError('unable to open database file')
    monkeypatch.setattr(core, 'ro_connect', broken)
    assert plugin._parked_waits(ksa, t) == []
