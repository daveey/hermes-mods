"""Selected-row transport must be bounded without weakening same-snapshot rewrites."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('selected_transport_wi', Path(__file__).parents[1] / 'waiting_inputs.py')
assert spec is not None and spec.loader is not None
wi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wi)


class Counted(sqlite3.Connection):
    calls = 0

    def execute(self, *args, **kwargs):
        self.calls += 1
        return super().execute(*args, **kwargs)


def world(n=1024):
    conn = sqlite3.connect(':memory:', factory=Counted)
    conn.execute('CREATE TABLE replica(gw TEXT, coll TEXT, key TEXT, seq INTEGER, data, PRIMARY KEY(gw,coll,key))')
    conn.executemany('INSERT INTO replica VALUES (?,?,?,?,?)',
                     [('g', 'tasks', f'default/default/t_{i}', i + 1, json.dumps({'status': 'ready'})) for i in range(n)])
    conn.commit()
    watch = wi.ReplicaWatch()
    watch.tails = frozenset(f't_{i}' for i in range(n))
    watch._rebuild(conn, watch.tails)
    conn.calls = 0
    return conn, watch


def test_increment_selected_row_queries_are_page_bounded():
    conn, watch = world()
    try:
        before = dict(watch.rows)
        conn.execute('BEGIN')
        assert watch._increment(conn)
        assert watch.rows == before
        assert conn.calls <= 16  # eight pages plus probes/membership, not 1024 point queries
    finally:
        conn.close()


@pytest.mark.parametrize('raw', ['{"status":"done"}', b'raw\x00bytes', -0.0, None, 123, 'not JSON', 'x' * 1048577])
def test_in_place_same_seq_keeps_literal_storage_values(raw):
    conn, watch = world(3)
    key = ('g', 'default/default/t_1')
    try:
        conn.execute("UPDATE replica SET data=? WHERE key=?", (raw, key[1]))
        conn.commit()
        actual_raw = conn.execute('SELECT data FROM replica WHERE key=?', (key[1],)).fetchone()[0]
        conn.execute('BEGIN')
        assert watch._increment(conn)
        assert watch.rows[key] == watch._entry(key[1], actual_raw)
    finally:
        conn.close()


def test_increment_and_rebuild_match_after_arrival_and_delete():
    conn, watch = world(3)
    try:
        conn.execute("DELETE FROM replica WHERE key='default/default/t_1'")
        conn.execute("INSERT INTO replica VALUES ('g','tasks','default/default/t_new',44,'{}')")
        conn.commit()
        assert not watch._increment(conn)  # deletion is still an unprovable aggregate delta
        watch._rebuild(conn, watch.tails | {'t_new'})
        assert ('g', 'default/default/t_1') not in watch.rows
        assert ('g', 'default/default/t_new') in watch.rows
    finally:
        conn.close()


@pytest.mark.parametrize('error', ['near "MATERIALIZED": syntax error', 'no such function: json_array'])
def test_transport_absent_empty_and_legacy_sql(error):
    conn, watch = world(3)
    class Legacy:
        def execute(self, sql, *args):
            if 'MATERIALIZED' in sql:
                raise sqlite3.OperationalError(error)
            return conn.execute(sql, *args)
    try:
        keys = list(watch.rows) + [('g', 'absent')]
        expected = {k: conn.execute(watch._ROW_SQL, k).fetchone()[1] for k in watch.rows}
        assert dict(watch._selected_rows(conn, keys)) == expected
        assert dict(watch._selected_rows(Legacy(), keys)) == expected
        before = conn.calls
        assert list(watch._selected_rows(conn, [])) == []
        assert conn.calls == before
    finally:
        conn.close()


def test_transport_noncompatibility_error_is_not_retried():
    conn, watch = world(3)
    class Broken:
        def execute(self, sql, *args):
            if 'MATERIALIZED' in sql:
                raise sqlite3.OperationalError('disk I/O error')
            return conn.execute(sql, *args)
    try:
        with pytest.raises(sqlite3.OperationalError, match='disk I/O error'):
            dict(watch._selected_rows(Broken(), list(watch.rows)))
    finally:
        conn.close()


def test_transport_keeps_snapshot_across_pages_and_sees_own_write(tmp_path):
    path = tmp_path / 'replica.db'
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('CREATE TABLE replica(gw TEXT,coll TEXT,key TEXT,seq INTEGER,data,PRIMARY KEY(gw,coll,key))')
    keys = [('g', str(i)) for i in range(129)]
    conn.executemany("INSERT INTO replica VALUES (?,'tasks',?,1,'old')", keys)
    conn.commit()
    writer = sqlite3.connect(path)
    watch = wi.ReplicaWatch()
    try:
        conn.execute('BEGIN')
        stream = iter(watch._selected_rows(conn, keys))
        first = next(stream)
        writer.execute("UPDATE replica SET data='new' WHERE key='128'")
        writer.commit()
        rows = dict([first, *stream])
        assert rows[keys[-1]] == 'old'
        conn.rollback()
        assert dict(watch._selected_rows(conn, keys))[keys[-1]] == 'new'
        conn.execute("UPDATE replica SET data='own' WHERE key='128'")
        assert dict(watch._selected_rows(conn, keys))[keys[-1]] == 'own'
        conn.rollback()
    finally:
        writer.close()
        conn.close()
