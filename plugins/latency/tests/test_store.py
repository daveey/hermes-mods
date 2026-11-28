"""Isolated store contract tests (stdlib unittest, also pytest compatible)."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone


def sample(index, duration=1, start=1800000000, metric='tool', host='host'):
    return dict(id=str(index), metric=metric, start=start, dur_ms=duration,
                host=host, gateway='gateway', attrs={'session': 'session-id'})


def load(name):
    spec = importlib.util.spec_from_file_location('latency_' + name, Path(__file__).parents[1] / (name + '.py'))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StoreTests(unittest.TestCase):
    def test_idempotent_ingest_cursor_and_zero(self):
        module = load('store')
        with tempfile.TemporaryDirectory() as root:
            store = module.Store(Path(root) / 'store.db')
            sample = dict(id='one', metric='tool', start=1800000000, dur_ms=0, host='host', gateway='gateway', attrs={})
            self.assertEqual(store.ingest([sample], source='log', cursor={'offset': 1}), 1)
            self.assertEqual(store.ingest([sample], source='log', cursor={'offset': 2}), 0)
            self.assertEqual(store.cursor('log'), {'offset': 2})
            view = store.view('tool', 1799990000, 1800010000)
            self.assertEqual((view['n'], view['p50'], view['p99']), (1, 0, 0))
            self.assertEqual(len(view['hours']), 1)
            self.assertEqual(view['slowest'][0]['id'], 'one')

    def test_quantiles_nearest_rank_upper_edges_and_small_values(self):
        module = load('store')
        with tempfile.TemporaryDirectory() as root:
            store = module.Store(Path(root) / 'store.db')
            values = [0.0] + [10 ** (-9 + i * 0.02) for i in range(1000)]
            store.ingest([sample(i, value, host='a' if i % 2 else 'b') for i, value in enumerate(values)])
            view = store.view('tool', 1799990000, 1800010000)
            self.assertEqual(view['n'], len(values))
            for label, p in [('p50', .5), ('p90', .9), ('p99', .99)]:
                exact = values[math.ceil(len(values) * p) - 1]
                self.assertGreaterEqual(view[label], exact)
                self.assertLessEqual(view[label], exact * 1.01)
            self.assertEqual(len(view['slowest']), 20)
            self.assertEqual(view['slowest'][0]['dur_ms'], values[-1])
            with sqlite3.connect(store.path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM slow').fetchone()[0], 20)
                self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
                self.assertEqual(db.execute('SELECT COUNT(DISTINCT host) FROM hist').fetchone()[0], 2)

    def test_bucket_boundaries_never_underestimate(self):
        module = load('store')
        for index in range(-2000, 2000, 7):
            boundary = math.exp(index * math.log(1.005))
            for value in [boundary, math.nextafter(boundary, math.inf), math.nextafter(boundary, 0)]:
                edge = module._edge(module._bucket(value))
                self.assertGreaterEqual(edge, value)
                self.assertLessEqual(edge, value * 1.01)

    def test_invalid_batch_and_cursor_do_not_commit(self):
        module = load('store')
        with tempfile.TemporaryDirectory() as root:
            store = module.Store(Path(root) / 'store.db')
            for bad in [-1, float('nan'), float('inf'), True, '3', 10 ** 1000]:
                with self.subTest(bad=repr(bad)[:30]):
                    with self.assertRaises(ValueError):
                        store.ingest([sample('valid'), sample('bad', bad)], source='s', cursor=9)
                    self.assertIsNone(store.cursor('s'))
                    self.assertEqual(store.view('tool', 0, 2000000000)['n'], 0)
            with self.assertRaises(ValueError):
                store.ingest([sample('valid')], source='s', cursor=float('nan'))
            self.assertEqual(store.view('tool', 0, 2000000000)['n'], 0)

    def test_concurrent_replay_persistence_and_metadata(self):
        module = load('store')
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'store.db'
            store = module.Store(path)
            events = [sample(i, i) for i in range(120)]
            with ThreadPoolExecutor(max_workers=8) as pool:
                counts = list(pool.map(lambda _: store.ingest(events), range(16)))
            self.assertEqual(sum(counts), len(events))
            store.ingest([], source='s', cursor=[42, 'opaque'])
            store.meta_set('measured_since', {'tool': 123})
            reopened = module.Store(path)
            self.assertEqual(reopened.cursor('s'), [42, 'opaque'])
            self.assertEqual(reopened.meta_get('measured_since'), {'tool': 123})
            self.assertEqual(reopened.meta_get('missing', 3), 3)
            self.assertEqual(reopened.view('tool', 0, 2000000000)['n'], len(events))

    def test_transaction_error_rolls_back_histograms_and_cursor(self):
        module = load('store')
        with tempfile.TemporaryDirectory() as root:
            store = module.Store(Path(root) / 'store.db')
            store.ingest([], source='s', cursor=1)
            with sqlite3.connect(store.path) as db:
                db.execute("CREATE TRIGGER refuse_bad BEFORE INSERT ON hist WHEN NEW.metric='bad' BEGIN SELECT RAISE(ABORT,'refused'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                store.ingest([sample('first'), sample('second', metric='bad')], source='s', cursor=2)
            self.assertEqual(store.cursor('s'), 1)
            self.assertEqual(store.view('tool', 0, 2000000000)['n'], 0)
            self.assertEqual(store.ingest([sample('first')]), 1)

    def test_connections_are_closed_after_each_operation(self):
        module = load('store')
        real_connect = sqlite3.connect
        connections = []
        def connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            connections.append(connection)
            return connection
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root, patch.object(module.sqlite3, 'connect', side_effect=connect):
            store = module.Store(Path(root) / 'store.db')
            store.ingest([sample('one')], source='s', cursor=1)
            store.cursor('s')
            store.meta_set('key', 2)
            store.meta_get('key')
            store.view('tool', 0, 2000000000)
            store.prune(1800000000)
            self.assertEqual(len(connections), 7)
            for connection in connections:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute('SELECT 1')

    def test_la_day_boundaries_and_repeated_dst_hour(self):
        module = load('store')
        stamps = [datetime.fromisoformat(text).timestamp() for text in
                  ['2026-11-01T06:30:00+00:00', '2026-11-01T08:30:00+00:00', '2026-11-01T09:30:00+00:00']]
        with tempfile.TemporaryDirectory() as root:
            store = module.Store(Path(root) / 'store.db')
            store.ingest([sample(i, i, start=stamp) for i, stamp in enumerate(stamps)])
            view = store.view('tool', stamps[0] - 3600, stamps[-1] + 3600)
            self.assertEqual([(x['day'], x['n']) for x in view['series']], [('2026-10-31', 1), ('2026-11-01', 2)])
            self.assertEqual(len(view['hours']), 3)
            self.assertEqual(store.view('missing', 0, 2000000000)['p99'], None)
            self.assertEqual(store.view('tool', stamps[0], stamps[0])['n'], 0)
            with self.assertRaises(ValueError):
                store.view('tool', 2, 1)

    def test_prune_preserves_recent_part_of_boundary_hour(self):
        module = load('store')
        now = 1800000000 + 123
        cutoff = now - module.RETENTION_SECONDS
        with tempfile.TemporaryDirectory() as root:
            store = module.Store(Path(root) / 'store.db')
            store.ingest([sample('old', start=cutoff - 7200), sample('recent', start=cutoff + 1)])
            store.prune(now)
            self.assertEqual(store.view('tool', cutoff, now)['n'], 1)
            self.assertEqual(store.ingest([sample('recent', start=cutoff + 1)]), 0)
            self.assertEqual(store.ingest([sample('old', start=cutoff - 7200)]), 1)
