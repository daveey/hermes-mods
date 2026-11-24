"""Real SQL bounded-tail, latest-run, work and no-projection-I/O controls."""
import dataclasses
import json
import os
from pathlib import Path
import socket
import sqlite3

import pytest

from test_snapshot_acquisition import acquisition, acquire
from test_core_readers import api, board, kanban_db


def test_bounded_tail_and_latest_run_match_full_canonical_history(acquisition, monkeypatch):
    r, cap, m, source, store, conn = acquisition
    tid = kanban_db.create_task(conn, title='Long history', session_id='S')
    # Canonical event writer; keep final timestamps tied to exercise the id tie-break.
    conn.execute('BEGIN')  # core's connection context alone does not start a transaction
    with conn:
        for n in range(5000):
            kanban_db._append_event(conn, tid, 'heartbeat' if n % 3 else 'note', {'n': n})
        conn.execute('UPDATE task_events SET created_at=1800000000 WHERE task_id=?', (tid,))
        cols = {row[1] for row in conn.execute('PRAGMA table_info(task_runs)')}
        assert {'task_id', 'profile', 'started_at', 'status'} <= cols
        for n in range(200):
            conn.execute('INSERT INTO task_runs(task_id,profile,started_at,status) VALUES (?,?,?,?)',
                         (tid, 'default', n + 1, 'completed'))
    events = [dataclasses.asdict(e) for e in kanban_db.list_events(conn, tid) if e.kind != 'heartbeat'][-40:]
    latest = dataclasses.asdict(kanban_db.latest_run(conn, tid))
    latest.pop('last_heartbeat_at', None)
    chat = m.ChatKey('fixture', 'default', 'S')
    store.register([m.Watch(chat, '', ('S',), 0)])
    queries = []
    class Traced(sqlite3.Connection):
        def set_trace_callback(self, callback):
            def trace(sql):
                queries.append([sql, 0])
                return callback(sql)
            return super().set_trace_callback(trace)
        def set_progress_handler(self, callback, interval):
            def progress():
                if queries:
                    queries[-1][1] += interval
                return callback()
            return super().set_progress_handler(progress, interval)
    original = sqlite3.connect
    monkeypatch.setattr(sqlite3, 'connect', lambda *args, **kw: original(*args, **kw, factory=Traced))
    part, snapshot = acquire(acquisition)
    key = m.CardKey('fixture', 'default', 'default', tid)
    detail = m.thaw(part.details[key])
    assert detail['events'] == events
    assert detail['runs'] == [latest]
    assert len(detail['events']) == 40
    assert len(detail['runs']) == 1
    print(json.dumps({'queries': sorted(queries, key=lambda q: q[1], reverse=True)[:10],
                      'metrics': m.thaw(part.metrics)}, sort_keys=True))
    # The bounded slim history query must not scan/deserialise the full tail.
    # Canonical execution acquisition has its own window queries: retain their
    # measured cost above, rather than pretending this adapter optimized core.
    slim = [steps for sql, steps in queries if "kind != 'heartbeat'" in sql]
    assert len(slim) == 1 and slim[0] < 2000
    assert part.metrics['elapsed_ns'] < 2_000_000_000
    plans = [tuple(row) for row in conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM task_events WHERE task_id=? AND kind!='heartbeat' "
        'ORDER BY created_at DESC,id DESC LIMIT 40', (tid,))]
    assert any('INDEX' in row[3] for row in plans)
    assert not any('TEMP B-TREE' in row[3] for row in plans)
    print(json.dumps({'history_rows': 5000, 'run_rows': 200, 'plan': plans,
                      'metrics': m.thaw(part.metrics)}, sort_keys=True))


def test_scoped_projection_has_no_sql_network_or_live_reader_fallback(acquisition, monkeypatch):
    r, cap, m, source, store, conn = acquisition
    tid = kanban_db.create_task(conn, title='Memory projection', session_id='S')
    chat = m.ChatKey('fixture', 'default', 'S')
    store.register([m.Watch(chat, '', ('S',), 0)])
    _, snapshot = acquire(acquisition)
    def forbidden(*args, **kwargs):
        raise AssertionError('projection attempted I/O')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(r, '_detail', forbidden)
    readers = m.SnapshotReaders(snapshot, chat)
    assert [row['id'] for row in readers.linked(['S'])] == [tid]
    assert [row['id'] for row in readers.done(['S'])] == [tid]
    first = readers.card(tid, 'default')
    first['detail']['task']['title'] = 'must not alias'
    assert readers.card(tid, 'default')['detail']['task']['title'] == 'Memory projection'


@pytest.mark.parametrize('status', ['ready', 'running', 'blocked', 'triage', 'scheduled'])
def test_real_reader_through_reducer_matches_snapshot_projection(acquisition, monkeypatch, status):
    from hermes_cli import kanban_session_activity as ksa, kanban_db_sync as sync
    r, cap, m, source, store, conn = acquisition
    parent = kanban_db.create_task(conn, title='Parent producer', initial_status='running')
    tid = kanban_db.create_task(conn, title='Linked work', session_id='S', parents=[parent], initial_status='running')
    with conn:
        conn.execute('UPDATE tasks SET status=? WHERE id=?', (status, tid))
    monkeypatch.setattr(ksa, '_board_db_paths', lambda: [r.boards()[0][1]])
    monkeypatch.setattr(sync, 'sync_config', lambda: {})
    monkeypatch.setattr(ksa, '_remote_links', lambda **kwargs: [])
    monkeypatch.setattr(ksa, 'remote_links_and_origin_rows', lambda **kwargs: ([], []))
    monkeypatch.setattr(ksa.time, 'time', lambda: 1800000000.0)
    monkeypatch.setattr(r, 'lineage', lambda session: ['S'])
    chat = m.ChatKey('fixture', 'default', 'S')
    store.register([m.Watch(chat, '', ('S',), 0)])
    feed = r.snapshot_feed_helpers().Feed(r, emit=lambda payload: None, clock=lambda: 1800000000.0)
    base = {'lineage': ['S'], 'requests': [], 'outcome_json': None, 'rentals': [],
            'gateway': 'fixture', 'profile': 'default'}
    old = feed.compute('S', base=base)
    # This unactivated adapter intentionally omits unsupported optional v1
    # enrichment. Compare every original field and the exact reducer revision
    # at the same input contract, not an enriched-vs-unenriched wire hash.
    assert any('actionability' in rec.get('detail', {}) for rec in old['cards'].values())
    for rec in old['cards'].values():
        rec.get('detail', {}).pop('actionability', None)
    old['model'] = feed.reducer(old, now=1800000000.0)
    _, snapshot = acquire(acquisition)
    sr = m.SnapshotReaders(snapshot, chat)
    assert all('actionability' not in m.thaw(detail) for detail in snapshot.parts[source].details.values())
    new = feed.compute('S', readers=sr, base=base)
    assert new['model']['sections'] == old['model']['sections']
    assert new['model']['header'] == old['model']['header']
    assert new['model']['revision'] == old['model']['revision']
    if output := os.environ.get('PANEL_SNAPSHOT_GOLDENS'):
        path = Path(output)
        cases = json.loads(path.read_text()) if path.exists() else {}
        cases[f'snapshot_{status}'] = {'model': new['model'], 'now': 1800000000.0}
        path.write_text(json.dumps(cases, sort_keys=True))
