"""Cross-pass structural reads; clocks and coverage remain outside the cache."""
import sqlite3

import pytest
from test_closeout import core, plugin
from test_light_read import _board


def _messages(path):
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, timestamp REAL, display_kind TEXT)')
        conn.execute("INSERT INTO messages VALUES ('chat', 'user', 'hello', 10, NULL)")


def _trace(monkeypatch):
    from reader_probe import trace_readers
    statements, _ = trace_readers(monkeypatch, core)
    return statements


@pytest.mark.parametrize('journal', ['wal', 'delete'])
def test_last_user_reads_every_pass_and_retains_nothing(tmp_path, monkeypatch, journal):
    """state.db is never observed across passes (a retained handle pins deleted WAL generations; see
    test_idle_hot_paths): each pass queries through its own pool, closed with the pass."""
    path = tmp_path / 'state.db'
    _messages(path)
    with sqlite3.connect(path) as writer:
        writer.execute(f'PRAGMA journal_mode={journal}')
    statements = _trace(monkeypatch)
    for _ in range(8):
        pool = core.ReadPool()
        try:
            assert core.last_user_at([path], ['chat'], pool=pool) == 10
        finally:
            pool.close()
    reads = lambda: [s for s in statements if s.startswith('SELECT content, timestamp')]
    assert len(reads()) == 8
    assert core._structural_reads.entries == {}
    with sqlite3.connect(path) as writer:
        writer.execute("INSERT INTO messages VALUES ('chat', 'user', 'new', 20, NULL)")
    assert core.last_user_at([path], ['chat']) == 20
    assert len(reads()) == 9


@pytest.mark.parametrize('light', [False, True])
def test_board_unchanged_commit_revision_survives_checkpoint(tmp_path, monkeypatch, light):
    path = tmp_path / 'kanban.db'
    _board(path)
    writer = sqlite3.connect(path, isolation_level=None)
    try:
        writer.execute('PRAGMA journal_mode=wal')
        writer.execute("UPDATE tasks SET title='changed' WHERE id='t_run'")
        statements = _trace(monkeypatch)
        first = core.read_cards([path], ['t_run'], light=light)
        assert writer.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone()[0] == 0
        for _ in range(8):
            assert core.read_cards([path], ['t_run'], light=light) == first
        reads = lambda: [s for s in statements if s.startswith('SELECT t.id')]
        assert len(reads()) == 1
        writer.execute("UPDATE tasks SET status='done' WHERE id='t_run'")
        assert core.read_cards([path], ['t_run'], light=light)['t_run']['status'] == 'done'
        assert len(reads()) == 2
    finally:
        writer.close()


@pytest.fixture(autouse=True)
def close_observers():
    core._structural_reads.clear()
    yield
    core._structural_reads.clear()


@pytest.mark.parametrize('kind', ['board', 'user'])
def test_read_errors_retry_without_commit(tmp_path, monkeypatch, kind):
    path = tmp_path / 'db'
    (_board if kind == 'board' else _messages)(path)
    real = core._cols
    calls = []
    def fail_once(*args):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError('temporary read failure')
        return real(*args)
    monkeypatch.setattr(core, '_cols', fail_once)
    def read():
        return core.read_cards([path], ['t_run']) if kind == 'board' else core.last_user_at([path], ['chat'])
    assert not read()
    assert read()
    assert read()
    assert len(calls) == (2 if kind == 'board' else 3)  # state.db reads are never cached


@pytest.mark.parametrize('kind', ['board', 'user'])
def test_database_replacement_and_missing_path_do_not_reuse_old_rows(tmp_path, kind):
    path = tmp_path / 'db'
    build = _board if kind == 'board' else _messages
    build(path)
    def read():
        return core.read_cards([path], ['t_run']) if kind == 'board' else core.last_user_at([path], ['chat'])
    assert read()
    replacement = tmp_path / 'replacement'
    build(replacement)
    with sqlite3.connect(replacement) as conn:
        conn.execute('DELETE FROM ' + ('tasks' if kind == 'board' else 'messages'))
    replacement.replace(path)
    assert not read()
    path.unlink()
    assert not read()
    build(path)
    assert read()


def test_commit_during_query_is_not_cached_under_new_revision(tmp_path):
    path = tmp_path / 'db'
    _messages(path)
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute('PRAGMA journal_mode=wal')
    calls = []
    def query(conn):
        calls.append(1)
        result = conn.execute('SELECT MAX(timestamp) FROM messages').fetchone()[0]
        if len(calls) == 1:
            writer.execute('UPDATE messages SET timestamp=20')
        return result
    try:
        cache = core._structural_reads
        assert cache.read(path, ('race',), query) == 10
        assert cache.read(path, ('race',), query) == 20
        assert cache.read(path, ('race',), query) == 20
        assert len(calls) == 2
    finally:
        writer.close()


def test_observers_and_query_entries_are_bounded_and_evictions_close(tmp_path, monkeypatch):
    monkeypatch.setattr(core, '_shared_reader', lambda: None)  # private fallback owns these entries
    cache = core._structural_reads
    monkeypatch.setattr(cache, 'MAX_DATABASES', 2)
    monkeypatch.setattr(cache, 'MAX_QUERIES', 2)
    path = tmp_path / 'db'
    _board(path)
    for i in range(3):
        core.read_cards([path], [f't_{i}'])
    assert len(cache.entries[str(path)][2]) == 2
    old_conn = cache.entries[str(path)][1]
    for i in range(2):
        other = tmp_path / str(i)
        _board(other)
        core.read_cards([other], ['t_run'])
    assert len(cache.entries) == 2
    with pytest.raises(sqlite3.ProgrammingError):
        old_conn.execute('SELECT 1')


def test_reused_board_rows_are_isolated_from_caller_mutation(tmp_path):
    path = tmp_path / 'db'
    _board(path)
    first = core.read_cards([path], ['t_run'])
    first['t_run']['status'] = 'done'
    first['t_run']['parents'].append('invented')
    again = core.read_cards([path], ['t_run'])
    assert again['t_run']['status'] == 'running'
    assert again['t_run']['parents'] == []


def test_structural_reads_do_not_write_database(tmp_path, monkeypatch):
    monkeypatch.setattr(core, '_shared_reader', lambda: None)  # private fallback transaction assertion
    path = tmp_path / 'db'
    _board(path)
    _messages(path)
    observer = sqlite3.connect(path)
    try:
        before = (observer.execute('PRAGMA data_version').fetchone(), tuple(observer.iterdump()))
        for _ in range(8):
            assert core.read_cards([path], ['t_run'])
            assert core.last_user_at([path], ['chat']) == 10
        after = (observer.execute('PRAGMA data_version').fetchone(), tuple(observer.iterdump()))
        assert before == after
        assert not core._structural_reads.entries[str(path)][1].in_transaction
    finally:
        observer.close()


def test_session_sets_paths_and_synthetic_filter_are_independent(tmp_path):
    first, second = tmp_path / 'one', tmp_path / 'two'
    for path in (first, second):
        _messages(path)
    with sqlite3.connect(second) as conn:
        conn.execute("INSERT INTO messages VALUES ('other', 'user', 'real', 30, NULL)")
        conn.execute("INSERT INTO messages VALUES ('chat', 'user', '[chat-closeout] notice', 40, NULL)")
        conn.execute("INSERT INTO messages VALUES ('chat', 'user', 'synthetic', 50, 'notice')")
    for _ in range(3):
        assert core.last_user_at([first], ['other']) == 0
        assert core.last_user_at([first, second], ['chat']) == 10
        assert core.last_user_at([first, second], ['chat', 'other']) == 30


from test_unlink import world
from test_board_coverage import board_world, KEY


def test_wake_quiet_deadline_advances_with_last_user(tmp_path, monkeypatch):
    path = tmp_path / 'state.db'
    _messages(path)
    led = core.Ledger(tmp_path / 'ledger.db')
    led.write('chat', batch=['t_one'], open=['t_one'])
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    statements = _trace(monkeypatch)
    wakes = []
    args = dict(wake=lambda *a, **k: wakes.append(a) or 'started', unseen={},
                read=lambda _: {'t_one': {'status': 'done'}}, proof=lambda *a: False,
                last_user=lambda ids: core.last_user_at([path], ids))
    assert plugin.wake_tick(now=309, **args) == []
    assert plugin.wake_tick(now=310, **args)
    assert len(wakes) == 1
    assert sum(s.startswith('SELECT content, timestamp') for s in statements) == 2  # read fresh every pass


@pytest.mark.parametrize('light', [False, True])
def test_new_pass_rechecks_shadow_expiry_and_disappearance(board_world, monkeypatch, light):
    _, path = board_world
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE kanban_sync_state SET pulled_at=1000')
    monkeypatch.setattr(plugin.time, 'time', lambda: 1000)
    assert KEY not in plugin._pass_reader(light=light)([KEY]).absence_veto
    monkeypatch.setattr(plugin.time, 'time', lambda: 1121)
    assert KEY in plugin._pass_reader(light=light)([KEY]).absence_veto
    monkeypatch.setattr(plugin.time, 'time', lambda: 1000)
    assert KEY not in plugin._pass_reader(light=light)([KEY]).absence_veto
    path.unlink()
    assert KEY in plugin._pass_reader(light=light)([KEY]).absence_veto
