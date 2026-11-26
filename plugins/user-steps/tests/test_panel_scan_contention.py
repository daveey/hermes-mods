"""Complete inventory must not hand off at every publication under contention."""
from contextlib import closing
import json
import sqlite3
import threading
import pytest

from test_panel_membership import sources, membership, core


@pytest.mark.parametrize('encoding', ['UTF-8', 'UTF-16le', 'UTF-16be'])
def test_batched_inventory_preserves_exact_bytes_storage_classes_and_sparse_rowids(tmp_path, encoding):
    import hashlib
    with closing(sqlite3.connect(tmp_path / 'frames.db')) as conn:
        conn.execute("PRAGMA encoding='" + encoding + "'")
        conn.execute('CREATE TABLE replica(gw,coll,key,data,version)')
        raws = [b'\x00\xff\n[1,"fake",4]\n:payload', b'', None, 123, 1.25,
                'non-ascii \u2603\n\x00', b'\xff\xfe{\x00}\x00', b'\xef\xbb\xbf{}']
        for i in range(1030):
            rid = -9000 + i * 23
            conn.execute('INSERT INTO replica(rowid,gw,coll,key,data,version) VALUES (?,?,?,?,?,?)',
                         (rid, 'g\n\x00', 'tasks' if i % 9 else 'other',
                          f'key/\"{i}', raws[i % len(raws)], i))
        conn.execute("INSERT INTO replica(rowid,gw,coll,key,data,version) VALUES (999999,'g','tasks','invalid',CAST(x'ff00' AS TEXT),1)")
        expected = conn.execute("SELECT gw,key,CAST(data AS BLOB),version,typeof(data) FROM replica WHERE coll='tasks' ORDER BY rowid").fetchall()
        actual = list(membership._inventory_rows(conn))
        assert actual == expected
        for row in actual:
            if row[2] is not None:
                assert membership._inventory_digest(row[2]) == hashlib.sha256(row[2]).digest()
        for n in [0, 1, 2047, 2048, 2049, 4094, 4095, 100000]:
            raw = bytes(range(256)) * (n // 256) + bytes(range(n % 256))
            assert membership._inventory_digest(raw) == hashlib.sha256(raw).digest()


def test_legacy_without_rowid_keeps_complete_streaming_reader(tmp_path):
    with closing(sqlite3.connect(tmp_path / 'legacy.db')) as conn:
        conn.execute('CREATE TABLE replica(gw,coll,key,data,version,PRIMARY KEY(gw,coll,key)) WITHOUT ROWID')
        conn.execute('INSERT INTO replica VALUES (?,?,?,?,?)', ('g', 'tasks', 'k', '{}', 1))
        expected = conn.execute("SELECT gw,key,CAST(data AS BLOB),version,typeof(data) FROM replica WHERE coll='tasks'").fetchall()
        assert list(membership._inventory_rows(conn)) == expected


def test_post_prefix_failure_is_unknown_not_a_partial_or_restarted_inventory(sources):
    s = sources
    with closing(sqlite3.connect(s.paths['replica'])) as conn, conn:
        conn.executemany('INSERT INTO replica VALUES (?,?,?,?,1)',
                         (('other', 'tasks', f'default/default/t_{i}',
                           json.dumps(dict(id=f't_{i}', status='done')))
                          for i in range(1025)))
    assert s.capture()
    packets = []
    class Connection:
        def __init__(self, conn): self.conn = conn
        def execute(self, sql, args=()):
            if 'group_concat' in sql:
                packets.append(sql)
                if len(packets) == 2:
                    raise sqlite3.OperationalError('fixture read interruption')
            return self.conn.execute(sql, args)
        def __getattr__(self, name): return getattr(self.conn, name)
    def connect(path, **kwargs): return Connection(core._ro_connect(path, **kwargs))
    assert s.capture(connect=connect) is None
    assert len(packets) == 2


def test_batch_queries_share_original_read_transaction(sources):
    s = sources
    s.sql('replica', 'PRAGMA journal_mode=WAL')
    s.put(s.task())
    with closing(sqlite3.connect(s.paths['replica'])) as conn:
        conn.execute('BEGIN')
        before = list(membership._inventory_rows(conn))
        s.put(s.task('t_after', title='new publication'))
        assert list(membership._inventory_rows(conn)) == before
        conn.rollback()
        assert len(list(membership._inventory_rows(conn))) == len(before) + 1



def test_inventory_hash_keeps_each_native_update_below_gil_release_threshold(monkeypatch):
    import hashlib
    from types import SimpleNamespace
    chunks = []
    real = hashlib.sha256
    class Hash:
        def __init__(self, raw=b''):
            chunks.append(len(raw))
            self.hash = real(raw)
        def update(self, raw):
            chunks.append(len(raw))
            self.hash.update(raw)
        def digest(self): return self.hash.digest()
    monkeypatch.setattr(membership, 'hashlib', SimpleNamespace(sha256=Hash))
    task = json.dumps(dict(id='t_chunk', status='done', payload=list(range(1200)))).encode()
    result = membership._inventory(task, 'default/default/t_chunk', 'source', 'default',
                                   lambda *args: ['chat'], 'text')
    assert result == (('default', 't_chunk'), None, frozenset(['chat']))
    assert sum(chunks) == len(task)
    assert max(chunks) <= 2047



def test_complete_capture_bounds_driver_handoffs_under_contention(sources):
    s = sources
    s.obligation()
    payload = list(range(1200))
    with closing(sqlite3.connect(s.paths['replica'])) as conn, conn:
        conn.executemany('INSERT INTO replica VALUES (?,?,?,?,1)',
                         (('other', 'tasks', f'default/default/t_{i}',
                           json.dumps(dict(id=f't_{i}', status='done', payload=payload)))
                          for i in range(1025)))
    s.sql('replica', 'CREATE INDEX replica_coll ON replica(coll)')
    s.sql('replica', 'ALTER TABLE replica ADD COLUMN seq INTEGER')
    s.sql('replica', 'CREATE INDEX replica_poll_metadata ON replica(coll,gw,seq)')
    expected = s.capture()
    assert expected
    offered, acknowledged, stop = threading.Event(), threading.Event(), threading.Event()
    handoffs = []
    def contender():
        while offered.wait(2):
            offered.clear()
            acknowledged.set()
            if stop.is_set():
                return
    worker = threading.Thread(target=contender)
    worker.start()
    def handoff():
        handoffs.append(1)
        acknowledged.clear()
        offered.set()
        assert acknowledged.wait(2), 'fixture contender did not run'
    class Cursor:
        def __init__(self, cursor): self.cursor = cursor
        def __iter__(self): return self
        def __next__(self):
            handoff()
            return next(self.cursor)
        def fetchone(self):
            handoff()
            return self.cursor.fetchone()
        def __getattr__(self, name): return getattr(self.cursor, name)
    class Connection:
        def __init__(self, conn): self.conn = conn
        def execute(self, sql, args=()):
            if 'group_concat' in sql:
                plan = self.conn.execute('EXPLAIN QUERY PLAN ' + sql, args).fetchall()
                assert not any('TEMP B-TREE' in row[-1] for row in plan), plan
            cursor = self.conn.execute(sql, args)
            return Cursor(cursor) if 'CAST(data AS BLOB)' in sql else cursor
        def __getattr__(self, name): return getattr(self.conn, name)
    def connect(path, **kwargs): return Connection(core._ro_connect(path, **kwargs))
    try:
        assert s.capture(connect=connect) == expected
        # Real contender execution at each exposed result boundary, independent
        # of OS timing. A row-at-a-time path has 1027 boundaries here.
        assert 0 < len(handoffs) <= 8
    finally:
        stop.set()
        offered.set()
        worker.join(3)
        assert not worker.is_alive()
