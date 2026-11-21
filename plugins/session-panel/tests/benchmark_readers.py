"""Reproducible synthetic real-reader benchmark (not a production SLA claim).

Run with the managed core test interpreter, core + pytest on PYTHONPATH:
  python tests/benchmark_readers.py --output /private/path/result.json
Copies of this file and test_reader_batch.py can run against the unmodified base.
No captured production rows are used or emitted.
"""
import argparse
import collections
import json
import platform
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from test_core_readers import api, board, kanban_db
from test_reader_batch import graph


def run(root):
    with pytest.MonkeyPatch.context() as patch:
        mod = api.__wrapped__(root, patch)
        local_board = board.__wrapped__(root)
        fixture = graph.__wrapped__(mod, local_board, root, patch)
        r, shared, foreign, ksa = next(fixture)
        c = local_board[1]
        from hermes_cli import kanban_queue_position as kqp, kanban_chat_links as links
        counts = collections.Counter()
        raw, remote_read, detail = ksa._read_session_tasks, links._read_replica, kqp.detail_fields
        try:
            # 23 sessions, shared + distinct cards, dependency chains, ready queue and replica runs.
            # The graph fixture already seeds two shared local/remote cards and all 23 associations.
            rc = sqlite3.connect(r.ksc.shadow_path('default'))
            rc.row_factory = sqlite3.Row
            try:
                for i in range(23):
                    cid = kanban_db.create_task(c, title=f'Local work {i}', session_id=f'S{i}', initial_status='running',
                                                parents=[shared] if i % 3 == 0 else [])
                    c.execute('UPDATE tasks SET status=? WHERE id=?', (('ready', 'running', 'todo')[i % 3], cid))
                    # Real replica rows with historical task_runs; association remains origin-qualified.
                    rid = kanban_db.create_task(rc, title=f'Remote work {i}', session_id='elsewhere', initial_status='running')
                    rc.execute('UPDATE tasks SET created_on=? WHERE id=?', ('remote-test', rid))
                    from hermes_cli.kanban_chat_links import link_uid
                    rc.execute('INSERT INTO task_chat_links (uid, task_id, chat_gateway, chat_profile, chat_session, kind, released, created_at) '
                               'VALUES (?, ?, ?, ?, ?, ?, 0, 1)',
                               (link_uid(rid, 'local-test', 'default', f'S{i}', 'shepherd'), rid,
                                'local-test', 'default', f'S{i}', 'shepherd'))
                    for n in range(3):
                        rc.execute("INSERT INTO task_runs(task_id, profile, status, started_at, ended_at, summary) VALUES (?, 'default', 'done', ?, ?, 'synthetic history')",
                                   (rid, 10 + n, 11 + n))
                c.commit(); rc.commit()
            finally:
                rc.close()

            def counted(name, fn):
                def call(*a, **k):
                    counts[name] += 1
                    return fn(*a, **k)
                return call

            patch.setattr(ksa, '_read_session_tasks', counted('local_hydration', raw))
            patch.setattr(links, '_read_replica', counted('replica_inventory', remote_read))
            patch.setattr(kqp, 'detail_fields', counted('queue_detail', detail))
            sessions = [f'S{i}' for i in range(23)]
            feed = mod._load_feed().Feed(r, emit=lambda _: None)
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            patch.setattr(mod, 'feed', lambda: feed)
            patch.setattr(mod, '_request_profile', lambda: mod.LAUNCH_PROFILE)
            app = FastAPI()
            app.include_router(mod.router, prefix='/api/plugins/session-panel')
            with TestClient(app) as client:
                started, cpu = time.perf_counter(), time.process_time()
                response = client.post('/api/plugins/session-panel/changes', json={'sessions': sessions})
                cold_s, cpu_s = time.perf_counter() - started, time.process_time() - cpu
                assert response.status_code == 200
                out = response.json()
            cold_counts = dict(counts)
            assert len(out['bundles']) == 23 and not feed.status()['failures']
            assert all(len(b['tasks']) >= 4 for b in out['bundles'].values())
            started = time.perf_counter()
            cached = feed.changes(sessions, since=out['rev'], epoch=out['epoch'])
            cached_s = time.perf_counter() - started
            assert cached['bundles'] == {}
            # Full refresh traverses exactly the same real readers, with a warm SQLite page cache.
            started = time.perf_counter()
            feed.tick()
            tick_s = time.perf_counter() - started
            return {'python': platform.python_version(), 'platform': platform.platform(),
                    'core_sha': subprocess.check_output(['git', '-C', str(Path(ksa.__file__).parents[1]), 'rev-parse', 'HEAD'], text=True).strip(),
                    'sessions': 23, 'local_cards': 24, 'replica_cards': 24, 'replica_historical_runs': 69,
                    'cold_changes_s': cold_s, 'cold_cpu_s': cpu_s, 'cached_control_s': cached_s,
                    'full_tick_s': tick_s, 'cold_reader_calls': cold_counts,
                    'bundles': len(out['bundles']), 'failures': feed.status()['failures'],
                    'limits': 'Synthetic Linux worker fixture; empty origin-ledger boundary, real local/replica/queue computation through in-process ASGI HTTP POST /changes. No network transport, hub contention or deployed-process proof.'}
        finally:
            fixture.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='session-panel-benchmark-') as directory:
        result = run(Path(directory))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
