"""Schema probes cross into Python as one SQLite row.

Each row a cursor returns releases and re-takes the GIL; inside the busy Desktop backend a re-take can wait a whole
5 ms switch interval. ``transcript_key`` probed state.db's ~70-column ``sessions`` table row by row on every proof;
these tests pin that ``_cols`` (and so ``transcript_key``) answers the same while transferring O(1) rows."""
import sqlite3
from contextlib import closing

import pytest
from test_closeout import core

WIDE = [f'c{i}' for i in range(68)] + ['transcript_revision', 'odd "name" 雪']


def _db(path):
    with closing(sqlite3.connect(path)) as conn:
        cols = ', '.join('"' + c.replace('"', '""') + '"' for c in WIDE)
        conn.executescript(f"""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, {cols});
            CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
                                   tool_calls TEXT, display_kind TEXT, timestamp REAL, active INTEGER);
            CREATE TRIGGER messages_transcript_revision_update AFTER UPDATE OF role, content, tool_calls ON messages
            BEGIN UPDATE sessions SET transcript_revision = transcript_revision + 1 WHERE id = new.session_id; END;
            CREATE TRIGGER messages_display_revision_update AFTER UPDATE OF display_kind ON messages
            BEGIN UPDATE sessions SET transcript_revision = transcript_revision + 1 WHERE id = new.session_id; END;
            INSERT INTO sessions (id, transcript_revision) VALUES ('s', 3);
            INSERT INTO messages (session_id, role, content, timestamp, active) VALUES ('s', 'user', 'hi', 10.5, 1);
        """)
        conn.commit()


class Counted:
    """A connection whose cursors count the rows they return."""

    def __init__(self, path):
        self.rows = 0
        self.conn = sqlite3.connect(path)

        def count(cursor, row):
            self.rows += 1
            return row
        self.conn.row_factory = count

    def execute(self, *a):
        return self.conn.execute(*a)


def test_cols_same_names_one_row(tmp_path):
    path = tmp_path / 'state.db'
    _db(path)
    c = Counted(path)
    with closing(c.conn):
        expected = {r[1] for r in c.conn.execute('PRAGMA table_info(sessions)')}
        c.rows = 0
        assert core._cols(c.conn, 'sessions') == expected == {'id', *WIDE}
        assert c.rows == 1
        assert core._cols(c.conn, 'missing') == set()


def test_cols_without_json_functions_reads_rows(tmp_path):
    path = tmp_path / 'state.db'
    _db(path)

    class NoJson:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, *a):
            if 'json_group_array' in sql:
                raise sqlite3.OperationalError('no such function: json_group_array')
            return self.conn.execute(sql, *a)

    with closing(sqlite3.connect(path)) as conn:
        assert core._cols(NoJson(conn), 'sessions') == {'id', *WIDE}
        with pytest.raises(sqlite3.OperationalError, match='database is locked'):
            class Locked(NoJson):
                def execute(self, sql, *a):
                    raise sqlite3.OperationalError('database is locked')
            core._cols(Locked(conn), 'sessions')


def test_transcript_key_transfers_constant_rows(tmp_path):
    path = tmp_path / 'state.db'
    _db(path)
    c = Counted(path)
    with closing(c.conn):
        key = core.transcript_key(c.conn, ['s'])
        assert key == (('s',), (1, 1, 1.0, 10.5, 1, 1, 3))
        # two triggers + one column list + one aggregate: never one row per sessions column
        assert c.rows == 4
