"""The batching transport preserves original SQLite result values."""
import sqlite3
from contextlib import closing
import pytest
from throughput_under_test import sampler as S

@pytest.mark.parametrize('rows', [[], [(None, 0, 2**63-1)],
    [('quote"\\\n雪', -(2**63), 2**53+1)], [(b'legacy-id', 1, 2)]])
def test_batch_rows_matches_sqlite(rows):
    with closing(sqlite3.connect(':memory:')) as c:
        c.execute('CREATE TABLE t (id, io, cache)')
        c.executemany('INSERT INTO t VALUES (?, ?, ?)', rows)
        expected = c.execute('SELECT * FROM t').fetchall()
        assert [tuple(r) for r in S._batch_rows(c, 'SELECT * FROM t', 3)] == expected

def test_batch_rows_bound_parameters():
    with closing(sqlite3.connect(':memory:')) as c:
        assert S._batch_rows(c, 'SELECT ?, ?, ?', 3, ('x', 2**53+1, None)) == [['x', 2**53+1, None]]

def test_batch_rows_without_json():
    with closing(sqlite3.connect(':memory:')) as c:
        class OldSQLite:
            def execute(self, sql, parameters=()):
                if 'json_group_array' in sql:
                    raise sqlite3.OperationalError('no such function: json_group_array')
                return c.execute(sql, parameters)
        assert S._batch_rows(OldSQLite(), 'SELECT ?, ?, ?', 3, ('x', 1, 2)) == [('x', 1, 2)]

def test_batch_rows_does_not_hide_read_error():
    class BrokenSQLite:
        def execute(self, *args):
            raise sqlite3.OperationalError('database is locked')
    with pytest.raises(sqlite3.OperationalError, match='locked'):
        S._batch_rows(BrokenSQLite(), 'SELECT 1, 2, 3', 3)
