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

@pytest.mark.parametrize('values', [(1.9999999999999998, 0), (2**63-1, 1)])
def test_real_token_results_preserved_full_and_incremental(tmp_path, values):
    from test_incremental import DB, M
    db = DB(tmp_path / 'state.db')
    db.turn('s', io=0)
    cur = S.read_cursor(db.path, M)
    with db.conn() as c:
        for table in ('sessions', 'session_model_usage'):
            c.execute(f'UPDATE {table} SET input_tokens=?, output_tokens=?', values)
        expected = c.execute(S._TOKENS_SQL).fetchone()[1]
    full, readable = S.session_snapshot([db.path])
    assert str(db.path) in readable
    assert full[(str(db.path), 's')][0] == int(expected)
    incremental, models = S.read_incremental(db.path, cur, M + 60, True)
    assert incremental['s'][0] == int(expected)
    assert models['s']['m'][0] == int(expected)


def test_batch_rows_bound_parameters():
    with closing(sqlite3.connect(':memory:')) as c:
        assert S._batch_rows(c, 'SELECT ?, ?, ?', 3, ('x', 2**53+1, None)) == [['x', 2**53+1, None]]

def test_batch_rows_without_json():
    with closing(sqlite3.connect(':memory:')) as c:
        calls = []

        class OldSQLite:
            def execute(self, sql, parameters=()):
                calls.append((sql, parameters))
                if 'json_group_array' in sql:
                    raise sqlite3.OperationalError('no such function: json_group_array')
                return c.execute(sql, parameters)
        assert S._batch_rows(OldSQLite(), 'SELECT ?, ?, ?', 3, ('x', 1, 2)) == [('x', 1, 2)]
        assert len(calls) == 2
        assert calls[1] == ('SELECT ?, ?, ?', ('x', 1, 2))

@pytest.mark.parametrize('message', [
    'database is locked',
    'no such function: unrelated_function',
], ids=['locked', 'non-json-function'])
def test_batch_rows_does_not_hide_read_error(message):
    with closing(sqlite3.connect(':memory:')) as c:
        calls = []
        error = sqlite3.OperationalError(message)

        class BrokenSQLite:
            def execute(self, sql, parameters=()):
                calls.append((sql, parameters))
                if len(calls) == 1:
                    raise error
                # A retry would succeed, hiding the original failure.
                return c.execute(sql, parameters)

        with pytest.raises(sqlite3.OperationalError) as raised:
            S._batch_rows(BrokenSQLite(), 'SELECT ?, ?, ?', 3, ('x', 1, 2))
        assert raised.value is error
        assert len(calls) == 1, 'unrelated read errors must not retry via fallback'
