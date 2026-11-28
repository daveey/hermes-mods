import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('decision_latency_api', ROOT/'dashboard/plugin_api.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
import importlib
sampler = importlib.import_module(api.pkg().__name__+'.sampler')
decisions = importlib.import_module(api.pkg().__name__+'.decisions')
Store = importlib.import_module(api.pkg().__name__+'.store').Store

METRIC = 'watch.e553cb8abcd5e7f3'
DEFINITION = dict(id=METRIC, label='decision.submit → unblocked', group='Cards & workers',
                  kind='span', source='spans', start='decision.submit', end='unblocked',
                  unit='ms', target_p90=60000)
SECRET = 'PRIVATE-PROMPT-ANSWER-CREDENTIAL'


class DecisionSpans(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = math_floor(time.time()) - 100
        (self.root/'latency').mkdir()
        (self.root/'latency/metrics.local.yaml').write_text(json.dumps({'metrics':[DEFINITION]}))
        (self.root/'latency/sources.json').write_text(json.dumps({'gateway':'gateway','profile':'default'}))
        (self.root/'user-steps').mkdir()
        self.actions = self.root/'user-steps/actions.sqlite3'
        self.board = self.root/'kanban.db'
        with sqlite3.connect(self.actions) as a:
            a.executescript('''CREATE TABLE operations(id TEXT PRIMARY KEY,namespace TEXT,session TEXT,request TEXT,body TEXT);
                CREATE TABLE kanban_projections(namespace TEXT,session TEXT,request_id TEXT,task TEXT,board TEXT,snapshot TEXT);''')
        with sqlite3.connect(self.board) as b:
            b.executescript('''CREATE TABLE tasks(id TEXT);
                CREATE TABLE task_events(id INTEGER PRIMARY KEY,task_id TEXT,kind TEXT,payload TEXT,created_at INTEGER);''')
        self.store = Store(self.root/'cache/latency/series.sqlite')
        # Actual activation cycle; source history must not become new spans.
        self.cycle(self.now)

    def tearDown(self):
        self.temp.cleanup()

    def cycle(self, now):
        return sampler.cycle(self.root, self.store, now, include_github=False)

    def operation(self, oid='operation-1', task='card-1', board='default', gateway='gateway',
                  start=None, remote=False):
        start = self.now+10.75 if start is None else start
        owner = dict(gatewayId=gateway, profile='default', storedSessionId='session-1')
        body = dict(recordedAt=datetime.fromtimestamp(start, timezone.utc).isoformat(), owner=owner,
                    intent={'prompt':SECRET}, decision={'revisionText':SECRET})
        with sqlite3.connect(self.actions) as a:
            a.execute('INSERT INTO operations VALUES (?,?,?,?,?)',
                      (oid,'namespace','session-1',oid+'-request',json.dumps(body)))
            a.execute('INSERT INTO kanban_projections VALUES (?,?,?,?,?,?)',
                      ('namespace','session-1',oid+'-request',task,board,json.dumps({'remote':remote,'ask':SECRET})))

    def event(self, oid='operation-1', task='card-1', kind='unblocked', end=None):
        end = self.now+25 if end is None else end
        with sqlite3.connect(self.board) as b:
            b.execute('INSERT INTO task_events(task_id,kind,payload,created_at) VALUES (?,?,?,?)',
                      (task,kind,json.dumps({'user_steps_projection':{'operationId':oid, 'value':SECRET}}),end))

    def test_collector_http_ledger_and_otlp_roundtrip(self):
        self.operation()
        self.event()
        self.event(end=self.now+26)  # duplicate operation receipt is one outcome
        result = self.cycle(self.now+30)
        self.assertEqual(result['errors'],{})
        self.assertEqual(result['counts']['decisions.0'],1)
        self.assertEqual(self.cycle(self.now+31)['counts']['decisions.0'],0)
        self.store = Store(self.root/'cache/latency/series.sqlite')  # restart
        self.assertEqual(self.cycle(self.now+32)['counts']['decisions.0'],0)
        app = FastAPI()
        app.include_router(api.router)
        api._cache.clear()
        with patch.object(sampler, 'home', return_value=self.root), TestClient(app) as client:
            response = client.get('/v1/series?metric='+METRIC)
            self.assertEqual(response.status_code,200,response.text)
            metric = response.json()['metrics'][0]
            self.assertEqual(metric['n'],1)
            span = metric['slowest'][0]
            self.assertEqual(span['dur_ms'],15000)
            self.assertEqual(span['attrs'], dict(card='card-1',profile='default',board='default',phase='seconds_resolution'))
            self.assertEqual(span['link']['id'],'card-1')
            self.assertNotIn(SECRET,response.text)
            ledger = client.get('/v1/ledger')
            self.assertEqual(ledger.status_code,200,ledger.text)
            self.assertIn(METRIC,ledger.text)
            self.assertNotIn(SECRET,ledger.text)
            otel = importlib.import_module(api.pkg().__name__+'.otel')
            encoded = otel.to_otlp([span])
            back = otel.from_otlp(encoded)
            retry = client.post('/v1/spans',json={'spans':back})
            self.assertEqual(retry.status_code,200,retry.text)
            self.assertEqual(retry.json()['inserted'],0)
        for file in (self.root/'cache/latency').glob('*'):
            if file.is_file():
                self.assertNotIn(SECRET.encode(),file.read_bytes())

    def test_unrelated_partial_archive_foreign_and_remote_do_not_match(self):
        self.operation()
        for oid,task,kind in [('wrong','card-1','unblocked'),('operation-1','wrong','unblocked'),
                              ('operation-1','card-1','archived'),('operation-1','card-1','user_steps_answer')]:
            self.event(oid,task,kind)
        for oid,kwargs in [('foreign',{'gateway':'elsewhere'}),('board',{'board':'other'}),('remote',{'remote':True})]:
            self.operation(oid,**kwargs)
            self.event(oid)
        self.assertEqual(self.cycle(self.now+30)['counts']['decisions.0'],0)

    def test_activation_no_history_delayed_replication_and_fast_spans(self):
        self.operation('historical',start=self.now-1)
        self.event('historical')
        self.event('late')
        self.assertEqual(self.cycle(self.now+30)['counts']['decisions.0'],0)
        self.operation('late')
        self.assertEqual(self.cycle(self.now+31)['counts']['decisions.0'],1)
        self.operation('fast',start=self.now+40.75)
        self.event('fast',end=self.now+40)
        self.assertEqual(self.cycle(self.now+41)['counts']['decisions.0'],1)
        self.operation('negative',start=self.now+50)
        self.event('negative',end=self.now+49)
        self.assertEqual(self.cycle(self.now+60)['counts']['decisions.0'],0)

    def test_missing_actions_does_not_block_other_sources_or_move_floor(self):
        self.actions.unlink()
        self.assertEqual(self.cycle(self.now+5)['errors'],{})
        self.assertEqual(decisions.definitions_for([dict(DEFINITION,source='kanban')]),[])


def math_floor(value):
    import math
    return math.floor(value)
