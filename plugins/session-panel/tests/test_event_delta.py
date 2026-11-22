"""Event transport only: a drained event log is NOT semantic source freshness."""
import importlib.util
import sqlite3
import pytest
from contextlib import closing
from pathlib import Path


def load_reader():
    path = Path(__file__).resolve().parents[1] / 'dashboard' / 'event_delta.py'
    assert path.exists(), 'coherent event-page reader is not implemented'
    spec = importlib.util.spec_from_file_location('_panel_event_delta', path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def database(path, count=0):
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript('CREATE TABLE tasks (id TEXT PRIMARY KEY, session_id TEXT);'
                           'CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT);')
        conn.execute('INSERT INTO tasks VALUES (?, ?)', ('task-a', 'session-a'))
        conn.executemany('INSERT INTO task_events VALUES (?, ?, ?)',
                         [(i, 'task-a', 'heartbeat') for i in range(1, count + 1)])
        conn.commit()


def test_event_delta_bootstrap_and_bounded_drain(tmp_path):
    mod = load_reader()
    path = tmp_path / 'board.db'
    database(path, 20003)
    first = mod.read_event_delta([path], None, page_size=20000)
    key = str(path)
    assert first['reset_sources'] == {key}
    assert first['unknown_sources'] == {}
    assert first['cursor'][key]['id'] == 20000
    assert first['heads'] == {key: 20003}
    assert first['more'] == {key}
    assert first['cards'] == {key: {'task-a': 'session-a'}}
    assert first['touched_sessions'] == {'session-a'}
    last = mod.read_event_delta([path], first['cursor'], page_size=20000)
    assert last['cursor'][key]['id'] == 20003
    assert not last['more'] and not last['reset_sources']
    empty = mod.read_event_delta([path], last['cursor'])
    assert empty['cards'] == {} and empty['touched_sessions'] == set()
    assert empty['cursor'] == last['cursor']


@pytest.mark.parametrize('count', [1, 3, 5])
def test_event_delta_replacement_resets_even_equal_or_higher_head(tmp_path, count):
    mod = load_reader()
    path = tmp_path / 'board.db'
    database(path, 3)
    first = mod.read_event_delta([path])
    replacement = tmp_path / 'replacement.db'
    database(replacement, count)
    replacement.replace(path)
    result = mod.read_event_delta([path], first['cursor'])
    assert result['reset_sources'] == {str(path)}
    assert result['cards'] == {str(path): {'task-a': 'session-a'}}
    assert result['cursor'][str(path)]['id'] == count


def test_event_delta_unknown_preserves_cursor_and_removal_is_explicit(tmp_path):
    mod = load_reader()
    path = tmp_path / 'board.db'
    database(path, 2)
    first = mod.read_event_delta([path])
    key = str(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE task_events')
        conn.commit()
    failed = mod.read_event_delta([path], first['cursor'])
    assert key in failed['unknown_sources']
    assert failed['cursor'] == first['cursor']
    assert failed['heads'] == {} and failed['cards'] == {}
    removed = mod.read_event_delta([], failed['cursor'])
    assert removed['reset_sources'] == {key}
    assert removed['removed_sources'] == {key}
    assert removed['cursor'] == {}
    missing = tmp_path / 'missing.db'
    unavailable = mod.read_event_delta([missing])
    assert str(missing) in unavailable['unknown_sources']
    assert unavailable['cursor'] == {} and not missing.exists()


def test_event_delta_replacement_during_open_is_unknown(tmp_path, monkeypatch):
    mod = load_reader()
    path = tmp_path / 'board.db'
    database(path, 2)
    first = mod.read_event_delta([path])
    replacement = tmp_path / 'replacement.db'
    database(replacement, 4)
    real_connect = sqlite3.connect
    opened = []

    def connect(*args, **kwargs):
        replacement.replace(path)
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(mod.sqlite3, 'connect', connect)
    result = mod.read_event_delta([path], first['cursor'])
    assert str(path) in result['unknown_sources']
    assert result['cursor'] == first['cursor'] and result['cards'] == {}
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            conn.execute('SELECT 1')


@pytest.mark.parametrize('limit', [0, -1, 20001, True, 1.5])
def test_event_delta_rejects_unbounded_or_empty_page(tmp_path, limit):
    mod = load_reader()
    with pytest.raises(ValueError, match='page_size'):
        mod.read_event_delta([], page_size=limit)


@pytest.mark.parametrize('prior', [{}, {'id': '2'}, {'id': -1}, None, {'id': 2, 'identity': [1]}])
def test_event_delta_invalid_cursor_rebuilds_source(tmp_path, prior):
    mod = load_reader()
    path = tmp_path / 'board.db'
    database(path, 2)
    result = mod.read_event_delta([path], {str(path): prior})
    assert result['reset_sources'] == {str(path)}
    assert result['cursor'][str(path)]['id'] == 2


def test_event_delta_null_event_identity_fails_closed(tmp_path):
    mod = load_reader()
    path = tmp_path / 'board.db'
    database(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('INSERT INTO task_events VALUES (1, NULL, ?)', ('changed',))
        conn.commit()
    result = mod.read_event_delta([path])
    assert str(path) in result['unknown_sources']
    assert result['cards'] == {} and result['cursor'] == {}


def test_event_delta_snapshot_and_success_failure_closure(tmp_path, monkeypatch):
    mod = load_reader()
    path = tmp_path / 'board.db'
    database(path, 1)
    real_connect = sqlite3.connect
    with closing(real_connect(path)) as setup:
        setup.execute('PRAGMA journal_mode=WAL')
    opened = []
    injected = False

    class Reader(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            nonlocal injected
            if sql.startswith('SELECT e.id') and not injected:
                injected = True
                with closing(real_connect(path)) as writer:
                    writer.execute("UPDATE tasks SET session_id='session-b'")
                    writer.execute("INSERT INTO task_events VALUES (2, 'task-a', 'changed')")
                    writer.commit()
            return super().execute(sql, parameters)

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs, factory=Reader)
        opened.append(conn)
        return conn

    monkeypatch.setattr(mod.sqlite3, 'connect', connect)
    before = mod.read_event_delta([path])
    assert before['heads'] == {str(path): 1}
    assert before['cards'][str(path)] == {'task-a': 'session-a'}
    assert before['cursor'][str(path)]['id'] == 1
    after = mod.read_event_delta([path], before['cursor'])
    assert after['cards'][str(path)] == {'task-a': 'session-b'}
    assert after['cursor'][str(path)]['id'] == 2
    with closing(real_connect(path)) as writer:
        assert writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
        writer.execute('DROP TABLE task_events')
        writer.commit()
    assert str(path) in mod.read_event_delta([path], after['cursor'])['unknown_sources']
    assert len(opened) == 3
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            conn.execute('SELECT 1')


def test_event_delta_source_isolation_shrink_and_current_membership(tmp_path):
    mod = load_reader()
    a, b, bad = (tmp_path / name for name in ('a.db', 'b.db', 'bad.db'))
    database(a, 3)
    database(b, 1)
    first = mod.read_event_delta([a, b, bad], page_size=1)
    assert set(first['cards']) == {str(a), str(b)}
    assert str(bad) in first['unknown_sources']
    assert first['more'] == {str(a)}
    second = mod.read_event_delta([a, b], first['cursor'], page_size=1)
    third = mod.read_event_delta([a, b], second['cursor'], page_size=1)
    assert second['cursor'][str(a)]['id'] == 2 and second['more'] == {str(a)}
    assert third['cursor'][str(a)]['id'] == 3 and third['more'] == set()
    assert not second['reset_sources'] and not third['reset_sources']
    with closing(sqlite3.connect(a)) as writer:
        writer.execute('DELETE FROM task_events WHERE id > 1')
        writer.execute('DELETE FROM tasks')
        writer.commit()
    shrink = mod.read_event_delta([a, b], third['cursor'])
    assert shrink['reset_sources'] == {str(a)}
    assert shrink['cards'] == {str(a): {'task-a': None}}
    assert shrink['touched_sessions'] == set(), 'old membership needs a separate dependency index'
