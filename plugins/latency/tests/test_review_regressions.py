"""Behavioral controls for review findings, without live sources."""
import importlib.util
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('latency_review_fixture', ROOT/'__init__.py', submodule_search_locations=[str(ROOT)])
pkg = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pkg
spec.loader.exec_module(pkg)
from latency_review_fixture import collectors, github, sampler
from latency_review_fixture.store import Store
registry = importlib.import_module('latency_review_fixture.registry')


class ReviewRegressions(unittest.TestCase):
    def test_registry_never_mutates_cached_yaml(self):
        shipped = registry.load_yaml(ROOT/'metrics.yaml')
        original = len(shipped['metrics'])
        local = dict(shipped['metrics'][0], id='custom')
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'latency'
            folder.mkdir()
            path = folder/'metrics.local.yaml'
            path.write_text('')
            with patch.object(registry, 'load_yaml', side_effect=lambda p: shipped if p.name == 'metrics.yaml' else {'metrics':[local]}):
                for _ in range(6):
                    self.assertEqual(len(registry.registry(tmp)), original+1)
                self.assertEqual(len(shipped['metrics']), original)
                path.unlink()
                self.assertEqual(len(registry.registry(tmp)), original)

    def test_missing_pending_run_retires_but_other_errors_hold_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'series.sqlite')
            defs = [dict(id='ci',source='github.example',start='run_started_at',end='updated_at',workflow='ci.yml')]
            key = 'github:example:'+registry.fingerprint(defs)
            store.ingest([], key, {'at':100, 'pending':[42]})
            def fetch(path, **kw):
                if '/runs/42' in path:
                    return github.api(path, run=lambda *a, **k: SimpleNamespace(returncode=1,stdout='',stderr='gh: Not Found (HTTP 404)'))
                return {'total_count':0, 'workflow_runs':[]}
            cfg = {'example':{'repo':'example/repo'}}
            self.assertEqual(github.collect_github(tmp,store,defs,cfg,0,200,fetch=fetch),0)
            self.assertEqual(store.cursor(key),{'at':200,'pending':[]})
            store.ingest([],key,{'at':200,'pending':[42]})
            with self.assertRaises(RuntimeError):
                github.collect_github(tmp,store,defs,cfg,0,300,fetch=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('unavailable')))
            self.assertEqual(store.cursor(key)['at'],200)

    def test_pending_revisit_and_run_attempt_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'series.sqlite')
            defs = [dict(id='ci',source='github.example',start='run_started_at',end='updated_at',workflow='ci.yml')]
            key = 'github:example:'+registry.fingerprint(defs)
            row = dict(id=42,run_attempt=1,path='ci.yml',created_at=github.iso(10),run_started_at=github.iso(10),updated_at=github.iso(20),status='completed',conclusion='success')
            cfg = {'example':{'repo':'example/repo'}}
            def fetch(path, **kw):
                return row if '/runs/42' in path else {'total_count':0,'workflow_runs':[]}
            for attempt in (1,2):
                row['run_attempt'] = attempt
                store.ingest([],key,{'at':200000,'pending':[42]})
                self.assertEqual(github.collect_github(tmp,store,defs,cfg,0,200100,fetch=fetch),1)
            self.assertEqual(store.view('ci',0,200100)['n'],2)

    def test_failed_github_attempt_obeys_ten_minute_cadence(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = sampler.Worker(tmp)
            calls = []
            class Stop:
                n = 0
                def is_set(self): return self.n >= 3
                def wait(self, seconds): self.n += 1
            worker.stop_event = Stop()
            def cycle(*a, **kw):
                calls.append(kw['include_github'])
                return {'errors':{'github':'RuntimeError'}}
            with patch.object(sampler, 'cycle', side_effect=cycle), patch.object(sampler.time,'monotonic',return_value=100):
                worker.run()
            self.assertEqual(calls,[True,False,False])

    def test_lock_contender_retries_after_previous_generation_exits(self):
        import fcntl
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'cache/latency'
            folder.mkdir(parents=True)
            ran = threading.Event()
            worker = sampler.Worker(tmp)
            worker.lock_retry_seconds = .01
            def cycle(*a,**kw):
                ran.set()
                worker.stop_event.set()
                return {'errors':{}}
            with (folder/'collector.lock').open('a+') as lock, patch.object(sampler,'cycle',side_effect=cycle):
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                worker.thread.start()
                self.assertFalse(ran.wait(.1))
                fcntl.flock(lock,fcntl.LOCK_UN)
                self.assertTrue(ran.wait(3))
                worker.stop()
                self.assertFalse(worker.thread.is_alive())

    def test_core_marker_uses_local_timezone_and_rotated_logs(self):
        """Wall-clock conversion must follow the source host's timezone."""
        if not hasattr(time,'tzset'):
            self.skipTest('requires POSIX TZ')
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)/'logs'
            logs.mkdir()
            (logs/'hermes-sync.log.1').write_text('2026-01-01 12:00:00 core: update exit 0: ✓ default (pid 1) @ abcdef12\n')
            before = os.environ.get('TZ')
            try:
                os.environ['TZ'] = 'America/Los_Angeles'
                time.tzset()
                _, markers = github.deployment_markers(tmp,tmp,'core',run=lambda *a,**k: SimpleNamespace(returncode=0,stdout='abcdef12'+'0'*32+'\n'))
                self.assertEqual(markers,[(github.epoch('2026-01-01T20:00:00Z'),0)])
            finally:
                if before is None: os.environ.pop('TZ',None)
                else: os.environ['TZ'] = before
                time.tzset()

    def test_partial_valid_line_and_invalid_row_do_not_poison_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'spans.jsonl'
            store = Store(Path(tmp)/'series.sqlite')
            row = dict(id='a',metric='span',start=100,dur_ms=5,host='h',gateway='g',attrs={})
            path.write_text(json.dumps(row))
            defs = [{'id':'span'}]
            self.assertEqual(collectors.collect_journals([path],store,defs,200),0)
            with path.open('a') as out:
                out.write('\n'+json.dumps({'metric':'span'})+'\n'+json.dumps(dict(row,id='b'))+'\n')
            self.assertEqual(collectors.collect_journals([path],store,defs,200),2)
            self.assertEqual(collectors.collect_journals([path],store,defs,200),0)

    def test_deployment_must_follow_merge(self):
        pr = dict(number=1,createdAt=github.iso(1),mergedAt=github.iso(10),mergeCommit={'oid':'a'},baseRefName='main')
        defs = [dict(id='deploy',start='pr_merged',end='deployed')]
        rows = list(github.github_samples(defs,[],[pr],{'a':0},[(5,0)],'example',0,20))
        self.assertEqual(rows,[])

    def test_stop_interrupts_wait_and_joins_owned_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = sampler.Worker(tmp)
            entered = threading.Event()
            def cycle(*a,**kw):
                entered.set()
                return {'errors':{}}
            with patch.object(sampler,'cycle',side_effect=cycle):
                worker.thread.start()
                try:
                    self.assertTrue(entered.wait(3))
                    worker.stop()
                    self.assertTrue(worker.stop_event.is_set())
                    self.assertFalse(worker.thread.is_alive())
                finally:
                    worker.stop_event.set()
                    worker.thread.join(3)
