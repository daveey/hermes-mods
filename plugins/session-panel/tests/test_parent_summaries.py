"""Parent summaries read only their displayed fields, without cross-read state."""
import sqlite3
from contextlib import closing

import pytest
from test_core_readers import api, board, readers_for, kanban_db


class CountingCursor(sqlite3.Cursor):
    def __next__(self):
        row = super().__next__()
        self.connection.cells += len(self.description)
        return row


class CountingConnection(sqlite3.Connection):
    cells = 0
    statements = 0

    def execute(self, sql, parameters=()):
        if sql.startswith('SELECT id, title, status'):
            self.statements += 1
        return self.cursor(CountingCursor).execute(sql, parameters)


def tiny_board(path):
    c = sqlite3.connect(path, factory=CountingConnection)
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY, title, status, discarded BLOB)')
    c.commit()
    return c


def test_parent_summary_bounded_cells_order_and_missing(api, tmp_path):
    with closing(tiny_board(tmp_path / 'small.db')) as c:
        ids = [f'p{i:04d}' for i in range(513)]
        c.executemany('INSERT INTO tasks VALUES (?, ?, ?, ?)',
                      [(i, i, 'todo', b'x' * 8192) for i in ids])
        wanted = list(reversed(ids)) + ['missing']
        got = api._parent_summaries(c, wanted)
        assert [r['id'] for r in got] == list(reversed(ids))
        assert c.statements == 3
        assert c.cells == 513 * 3
        assert api._parent_summaries(c, []) == []
        assert c.statements == 3


def test_parent_summary_lost_notification_and_pinned_snapshot(api, tmp_path):
    path = tmp_path / 'wal.db'
    with closing(tiny_board(path)) as reader, closing(sqlite3.connect(path)) as writer:
        reader.execute('PRAGMA journal_mode=WAL')
        reader.execute("INSERT INTO tasks VALUES ('p', 'old', 'running', NULL)")
        reader.commit()
        reader.execute('BEGIN')
        old = api._parent_summaries(reader, ['p'])
        writer.execute("UPDATE tasks SET title='new', status='done' WHERE id='p'")
        writer.commit()  # no event or notification, while the reader keeps its old WAL snapshot
        assert api._parent_summaries(reader, ['p']) == old
        reader.rollback()
        assert api._parent_summaries(reader, ['p']) == [{'id': 'p', 'title': 'new', 'status': 'done'}]
        writer.execute("DELETE FROM tasks WHERE id='p'")
        writer.commit()
        assert api._parent_summaries(reader, ['p']) == []


def test_parent_summary_same_path_replacement(api, tmp_path):
    path, replacement = tmp_path / 'board.db', tmp_path / 'new.db'
    with closing(tiny_board(path)) as old, closing(tiny_board(replacement)) as new:
        old.execute("INSERT INTO tasks VALUES ('p', 'old', 'todo', NULL)")
        old.commit()
        new.execute("INSERT INTO tasks VALUES ('p', 'replacement', 'done', NULL)")
        new.commit()
        assert api._parent_summaries(old, ['p'])[0]['title'] == 'old'
        replacement.replace(path)
        with closing(sqlite3.connect(path)) as current:
            current.row_factory = sqlite3.Row
            assert api._parent_summaries(current, ['p'])[0]['title'] == 'replacement'
        assert api._parent_summaries(old, ['p'])[0]['title'] == 'old'


@pytest.mark.parametrize('value', [b'bad\xff', None, 42, 'ordinary'])
def test_parent_summary_retains_canonical_scalar_conversion(api, tmp_path, value):
    with closing(tiny_board(tmp_path / 'values.db')) as c:
        c.execute('INSERT INTO tasks VALUES (?, ?, ?, NULL)', ('p', value, value))
        got = api._parent_summaries(c, ['p'])[0]
        assert got == {'id': 'p', 'title': kanban_db._lossy_text(value),
                       'status': kanban_db._lossy_text(value)}


def test_parent_summary_read_error_is_not_empty_success(api, tmp_path):
    with closing(sqlite3.connect(tmp_path / 'missing.db')) as c:
        with pytest.raises(sqlite3.OperationalError):
            api._parent_summaries(c, ['p'])


def test_parent_summary_does_not_hydrate_complete_tasks(api, board, monkeypatch):
    path, conn = board
    parents = [kanban_db.create_task(conn, title=f'Parent {i}', initial_status='running') for i in range(4)]
    child = kanban_db.create_task(conn, title='Child', parents=parents, initial_status='running')
    expected = []
    for pid in sorted(parents):
        task = kanban_db.get_task(conn, pid)
        expected.append({'id': task.id, 'title': task.title, 'status': task.status})
    real = kanban_db.get_task
    fetched = []
    def traced(c, tid):
        fetched.append(tid)
        return real(c, tid)
    monkeypatch.setattr(kanban_db, 'get_task', traced)
    reader = readers_for(api, path, monkeypatch)
    try:
        detail = reader._detail(conn, child, 'default', False)
        assert detail['links']['parents'] == sorted(parents)
        assert detail['link_tasks'] == expected
        assert not set(parents).intersection(fetched), 'parent summaries hydrated full Task records'
    finally:
        reader.release()
