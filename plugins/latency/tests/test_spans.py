"""Span capture and privacy boundary tests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
import os
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch


def event(index=0):
    return dict(id='event-' + str(index), metric='tool', start=time.time(), dur_ms=index,
                host='host', gateway='gateway', attrs={})


def process_writer(root, index):
    spans = load()
    spans.append_batch([event(index * 100 + i) for i in range(10)], root=root)


def load():
    import sys
    root = Path(__file__).parents[1]
    name = 'latency_spans_package_test'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, root / '__init__.py', submodule_search_locations=[str(root)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return importlib.import_module(name + '.spans')


class SpanTests(unittest.TestCase):
    def test_batch_normalization_retry_and_all_or_nothing_validation(self):
        spans = load()
        value = dict(metric='tool', start=time.time(), dur_ms=12, host='host', gateway='gateway', attrs={'session': 'abc'})
        with tempfile.TemporaryDirectory() as root:
            one = spans.append_batch([value], root=root)
            two = spans.append_batch([value], root=root)
            self.assertEqual(one[0]['id'], two[0]['id'])
            files = list((Path(root) / 'cache' / 'latency').glob('*.jsonl'))
            self.assertEqual(len(files), 1)
            before = files[0].read_bytes()
            with self.assertRaises(ValueError):
                spans.append_batch([value, {**value, 'attrs': {'body': 'secret'}}], root=root)
            self.assertEqual(files[0].read_bytes(), before)
            self.assertEqual(json.loads(before.splitlines()[0]), one[0])

    def test_validation_numeric_time_bounds_and_privacy(self):
        spans = load()
        now = time.time()
        value = {**event(), 'start': now}
        for field in ['start', 'dur_ms']:
            for bad in [True, False, float('nan'), float('inf'), '12', None, 10 ** 1000]:
                with self.subTest(field=field, bad=repr(bad)[:30]):
                    with self.assertRaises(ValueError):
                        spans.validate_span({**value, field: bad}, now=now)
        for updates in [dict(start=now - 3600.01), dict(start=now + 60.01),
                        dict(dur_ms=-1), dict(dur_ms=spans.MAX_DURATION_MS + 1),
                        dict(host='a' * 101), dict(host='https://user:password@host?token=x'),
                        dict(gateway='gateway?secret'), dict(metric='tool/query'),
                        dict(id='id?secret'), dict(id=None), dict(body='private'),
                        dict(attrs={'session': 'https://host?secret'}), dict(attrs={'session': 'a@b'}),
                        dict(attrs={'query': 'private'}), dict(attrs={'phase': 'two words'}),
                        dict(attrs={'pr': 123}), dict(attrs={'run': 'x' * 257})]:
            with self.subTest(updates=updates):
                with self.assertRaises(ValueError):
                    spans.validate_span({**value, **updates}, now=now)
        for start in [now - 3600, now + 60]:
            self.assertEqual(spans.validate_span({**value, 'start': start, 'dur_ms': 0}, now=now)['dur_ms'], 0)
        valid = spans.validate_span({**value, 'dur_ms': spans.MAX_DURATION_MS,
                                     'attrs': {'phase': 'queue_wait', 'pr': '123'}}, now=now)
        self.assertEqual(valid['id'], value['id'])

    def test_rotation_global_bound_and_expired_cleanup(self):
        spans = load()
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / 'cache' / 'latency'
            folder.mkdir(parents=True)
            expired = folder / 'spans-20000101.jsonl'
            expired.write_text('old')
            os.utime(expired, (0, 0))
            with patch.object(spans, 'MAX_FILE_BYTES', 600):
                for i in range(30):
                    spans.append_batch([event(i)], root=root)
                files = list(folder.glob('*.jsonl'))
                self.assertEqual(len(files), 3)
                self.assertTrue(all(p.stat().st_size <= 600 for p in files))
                self.assertFalse(expired.exists())
                lines = [json.loads(line) for p in files for line in p.read_text().splitlines()]
                self.assertIn('event-29', [line['id'] for line in lines])
                with self.assertRaises(ValueError):
                    spans.append_batch([event(i) for i in range(100)], root=root)
            self.assertEqual(spans.append_batch([], root=root), [])

    def test_process_and_thread_writes_remain_complete_jsonl(self):
        spans = load()
        with tempfile.TemporaryDirectory() as root:
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda i: spans.append_batch([event(i)], root=root), range(80)))
            context = multiprocessing.get_context('spawn')
            workers = [context.Process(target=process_writer, args=(root, i + 10)) for i in range(4)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(20)
                if worker.is_alive():
                    worker.kill()
                    worker.join()
                    self.fail('writer timed out')
                self.assertEqual(worker.exitcode, 0)
            files = list((Path(root) / 'cache' / 'latency').glob('*.jsonl'))
            lines = [json.loads(line) for path in files for line in path.read_text().splitlines()]
            self.assertEqual(len(lines), 120)
            self.assertEqual(len({line['id'] for line in lines}), 120)

    def test_context_monotonic_capture_home_and_exception_preservation(self):
        spans = load()
        with tempfile.TemporaryDirectory() as root:
            with patch.object(spans, '_home', return_value=Path(root)), \
                 patch.object(spans.time, 'time', return_value=1000), \
                 patch.object(spans.time, 'monotonic', side_effect=[10.5, 10.625]):
                with spans.span('tool', session='session-1', phase='execution'):
                    pass
            path = next((Path(root) / 'cache' / 'latency').glob('*.jsonl'))
            captured = json.loads(path.read_text())
            self.assertEqual(captured['dur_ms'], 125)
            self.assertEqual(captured['start'], 1000)
            self.assertEqual(captured['attrs'], {'session': 'session-1', 'phase': 'execution'})
            with patch.object(spans, 'append_batch', side_effect=OSError('secret text')):
                with self.assertLogs(spans._LOG, level='WARNING') as logs:
                    with self.assertRaisesRegex(RuntimeError, '^body failure$'):
                        with spans.span('tool'):
                            raise RuntimeError('body failure')
                self.assertNotIn('secret', ' '.join(logs.output))
            with patch.dict('sys.modules', {'hermes_constants': None}), patch.dict(os.environ, {'HERMES_HOME': root}):
                self.assertEqual(spans._home(), Path(root))

    def test_fsync_failure_rolls_back_batch(self):
        spans = load()
        with tempfile.TemporaryDirectory() as root:
            spans.append_batch([event(1)], root=root)
            path = next((Path(root) / 'cache' / 'latency').glob('*.jsonl'))
            before = path.read_bytes()
            with patch.object(spans.os, 'fsync', side_effect=[OSError('disk failure'), None]):
                with self.assertRaises(OSError):
                    spans.append_batch([event(2), event(3)], root=root)
            self.assertEqual(path.read_bytes(), before)
