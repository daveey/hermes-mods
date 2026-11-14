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
        class OldSQLite:
            def execute(self, sql, parameters=()):
                if 'json_group_array' in sql:
                    raise sqlite3.OperationalError('no such function: json_group_array')
                return c.execute(sql, parameters)
        assert S._batch_rows(OldSQLite(), 'SELECT ?, ?, ?', 3, ('x', 1, 2)) == [('x', 1, 2)]

class TransferProbe:
    """Count real SQLite result rows crossing into Python, not helper calls.

    A fetchall call is one Python call but transfers N SQLite rows. A row factory
    observes each of those transfers, including iteration and fetchmany, without
    changing the SQL, result values, or the sampler's batching implementation.
    Only token/ledger reads are measured; schema and watermark reads are separate.
    """

    def __init__(self, monkeypatch):
        self.queries = []
        real_connect = S.ro_connect
        probe = self

        class Connection:
            def __init__(self, path):
                self.connection = real_connect(path)

            def execute(self, sql, parameters=()):
                cursor = self.connection.execute(sql, parameters)
                if S._TOKENS_SQL in sql or S._MODELS_SQL in sql:
                    record = {'parameters': tuple(parameters), 'rows': 0}
                    probe.queries.append(record)

                    def count_row(cursor, row):
                        record['rows'] += 1
                        return row

                    cursor.row_factory = count_row
                return cursor

            def close(self):
                self.connection.close()

        monkeypatch.setattr(S, 'ro_connect', Connection)


def seed_transfer_db(tmp_path, count):
    from test_incremental import DB, M
    db = DB(tmp_path / 'state.db')
    # Two ledger rows per session exercise real GROUP BY aggregation; quoted
    # Unicode ids and >2**53 integer counters also check transport fidelity.
    expected = {}
    with db.conn() as c:
        for i in range(count):
            sid = f's-{i}-"雪'
            io, cache = 2**53 + i, i + 7
            c.execute('INSERT INTO sessions(id, started_at, input_tokens, cache_read_tokens) '
                      'VALUES (?, ?, ?, ?)', (sid, M, io, cache))
            c.executemany('INSERT INTO session_model_usage '
                          '(session_id, model, task, input_tokens, cache_read_tokens) '
                          'VALUES (?, ?, ?, ?, ?)',
                          [(sid, ' m ', 'a', io - 1, cache - 1),
                           (sid, 'm', 'b', 1, 1)])
            expected[sid] = (io, cache)
    return db, expected


@pytest.mark.parametrize('count', [0, 1, 1103])
@pytest.mark.parametrize('models', [False, True], ids=['sessions', 'models'])
def test_full_snapshot_transfers_one_sqlite_row(tmp_path, monkeypatch, count, models):
    db, expected = seed_transfer_db(tmp_path, count)
    probe = TransferProbe(monkeypatch)
    read = S.model_snapshot if models else S.session_snapshot
    snapshot, readable = read([db.path])
    assert readable == {str(db.path)}
    assert snapshot == {(str(db.path), sid, 'm') if models else (str(db.path), sid): value
                        for sid, value in expected.items()}
    assert len(probe.queries) == 1
    assert probe.queries[0]['rows'] == 1, 'full snapshot must transfer one aggregated SQLite row'


def test_batch_rows_does_not_hide_read_error():
    class BrokenSQLite:
        def execute(self, *args):
            raise sqlite3.OperationalError('database is locked')
    with pytest.raises(sqlite3.OperationalError, match='locked'):
        S._batch_rows(BrokenSQLite(), 'SELECT 1, 2, 3', 3)
