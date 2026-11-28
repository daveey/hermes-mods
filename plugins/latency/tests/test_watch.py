"""Chat tool -> Add -> one collector cycle -> panel, in isolated homes."""
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from test_api import api


class Watch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.watch = importlib.import_module(api.pkg().__name__+'.watch')
        self.sampler = importlib.import_module(api.pkg().__name__+'.sampler')
        self.registry = importlib.import_module(api.pkg().__name__+'.registry')

    def tearDown(self):
        self.temp.cleanup()

    def test_tool_proposal_never_writes_and_registers(self):
        proposal = json.loads(self.watch.handle({'describe': 'decision_requested → unblocked'}))
        self.assertEqual(proposal['definition']['kind'], 'derived')
        self.assertIn('::latency-watch{definition="', proposal['directive'])
        self.assertEqual(proposal['history'], 'Backfill last 30 days within one collector cycle')
        self.assertFalse((self.root/'latency/metrics.local.yaml').exists())
        span = json.loads(self.watch.handle({'describe': 'decision.submit → unblocked'}))
        self.assertEqual(span['definition']['kind'], 'span')
        self.assertIn('error', json.loads(self.watch.handle({'describe': 'slow'})))
        class Context:
            def register_tool(_, **kw): self.tool = kw
        with patch.object(self.sampler, 'eligible_process', return_value=False):
            api.pkg().register(Context())
        self.assertEqual(self.tool['name'], 'latency_watch')

    def test_thirty_day_history_in_one_cycle_and_span_link(self):
        now = 1800000000
        db = sqlite3.connect(self.root/'kanban.db')
        db.executescript('CREATE TABLE tasks(id TEXT); CREATE TABLE task_events(id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, created_at REAL);')
        for day in range(30):
            start = now-day*86400-120
            db.execute('INSERT INTO tasks VALUES (?)', (str(day),))
            db.executemany('INSERT INTO task_events VALUES (?,?,?,?)', [(day*2+1,str(day),'decision_requested',start),(day*2+2,str(day),'unblocked',start+10)])
        db.commit(); db.close()
        derived = self.watch.propose(describe='decision_requested → unblocked')['definition']
        self.watch.add(self.root, derived, now=now)
        cycle = self.sampler.cycle(self.root, now=now, include_github=False)
        self.assertEqual(cycle['errors'], {})
        with patch.object(self.sampler, 'home', return_value=self.root):
            data = api.build('30d', now=now)
            ledger = importlib.import_module(api.pkg().__name__+'.ledger').build(data, data, data)
        row = next(r for g in ledger['groups'] for r in g['rows'] if r['id']==derived['id'])
        self.assertEqual(row['n'], 30)
        self.assertEqual(len(row['series']), 30)
        self.assertAlmostEqual(row['p90'], 10000, delta=50)
        self.assertEqual(self.sampler.cycle(self.root, now=now, include_github=False)['counts']['kanban.0'],0)
        span = self.watch.propose(describe='decision.submit → unblocked')['definition']
        calls = []
        def file_card(definition):
            calls.append(definition)
            return dict(id='t_instrument', board='default', gateway='hub', profile='default')
        one = self.watch.add(self.root, span, now=now, file_card=file_card)
        two = self.watch.add(self.root, span, now=now+1, file_card=file_card)
        self.assertEqual(one['definition'],two['definition'])
        self.assertEqual(len(calls),1)
        self.assertEqual(one['definition']['instrumentation_card']['id'],'t_instrument')
        with patch.object(self.sampler,'home',return_value=self.root):
            row = api.build('30d',span['id'],now=now)['metrics'][0]
        self.assertEqual(row['n'],0)
        self.assertIsNone(row['p90'])
        self.assertTrue(row['collection_label'].startswith('collecting from '))
        self.assertEqual(row['measured_since'],now)
        self.assertEqual(len(self.registry.load_yaml(self.root/'latency/metrics.local.yaml')['metrics']),2)

    def test_invalid_definitions_conflicts_and_card_failure_do_not_mutate(self):
        good = self.watch.propose(describe='decision_requested -> unblocked')['definition']
        for changes in ({'target_p90':True},{'target_p90':float('nan')},{'unit':'s'}, {'group':'invisible'}, {'source':'shell'}, {'start':'invented'}, {'body':'private'}, {'id':'turn_first'}):
            with self.assertRaises(ValueError): self.watch.add(self.root,{**good,**changes})
        span = self.watch.propose(describe='decision.submit -> unblocked')['definition']
        def fail(_): raise RuntimeError('cannot file')
        with self.assertRaises(RuntimeError): self.watch.add(self.root,span,file_card=fail)
        self.assertFalse((self.root/'latency/metrics.local.yaml').exists())
        self.watch.add(self.root,good)
        before = (self.root/'latency/metrics.local.yaml').read_bytes()
        with self.assertRaises(ValueError): self.watch.add(self.root,{**good,'target_p90':12})
        self.assertEqual((self.root/'latency/metrics.local.yaml').read_bytes(),before)

    def test_target_boundaries_tool_and_add_leave_registry_unchanged(self):
        good = self.watch.propose(describe='decision_requested -> unblocked')['definition']
        path = self.root/'latency/metrics.local.yaml'
        invalid = (10**1000, -(10**1000), True, False, float('nan'),
                   float('inf'), -float('inf'), 0, -1, 90*86400000+1)
        # Both a missing registry and an existing registry must remain untouched.
        for seeded in (False, True):
            if seeded:
                self.watch.add(self.root, good)
            before = path.read_bytes() if path.exists() else None
            for target in invalid:
                with self.subTest(seeded=seeded, target=repr(target)):
                    definition = {**good, 'target_p90':target}
                    result = json.loads(self.watch.handle({'definition':definition}))
                    self.assertIn('error', result)
                    with self.assertRaises(ValueError):
                        self.watch.add(self.root, definition)
                    self.assertEqual(path.read_bytes() if path.exists() else None, before)
        for target in (1, 1.5, 90*86400000, float(90*86400000)):
            with self.subTest(valid_target=target):
                result = json.loads(self.watch.handle({'definition':{**good, 'target_p90':target}}))
                self.assertEqual(result['definition']['target_p90'], target)
        self.assertEqual(path.read_bytes(), before)

    def test_span_label_stops_on_arrival_and_uses_original_add_date(self):
        Store=importlib.import_module(api.pkg().__name__+'.store').Store
        now=1800000000
        d=self.watch.propose(describe='decision.submit -> unblocked')['definition']
        self.watch.add(self.root,d,now=now,file_card=lambda _:{'id':'t_one','board':'default','gateway':'hub','profile':'default'})
        with patch.object(self.sampler,'home',return_value=self.root):
            first=api.build('7d',d['id'],now=now)['metrics'][0]
            later=api.build('7d',d['id'],now=now+86400)['metrics'][0]
        self.assertEqual(first['collection_label'],'collecting from today')
        self.assertNotIn('today',later['collection_label'])
        store=Store(self.root/'cache/latency/series.sqlite')
        store.ingest([dict(id='measured',metric=d['id'],start=now,dur_ms=123,host='test',gateway='hub',attrs={})])
        with patch.object(self.sampler,'home',return_value=self.root):
            present=api.build('7d',d['id'],now=now+1)['metrics'][0]
            outside=api.build('24h',d['id'],now=now+2*86400)['metrics'][0]
        self.assertIsNone(present['collection_label'])
        self.assertIsNone(outside['collection_label'])

    def test_core_instrumentation_creation_is_linked_and_deduped(self):
        try:
            from hermes_cli import kanban_db as kb
            from hermes_cli import kanban_db_connect as kbc
        except ImportError:
            self.skipTest('core creation API not installed')
        import os
        from types import SimpleNamespace
        # This fixture owns a disposable board only; no production publication.
        with patch.dict(os.environ, {'HERMES_HOME':str(self.root), 'HERMES_KANBAN_DB':str(self.root/'kanban.db'), 'HERMES_KANBAN_BOARD':'default', 'HERMES_HUB_STATE':str(self.root/'hub-state')}):
            (self.root/'config.yaml').write_text('terminal:\n  backend: local\nplatform_toolsets:\n  cli: [terminal, file, kanban]\n')
            (self.root/'latency').mkdir(exist_ok=True)
            (self.root/'latency/sources.json').write_text(json.dumps({'gateway':'fixture-hub','profile':'default'}))
            kb.init_db()
            with patch('hermes_cli.profiles.list_profiles',return_value=[SimpleNamespace(name='default')]):
                d=self.watch.propose(describe='decision.submit -> unblocked')['definition']
                replace=self.watch.os.replace
                def crash_registry(source,target):
                    if Path(target)==self.root/'latency/metrics.local.yaml':
                        raise OSError('simulated publication crash')
                    return replace(source,target)
                with patch.object(self.watch.os,'replace',side_effect=crash_registry):
                    with self.assertRaises(OSError): self.watch.add(self.root,d)
                self.assertFalse((self.root/'latency/metrics.local.yaml').exists())
                with kbc.connect_closing() as conn:
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],1)
                one=self.watch.add(self.root,d)
                two=self.watch.add(self.root,d)
            with kbc.connect_closing() as conn:
                task=kb.get_task(conn,one['definition']['instrumentation_card']['id'])
                self.assertIn(d['id'],task.body)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],1)
            self.assertEqual(one['definition'],two['definition'])

    def test_concurrent_add_and_unknown_metadata_rejected(self):
        from concurrent.futures import ThreadPoolExecutor
        d = self.watch.propose(describe='decision.submit -> unblocked')['definition']
        calls=[]
        def create(_): calls.append(1); return dict(id='t_one',board='default',gateway='hub',profile='default')
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows=list(pool.map(lambda _:self.watch.add(self.root,d,file_card=create),range(8)))
        self.assertEqual(len(calls),1)
        self.assertEqual(sum(r['added'] for r in rows),1)
        with self.assertRaises(ValueError): self.watch.add(self.root,{**d,'instrumentation_card':{'id':'t_fake'}})
