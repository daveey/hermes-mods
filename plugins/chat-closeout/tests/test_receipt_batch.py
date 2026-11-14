"""Full board reads project historical receipts in one batch, preserving old-core fallback."""
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
