import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('latency_api_fixture', ROOT/'dashboard/plugin_api.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class API(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        package = api.pkg()
        import importlib
        self.sampler = importlib.import_module(package.__name__+'.sampler')
        self.mock = patch.object(self.sampler, 'home', return_value=self.root)
        self.mock.start()
        api._cache.clear()
        app = FastAPI()
        app.include_router(api.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.mock.stop()
        self.temp.cleanup()

    def test_all_registry_metrics_nulls_post_and_retry(self):
        response = self.client.get('/v1/series?range=7d')
        self.assertEqual(response.status_code,200,response.text)
        metrics = response.json()['metrics']
        self.assertEqual(len(metrics),20)
        self.assertTrue(all(m['p90'] is None and m['n']==0 and m['measured_since'] for m in metrics))
        body = {'spans':[dict(metric='ui.session_switch',start=time.time()-60,dur_ms=123,host='desktop',gateway='gateway',attrs={'session':'s'})]}
        response = self.client.post('/v1/spans',json=body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['inserted'],1)
        self.assertEqual(self.client.post('/v1/spans',json=body).json()['inserted'],0)
        metric = self.client.get('/v1/series?metric=ui.session_switch').json()['metrics'][0]
        self.assertEqual(metric['n'],1)
        self.assertGreaterEqual(metric['p90'],123)
        self.assertLess(metric['p90'],124)
        for value in ('24h','7d','30d','90d'):
            self.assertEqual(self.client.get('/v1/series?range='+value).status_code,200)

    def test_malformed_spans_are_400_and_write_nothing(self):
        for body in ({},[],{'spans':[]},{'spans':[{}]}, {'spans':[{'metric':'turn_first'}]}, {'spans':[{'metric':'ui.session_switch','body':'private'}]}):
            response = self.client.post('/v1/spans',json=body)
            self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(self.client.post('/v1/spans',content='{').status_code,400)
        self.assertEqual(self.client.post('/v1/spans',content=' '*300000).status_code,400)
        self.assertEqual(list(self.root.glob('cache/latency/spans*')),[])
        self.assertEqual(self.client.get('/v1/series?range=forever').status_code,400)
        self.assertEqual(self.client.get('/v1/series?metric=unknown').status_code,400)

    def test_body_cap_metric_allowlist_and_nested_json(self):
        good = dict(metric='ui.session_switch',start=time.time()-1,dur_ms=1,host='desktop',gateway='gateway',attrs={})
        raw = json.dumps({'spans':[good]}) + ' '*api.MAX_BODY
        self.assertEqual(self.client.post('/v1/spans',content=raw).status_code,400)
        for metric in ('unknown', 'turn_first'):
            self.assertEqual(self.client.post('/v1/spans',json={'spans':[dict(good,metric=metric)]}).status_code,400)
        self.assertEqual(self.client.post('/v1/spans',content='['*130000+'0'+']'*130000).status_code,400)

    def test_watch_post_validation_cache_and_instrumentation_failure(self):
        import importlib
        watch = importlib.import_module(api.pkg().__name__+'.watch')
        definition = watch.propose(describe='decision_requested -> unblocked')['definition']
        self.client.get('/v1/series') # warm old inventory
        for body in ({}, [], {'definition':{**definition,'extra':'private'}}, {'definition':{**definition,'target_p90':False}}):
            self.assertEqual(self.client.post('/v1/watch',json=body).status_code,400)
        self.assertEqual(self.client.post('/v1/watch',content='['*4200).status_code,400)
        response=self.client.post('/v1/watch',json={'definition':definition})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['added'])
        self.assertFalse(self.client.post('/v1/watch',json={'definition':definition}).json()['added'])
        self.assertIn(definition['id'],{d['id'] for d in self.client.get('/v1/series').json()['metrics']})
        self.assertEqual(self.client.post('/v1/watch',json={'definition':{**definition,'target_p90':3}}).status_code,400)
        span=watch.propose(describe='decision.submit -> unblocked')['definition']
        with patch.object(watch,'instrumentation_card',side_effect=RuntimeError('private failure')):
            response=self.client.post('/v1/watch',json={'definition':span})
        self.assertEqual(response.status_code,503)
        self.assertNotIn('private',response.text)
        self.assertNotIn(span['id'],{d['id'] for d in self.client.get('/v1/series').json()['metrics']})

    def test_watch_target_boundaries_are_400_without_registry_mutation(self):
        import importlib
        watch = importlib.import_module(api.pkg().__name__+'.watch')
        good = watch.propose(describe='decision_requested -> unblocked')['definition']
        path = self.root/'latency/metrics.local.yaml'
        invalid = (10**1000, -(10**1000), True, False, float('nan'),
                   float('inf'), -float('inf'), 0, -1, 90*86400000+1)
        for seeded in (False, True):
            if seeded:
                self.assertEqual(self.client.post('/v1/watch',json={'definition':good}).status_code,200)
            before = path.read_bytes() if path.exists() else None
            for target in invalid:
                with self.subTest(seeded=seeded, target=repr(target)):
                    # Raw JSON also exercises nonfinite values disallowed by httpx's encoder.
                    raw = json.dumps({'definition':{**good, 'target_p90':target}})
                    self.assertLess(len(raw.encode()),4096)
                    response = self.client.post('/v1/watch',content=raw,
                                                headers={'Content-Type':'application/json'})
                    self.assertEqual(response.status_code,400,response.text)
                    self.assertEqual(path.read_bytes() if path.exists() else None,before)
        for index,target in enumerate((1,1.5,90*86400000,float(90*86400000))):
            definition = {**good,'id':'watch.boundary.'+str(index),'target_p90':target}
            response = self.client.post('/v1/watch',json={'definition':definition})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['definition']['target_p90'],target)

    def test_15_second_cache_has_single_build(self):
        with patch.object(api,'build',return_value={'metrics':[]}) as build:
            self.assertEqual(api.cached('7d'),api.cached('7d'))
            self.assertEqual(build.call_count,1)


if __name__ == '__main__':
    unittest.main()
