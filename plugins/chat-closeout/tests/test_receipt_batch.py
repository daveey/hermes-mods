"""Full board reads project historical receipts in one batch, preserving old-core fallback."""
from contextlib import closing
import json
import sqlite3

import pytest
from test_closeout import core

projection = pytest.importorskip('hermes_cli.kanban_completion_receipt_projection')
origin = pytest.importorskip('hermes_cli.kanban_origin')


@pytest.fixture
def board(tmp_path):
    path = tmp_path / 'kanban.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE tasks (id TEXT, title TEXT, status TEXT)')
        conn.executemany('INSERT INTO tasks VALUES (?,?,?)', [('a', 'A', 'done'), ('b', 'B', 'running')])
    return path


def test_one_receipt_batch_per_board(board, monkeypatch):
    calls = []
    monkeypatch.setattr(origin, 'gateway_names', lambda: ('fixture', set()))
    def many(conn, ids, *, board, gateway):
        calls.append((list(ids), board, gateway))
        return {'a': {'completion_receipt': {'marker': 'historical'}}}
    monkeypatch.setattr(projection, 'read_completed_many', many)
    monkeypatch.setattr(projection, 'read_inventory', lambda *a, **k: pytest.fail('per-task receipt read'))
    rows, error = core._board_rows(board, ['a', 'b'], False)
    assert error is None
    assert calls == [(['a', 'b'], 'default', 'fixture')]
    assert rows[0]['completion_receipt'] == {'marker': 'historical'}
    assert 'completion_receipt' not in rows[1]


def test_successful_empty_batch_is_authoritative(board, monkeypatch):
    calls = []
    monkeypatch.setattr(origin, 'gateway_names', lambda: ('fixture', set()))

    def many(conn, ids, *, board, gateway):
        calls.append((list(ids), board, gateway))
        return {}  # successful absence, not unavailable/old-core fallback

    monkeypatch.setattr(projection, 'read_completed_many', many)
    monkeypatch.setattr(projection, 'read_inventory',
                        lambda *a, **k: pytest.fail('empty batch triggered per-task read'))
    rows, error = core._board_rows(board, ['a', 'b'], False)
    assert error is None
    assert calls == [(['a', 'b'], 'default', 'fixture')]
    assert [r['id'] for r in rows] == ['a', 'b']
    assert all('completion_receipt' not in r for r in rows)


@pytest.mark.parametrize('created_on', [None, '', 'source-owner'])
def test_real_batch_parity_and_receipt_write_invalidation(tmp_path, monkeypatch, created_on):
    from test_completion_receipt import receipt

    path = tmp_path / 'boards' / 'build' / 'kanban.db'
    path.parent.mkdir(parents=True)
    value = receipt('rejected')
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript('''
            CREATE TABLE tasks(id TEXT, title TEXT, status TEXT, created_on TEXT);
            CREATE TABLE task_events(id INTEGER, task_id TEXT, kind TEXT, run_id INTEGER,
                                     created_at INTEGER, uid TEXT, payload TEXT);
            CREATE TABLE task_runs(id INTEGER, task_id TEXT, outcome TEXT, summary TEXT,
                                   metadata TEXT, uid TEXT);
        ''')
        conn.executemany('INSERT INTO tasks VALUES (?,?,?,?)', [
            ('a', 'Assessment', 'done', created_on), ('b', 'Legacy', 'done', None)])
        conn.execute("INSERT INTO task_events VALUES (1,'a','completed',1,123,'event-original',?)",
                     (json.dumps({'completion_receipt_admission': 1}),))
        conn.execute("INSERT INTO task_runs VALUES (1,'a','completed','Assessment',?,'run-original')",
                     (json.dumps({'completion_receipt': value}),))

    monkeypatch.setattr(origin, 'gateway_names', lambda: ('observer', set()))
    real_many, real_inventory = projection.read_completed_many, projection.read_inventory
    calls = []

    def many(conn, ids, *, board, gateway):
        result = real_many(conn, ids, board=board, gateway=gateway)
        # Compare real producers, including the no-receipt row, before the consumer merges fields.
        assert result == {tid: real_inventory(conn, tid, board=board, gateway=gateway) for tid in ids}
        calls.append(result)
        return result

    monkeypatch.setattr(projection, 'read_completed_many', many)
    monkeypatch.setattr(projection, 'read_inventory',
                        lambda *a, **k: pytest.fail('consumer bypassed real batch'))
    try:
        first = core.read_cards([path], ['build/a', 'build/b'])
        assert first['build/a']['completion_receipt'] == value
        source = first['build/a']['completion_receipt_source']
        assert source['ref'] == f"@task:build@{created_on or 'observer'}/a"
        assert source['event_uid'] == 'event-original'
        assert source['run_uid'] == 'run-original'
        assert 'completion_receipt' not in first['build/b']
        assert core.read_cards([path], ['build/a', 'build/b']) == first
        assert len(calls) == 1  # unchanged committed revision reuses the batch

        changed = receipt('delivered')
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute('UPDATE task_runs SET metadata=? WHERE id=1',
                         (json.dumps({'completion_receipt': changed}),))
        second = core.read_cards([path], ['build/a', 'build/b'])
        assert second['build/a']['completion_receipt'] == changed
        assert second['build/a']['completion_receipt_source'] == source
        assert len(calls) == 2  # receipt-only write invalidates; task rows never changed
    finally:
        core._structural_reads.clear()


def test_old_core_receipt_fallback(board, monkeypatch):
    monkeypatch.delattr(projection, 'read_completed_many')
    calls = []
    def read(conn, tid, *, board):
        calls.append(tid)
        return {'completion_receipt': {'id': tid}}
    monkeypatch.setattr(projection, 'read_inventory', read)
    rows, error = core._board_rows(board, ['a', 'b'], False)
    assert error is None
    assert calls == ['a', 'b']
    assert [r['completion_receipt']['id'] for r in rows] == ['a', 'b']
