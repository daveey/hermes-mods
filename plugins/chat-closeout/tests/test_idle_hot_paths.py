"""Measured idle hot paths: unseen counts, shadow coverage reads, and short-lived state.db reads.

Native replay (NATIVE_REPLAY_REPORT): ``_unseen_counts -> ksa._all_counts -> _remote_links`` was 58% of the
closeout callback and ``_read_shadow`` most of ``_board_absence_veto``. Unseen counts come only from local
boards' notify subscriptions, so closeout reads exactly that. State.db observers must not be retained: a
retained plain handle pins a deleted WAL generation and makes the next SessionDB open fail.
"""
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing

import pytest
from test_closeout import core, plugin
from test_board_coverage import board_world  # noqa: F401 -- fixture
from test_unlink import world  # noqa: F401 -- fixture used by board_world

linux = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='/proc/self/fd probe')
KINDS = ('completed', 'blocked', 'gave_up', 'crashed', 'timed_out', 'status')


@pytest.fixture(autouse=True)
def fresh_caches():
    core._structural_reads.clear()
    yield
    core._structural_reads.clear()


def _trace(monkeypatch, target=core):
    statements, opens = [], []
    real = target.ro_connect

    def connect(path, *args, **kwargs):
        opens.append(str(path))
        conn = real(path, *args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn
    monkeypatch.setattr(target, 'ro_connect', connect)
    return statements, opens


def _fds(path):
    real = os.path.realpath(path)
    out = []
    for name in os.listdir('/proc/self/fd'):
        try:
            target = os.readlink(f'/proc/self/fd/{name}')
        except OSError:
            continue
        if target.split(' (deleted)')[0] in (real, real + '-wal', real + '-shm'):
            out.append(target)
    return out


# ---- state.db: last_user_at is short-lived and uncached -------------------------------------------------------
def _messages(path, wal=True):
    with closing(sqlite3.connect(path)) as conn:
        if wal:
            conn.execute('PRAGMA journal_mode=wal')
        conn.execute('CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, timestamp REAL, display_kind TEXT)')
        conn.execute("INSERT INTO messages VALUES ('chat', 'user', 'hello', 10, NULL)")
        conn.commit()


@linux
def test_last_user_at_retains_no_state_db_handle(tmp_path):
    path = tmp_path / 'state.db'
    _messages(path)
    assert core.last_user_at([path], ['chat']) == 10
    assert _fds(path) == []
    assert not any('state.db' in k for k in core._structural_reads.entries)
    pool = core.ReadPool()
    try:
        assert core.last_user_at([path], ['chat'], pool=pool) == 10
        assert _fds(path)  # the pass's own pool connection, until the pass closes it
    finally:
        pool.close()
    assert _fds(path) == []


def test_last_user_at_reads_every_call_and_sees_commits(tmp_path, monkeypatch):
    path = tmp_path / 'state.db'
    _messages(path)
    statements, _ = _trace(monkeypatch)
    for _ in range(3):
        assert core.last_user_at([path], ['chat']) == 10
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("INSERT INTO messages VALUES ('chat', 'user', 'new', 20, NULL)")
        writer.commit()
    assert core.last_user_at([path], ['chat']) == 20
    assert sum(s.startswith('SELECT content, timestamp') for s in statements) == 4


def test_last_user_at_unreadable_db_is_skipped_and_dropped_from_pool(tmp_path):
    good, bad = tmp_path / 'good.db', tmp_path / 'bad.db'
    _messages(good)
    bad.write_text('not a database')
    pool = core.ReadPool()
    try:
        assert core.last_user_at([bad, good, tmp_path / 'missing.db'], ['chat'], pool=pool) == 10
    finally:
        pool.close()


@linux
def test_foreign_session_db_opens_after_sidecar_loss(tmp_path):
    """The reviewer's probe (rv_probe_state2): a retained observer made the next SessionDB open fail."""
    pytest.importorskip('hermes_state')
    db = tmp_path / 'state.db'
    env = {**os.environ}
    mk = f"from hermes_state import SessionDB; from pathlib import Path; s=SessionDB(db_path=Path({str(db)!r})); s.close()"
    subprocess.run([sys.executable, '-c', mk], check=True, env=env, timeout=60)
    assert core.last_user_at([db], ['s']) == 0.0
    # A stray in-process open/close drops this process's POSIX locks; a foreign last close then deletes sidecars.
    for suffix in ('-shm', ''):
        if os.path.exists(str(db) + suffix):
            os.close(os.open(str(db) + suffix, os.O_RDONLY))
    subprocess.run([sys.executable, '-c', f"import sqlite3;c=sqlite3.connect({str(db)!r});c.execute('select 1').fetchone();c.close()"],
                   check=True, timeout=60)
    r = subprocess.run([sys.executable, '-c', mk], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr[-400:]


# ---- unseen counts: local notify subscriptions only, never the whole activity projection ----------------------
def _board(path, wal=True):
    with closing(sqlite3.connect(path)) as conn:
        if wal:
            conn.execute('PRAGMA journal_mode=wal')
        conn.execute('CREATE TABLE kanban_notify_subs (task_id TEXT, platform TEXT, chat_id TEXT, last_event_id INTEGER)')
        conn.execute('CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT)')
        conn.executemany('INSERT INTO kanban_notify_subs VALUES (?, ?, ?, ?)', [
            ('t1', 'tui', 'chat', 0), ('t2', 'tui', 'chat', 5), ('t3', 'telegram', 'chat', 0),
            ('t4', 'tui', '', 0), ('t5', 'tui', 'other', 0)])
        conn.executemany('INSERT INTO task_events (id, task_id, kind) VALUES (?, ?, ?)', [
            (1, 't1', 'completed'), (2, 't2', 'blocked'), (3, 't3', 'completed'), (4, 't4', 'completed'),
            (5, 't5', 'commented')])
        conn.commit()


def test_unseen_counts_match_the_subscription_rule_and_sum_boards(tmp_path):
    a, b = tmp_path / 'a.db', tmp_path / 'b.db'
    _board(a)
    _board(b, wal=False)
    assert core.unseen_counts([a], KINDS) == {'chat': 1}
    assert core.unseen_counts([a, b], KINDS) == {'chat': 2}


def test_unseen_counts_reuse_unchanged_boards_and_see_every_commit(tmp_path, monkeypatch):
    path = tmp_path / 'kanban.db'
    _board(path)
    statements, _ = _trace(monkeypatch)
    for _ in range(8):
        assert core.unseen_counts([path], KINDS) == {'chat': 1}
    reads = lambda: sum('FROM kanban_notify_subs' in s for s in statements)
    assert reads() == 1
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("INSERT INTO task_events (id, task_id, kind) VALUES (6, 't5', 'gave_up')")
        writer.commit()
    assert core.unseen_counts([path], KINDS) == {'chat': 1, 'other': 1}
    assert reads() == 2
    result = core.unseen_counts([path], KINDS)
    result['chat'] = 99  # callers get their own dict
    assert core.unseen_counts([path], KINDS) == {'chat': 1, 'other': 1}


def test_unseen_counts_board_without_subscriptions_counts_nothing(tmp_path):
    path = tmp_path / 'kanban.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE tasks (id TEXT)')
    assert core.unseen_counts([path], KINDS) == {}


def test_unseen_counts_cache_is_keyed_by_event_kinds(tmp_path):
    path = tmp_path / 'kanban.db'
    _board(path)
    assert core.unseen_counts([path], KINDS) == {'chat': 1}
    assert core.unseen_counts([path], ('commented',)) == {'other': 1}
    assert core.unseen_counts([path], KINDS) == {'chat': 1}


def test_unseen_query_schema_gaps_count_nothing_but_other_errors_raise(tmp_path):
    path = tmp_path / 'old.db'
    with closing(sqlite3.connect(path)) as conn:  # predates subscription cursors
        conn.execute('CREATE TABLE kanban_notify_subs (task_id TEXT, platform TEXT, chat_id TEXT)')
        conn.execute('CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT)')
    assert core.unseen_counts([path], KINDS) == {}

    class Locked:
        def __init__(self, real):
            self.real = real

        def execute(self, sql, *args):
            if 'kanban_notify_subs s' in sql:
                raise sqlite3.OperationalError('database is locked')
            return self.real.execute(sql, *args)
    good = tmp_path / 'good.db'
    _board(good)
    with closing(sqlite3.connect(good)) as conn:
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            core._unseen_query(Locked(conn), KINDS)


def test_unseen_counts_unreadable_board_raises_and_retries(tmp_path, monkeypatch):
    path = tmp_path / 'kanban.db'
    _board(path)
    with pytest.raises((sqlite3.Error, OSError)):
        core.unseen_counts([path, tmp_path / 'gone.db'], KINDS)
    core._structural_reads.clear()
    real = core._unseen_query
    calls = []

    def flaky(conn, kinds):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError('database is locked')
        return real(conn, kinds)
    monkeypatch.setattr(core, '_unseen_query', flaky)
    with pytest.raises(sqlite3.OperationalError):
        core.unseen_counts([path], KINDS)
    assert core.unseen_counts([path], KINDS) == {'chat': 1}  # the failure was not cached


def test_plugin_unseen_counts_never_compute_the_activity_projection(tmp_path, monkeypatch):
    path = tmp_path / 'kanban.db'
    _board(path)
    import types

    def boom(*a, **k):
        raise AssertionError('_all_counts must not run: its remote-links refresh dominated idle CPU')
    ksa = types.SimpleNamespace(UNSEEN_KINDS=KINDS, _board_db_paths=lambda: [path], _all_counts=boom)
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_session_activity', ksa)
    import hermes_cli
    monkeypatch.setattr(hermes_cli, 'kanban_session_activity', ksa, raising=False)
    assert plugin._unseen_counts() == {'chat': 1}
    ksa._board_db_paths = lambda: [path, tmp_path / 'gone.db']
    assert plugin._unseen_counts() is None  # unreadable: never "all delivered"


def test_plugin_unseen_counts_fall_back_on_an_older_core(tmp_path, monkeypatch):
    import types
    ksa = types.SimpleNamespace(_all_counts=lambda: ({}, {}, {'chat': 3}, {}))
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_session_activity', ksa)
    import hermes_cli
    monkeypatch.setattr(hermes_cli, 'kanban_session_activity', ksa, raising=False)
    assert plugin._unseen_counts() == {'chat': 3}


def test_unseen_counts_equal_core_all_counts_on_a_real_board(tmp_path, monkeypatch):
    """Equivalence with the projection it replaces, on core's own schema and SQL."""
    kb = pytest.importorskip('hermes_cli.kanban_db')
    from hermes_cli import kanban_session_activity as ksa
    from hermes_cli.kanban_db_connect import connect_closing
    for key in ('HERMES_KANBAN_TASK', 'HERMES_DELEGATED_CHILD_CONTEXT', 'HERMES_DELEGATED_CHILD_OWNER'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_DB', str(tmp_path / 'kanban.db'))
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'hub-state'))
    ksa._reset_cache_for_tests()
    try:
        with connect_closing() as conn:
            done = kb.create_task(conn, title='done', session_id='s1')
            other = kb.create_task(conn, title='open', session_id='s2')
            for tid, sid in ((done, 's1'), (other, 's2')):
                conn.execute("INSERT INTO kanban_notify_subs (task_id, platform, chat_id, created_at) "
                             "VALUES (?, 'tui', ?, 0)", (tid, sid))
            kb._append_event(conn, done, 'completed', {})
            conn.commit()
        expected = dict(ksa._all_counts()[2])
        assert expected == {'s1': 1}
        assert plugin._unseen_counts() == expected
    finally:
        ksa._reset_cache_for_tests()


# ---- sync-shadow coverage: reuse unchanged files, clock always re-judged ----------------------------------------
@pytest.mark.parametrize('journal', ['wal', 'delete'])
def test_shadow_unchanged_file_read_once_and_any_commit_seen(board_world, monkeypatch, journal):
    _, path = board_world
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(f'PRAGMA journal_mode={journal}')
        conn.execute('UPDATE kanban_sync_state SET pulled_at=1000')
        conn.commit()
    monkeypatch.setattr(plugin.time, 'time', lambda: 1000)
    statements, _ = _trace(monkeypatch)
    for _ in range(8):
        assert plugin._read_shadow('build', {'t_one'}) == (True, set())
    reads = lambda: sum(s.startswith('SELECT id FROM tasks') for s in statements)
    assert reads() == 1
    assert plugin._read_shadow('build', {'t_one', 't_two'}) == (True, set())  # another id set: its own read
    assert reads() == 2
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO tasks VALUES ('t_one', 'todo', 'hub')")
        conn.commit()
    assert plugin._read_shadow('build', {'t_one'}) == (True, {'t_one'})
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('UPDATE kanban_sync_state SET pulled_at=1001')
        conn.commit()
    monkeypatch.setattr(plugin.time, 'time', lambda: 1121.5)
    assert plugin._read_shadow('build', {'t_one'}) == (False, {'t_one'})  # 120.5 s since the new pull
    observer = core._structural_reads.entries[str(path.absolute())][1]
    assert not observer.in_transaction  # no read snapshot outlives the call


def test_shadow_freshness_follows_the_clock_on_a_reused_read(board_world, monkeypatch):
    _, path = board_world
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('UPDATE kanban_sync_state SET pulled_at=1000')
        conn.commit()
    statements, _ = _trace(monkeypatch)
    for now, fresh in ((1000, True), (1120, True), (1120.5, False), (999, False), (1060, True)):
        monkeypatch.setattr(plugin.time, 'time', lambda now=now: now)
        assert plugin._read_shadow('build', {'t_one'})[0] is fresh
    assert sum(s.startswith('SELECT id FROM tasks') for s in statements) == 1


def test_shadow_replacement_corruption_and_deletion_are_never_reused(board_world, monkeypatch, tmp_path):
    _, path = board_world
    assert plugin._read_shadow('build', {'t_one'})[1] == set()
    replacement = tmp_path / 'replacement.db'
    with closing(sqlite3.connect(path)) as src, closing(sqlite3.connect(replacement)) as dst:
        src.backup(dst)
        dst.execute("INSERT INTO tasks VALUES ('t_one', 'todo', 'hub')")
        dst.commit()
    os.replace(replacement, path)
    assert plugin._read_shadow('build', {'t_one'})[1] == {'t_one'}
    path.write_bytes(b'not a database' * 100)
    with pytest.raises(sqlite3.Error):
        plugin._read_shadow('build', {'t_one'})
    with pytest.raises(sqlite3.Error):
        plugin._read_shadow('build', {'t_one'})
    path.unlink()
    with pytest.raises((sqlite3.Error, OSError)):
        plugin._read_shadow('build', {'t_one'})


def test_shadow_query_error_is_not_cached(board_world, monkeypatch):
    _, path = board_world
    real = core.ro_connect
    calls = []

    def flaky(p, *a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError('database is locked')
        return real(p, *a, **k)
    monkeypatch.setattr(core, 'ro_connect', flaky)
    with pytest.raises(sqlite3.OperationalError):
        plugin._read_shadow('build', {'t_one'})
    assert plugin._read_shadow('build', {'t_one'})[0] is True
    assert plugin._read_shadow('build', {'t_one'})[0] is True
    assert len(calls) == 2


def test_shadow_commit_during_read_is_not_cached(board_world, monkeypatch):
    _, path = board_world
    statements, _ = _trace(monkeypatch)
    real = core.shadow_rows
    state = {'n': 0}

    def racing(p, ids):
        state['n'] += 1
        if state['n'] == 1:  # a pull commits between the pre-read revision and the snapshot
            real_query = core._structural_reads._read

            def read(path_, entry, key, query):
                def late(conn):
                    with closing(sqlite3.connect(path)) as w:
                        w.execute("INSERT INTO tasks VALUES ('t_one', 'todo', 'hub')")
                        w.commit()
                    return query(conn)
                return real_query(path_, entry, key, late)
            monkeypatch.setattr(core._structural_reads, '_read', read)
            try:
                return real(p, ids)
            finally:
                monkeypatch.setattr(core._structural_reads, '_read', real_query)
        return real(p, ids)
    monkeypatch.setattr(core, 'shadow_rows', racing)
    reads = lambda: sum(s.startswith('SELECT id FROM tasks') for s in statements)
    assert plugin._read_shadow('build', {'t_one'})[1] == {'t_one'}
    assert plugin._read_shadow('build', {'t_one'})[1] == {'t_one'}
    assert reads() == 2  # read under a revision newer than its label: not reused
    assert plugin._read_shadow('build', {'t_one'})[1] == {'t_one'}
    assert reads() == 2


@linux
def test_shadow_observer_sidecar_loss_then_core_connect_recovers(board_world):
    lockguard = pytest.importorskip('hermes_cli.kanban_lockguard')
    from test_idle_reads_generation import _lose_sidecars, _deleted_maps, _wal
    _, path = board_world
    _wal(path)
    assert plugin._read_shadow('build', {'t_one'})[1] == set()
    _lose_sidecars(path, "INSERT INTO tasks VALUES ('t_one', 'todo', 'hub')")
    with closing(lockguard.connect(path)) as conn:  # recover() retires the registered observer
        assert conn.execute("SELECT id FROM tasks").fetchone()[0] == 't_one'
    assert not _deleted_maps(path)
    assert plugin._read_shadow('build', {'t_one'})[1] == {'t_one'}
