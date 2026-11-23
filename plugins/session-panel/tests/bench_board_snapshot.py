#!/usr/bin/env python3
"""Disposable B1 acquisition/projection costs; no service or publication claims.

Run with the canonical core/test environment on PYTHONPATH. The fixture uses real
migrations, selectors, execution acquisition and the real reducer. B2 must add
reserved-role contention and actual emit/catch-up receipts to this same harness.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import resource
import statistics
import sys
import tempfile
import time
import threading


def run(args):
    started_cpu, started_thread = time.process_time_ns(), time.thread_time_ns()
    with tempfile.TemporaryDirectory(prefix='panel-snapshot-bench-', dir=os.environ.get('TMPDIR')) as temp:
        root = Path(temp)
        # Do not let fixture defaults discover any real board, profile or ledger.
        for name in ('HOME', 'HERMES_HOME', 'HERMES_KANBAN_HOME'):
            os.environ[name] = str(root)
        os.environ.pop('HERMES_KANBAN_DB', None)
        from hermes_cli import kanban_db
        from hermes_cli.kanban_db_connect import connect
        path = root / 'kanban.db'
        conn = connect(path)
        try:
            tasks = [kanban_db.create_task(conn, title=f'Shared card {n}', session_id='S0')
                     for n in range(args.shared_cards)]
            with conn:
                conn.executemany("INSERT INTO kanban_notify_subs(task_id,platform,chat_id,created_at) VALUES (?,'tui',?,1)",
                                 [(task, f'S{n}') for task in tasks for n in range(1, args.chats)])
                for n in range(args.history_events):
                    kanban_db._append_event(conn, tasks[n % len(tasks)],
                                            'heartbeat' if n % 3 else 'note', {'n': n})
            api_path = Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py'
            spec = importlib.util.spec_from_file_location('_snapshot_cost_api', api_path)
            api = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = api
            spec.loader.exec_module(api)
            readers = api.CoreReaders()
            readers.boards = lambda: [('default', path)]
            cap = readers.snapshot_modules()
            m = cap.snapshot
            store = m.SnapshotStore()
            source = m.SourceKey('fixture', 'default', 'default', 'local', str(path))
            store.add_source(source)
            chats = [m.ChatKey('fixture', 'default', f'S{n}') for n in range(args.chats)]
            # --chats above the panel limit exercises one oversized lineage union.
            if args.chats > 400:
                chats = chats[:1]
                watches = [m.Watch(chats[0], '', tuple(f'S{n}' for n in range(args.chats)), 0)]
            else:
                watches = [m.Watch(chat, '', (chat.session_id,), 0) for chat in chats]
            store.register(watches)
            feed = readers.snapshot_feed_helpers().Feed(readers, emit=lambda payload: None,
                                                       clock=lambda: 1800000000.0)
            previous, captures, projections, admits = None, [], [], []
            stop = time.monotonic() + args.seconds
            loops = 0
            baseline_threads = threading.active_count()
            while loops == 0 or time.monotonic() < stop:
                written = time.monotonic_ns()
                if loops:
                    with conn:
                        conn.execute('UPDATE tasks SET title=? WHERE id=?', (f'Changed {loops}', tasks[loops % len(tasks)]))
                hints = cap.CaptureHints(store.version(source), store.owner_token(source), 1800000000.0)
                part = readers.capture_local(source, previous, store.demands(source), hints)
                snapshot, admissions = store.accept_local(part, hints.prior_version)
                admitted = time.monotonic_ns()
                captures.append(m.thaw(part.metrics))
                admits.extend((admitted - written) / 1e9 for _ in admissions)
                for chat in chats:
                    began = time.monotonic_ns()
                    sr = m.SnapshotReaders(snapshot, chat)
                    bundle = feed.compute(chat.session_id, readers=sr,
                                          base={'lineage': sr.lineage(chat.session_id), 'requests': [],
                                                'outcome_json': None, 'rentals': [], 'gateway': 'fixture',
                                                'profile': 'default'})
                    assert bundle['model']['version'] == 1
                    projections.append((time.monotonic_ns() - began) / 1e9)
                previous = part
                loops += 1
            readers.release()
            assert threading.active_count() == baseline_threads
            def stats(values):
                ordered = sorted(values)
                return {'count': len(ordered), 'max': max(ordered, default=0),
                        'p95': ordered[min(len(ordered) - 1, int(len(ordered) * .95))] if ordered else 0,
                        'mean': statistics.fmean(ordered) if ordered else 0}
            return {
                'schema': 'board-snapshot-b1-cost/1', 'scope': 'disposable unactivated B1 only',
                'input': vars(args) | {'output': str(args.output)}, 'iterations': loops,
                'sql_variable_limit': 999, 'captures': captures,
                'acquisition_s': stats([item['elapsed_ns'] / 1e9 for item in captures]),
                'source_to_admission_s': stats(admits), 'projection_reducer_s': stats(projections),
                'process_cpu_s': (time.process_time_ns() - started_cpu) / 1e9,
                'thread_cpu_s': (time.thread_time_ns() - started_thread) / 1e9,
                'memory_high_water_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                'observed_role_concurrency': {'local': 1, 'external': 0, 'projection': 1, 'publisher': 0},
                'producer_counts': {'local': loops, 'external': 0, 'projections': len(projections)},
                'publication_receipts': 0, 'missing_publication_receipts': len(projections),
                'unobserved': ['B2 reserved concurrency/fairness', 'held external source',
                               'writer contention', 'actual emit/catch-up', 'live Desktop'],
            }
        finally:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chats', type=int, required=True)
    parser.add_argument('--shared-cards', type=int, required=True)
    parser.add_argument('--history-events', type=int, required=True)
    parser.add_argument('--seconds', type=float, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.chats < 1 or args.shared_cards < 1 or args.history_events < 0 or args.seconds < 0:
        parser.error('counts and seconds must be nonnegative; chats/cards must be positive')
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ('iterations', 'acquisition_s', 'projection_reducer_s',
                                                 'missing_publication_receipts')}, sort_keys=True))


if __name__ == '__main__':
    main()
