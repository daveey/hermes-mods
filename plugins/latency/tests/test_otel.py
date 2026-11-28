"""Grafana-ready acceptance through real dashboard routes and a local HTTP stub."""
import importlib
import json
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from test_api import API, api


class OpenTelemetry(API):
    def sample(self):
        start = time.time_ns()-1_000_000_000
        return dict(trace_id='a'*32, span_id='b'*16, parent_span_id='c'*16,
                    name='ui.session_switch', start_time_unix_nano=str(start),
                    end_time_unix_nano=str(start+123_000_000),
                    attributes={'host.name': 'desktop', 'service.name': 'hermes-desktop',
                                'hermes.session_id': 'session-1'})

    def test_json_routes_identical_stored_sample_and_replay(self):
        otel = importlib.import_module(api.pkg().__name__+'.otel')
        spans = importlib.import_module(api.pkg().__name__+'.spans')
        sample = self.sample()
        canonical = spans.validate_span(sample)
        response = self.client.post('/v1/otlp/traces', json=otel.to_otlp([canonical]))
        self.assertEqual(response.status_code, 200, response.text)
        first = self.client.get('/v1/series?metric=ui.session_switch').json()['metrics'][0]
        response = self.client.post('/v1/spans', json={'spans': [sample]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['inserted'], 0)
        response = self.client.post('/v1/spans', json={'spans': [canonical]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['inserted'], 0)
        second = self.client.get('/v1/series?metric=ui.session_switch').json()['metrics'][0]
        self.assertEqual(first['slowest'], second['slowest'])
        self.assertEqual(first['n'], 1)
        self.assertEqual(first['slowest'][0]['trace_id'], sample['trace_id'])
        self.assertEqual(first['slowest'][0]['parent_span_id'], sample['parent_span_id'])

    def test_legacy_otlp_round_trip_preserves_retry_identity(self):
        otel = importlib.import_module(api.pkg().__name__+'.otel')
        sample = dict(id='legacy-replay', metric='ui.session_switch', start=time.time()-1,
                      dur_ms=123, host='desktop', gateway='gateway', attrs={})
        first = self.client.post('/v1/spans', json={'spans': [sample]})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()['inserted'], 1)
        view = self.client.get('/v1/series?metric=ui.session_switch').json()['metrics'][0]
        canonical = view['slowest'][0]
        replay = self.client.post('/v1/otlp/traces', json=otel.to_otlp([canonical]))
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()['inserted'], 0)
        self.assertEqual(self.client.post('/v1/spans', json={'spans': [sample]}).json()['inserted'], 0)
        final = self.client.get('/v1/series?metric=ui.session_switch').json()['metrics'][0]
        self.assertEqual(final['n'], view['n'])
        self.assertEqual(final['slowest'], view['slowest'])

    def test_export_start_failure_cannot_fail_ingestion_or_keep_stale_cache(self):
        exporter = importlib.import_module(api.pkg().__name__+'.exporter')
        before = self.client.get('/v1/series?metric=ui.session_switch').json()['metrics'][0]['n']
        original_start = threading.Thread.start
        def start(thread):
            if thread.name == 'latency-otlp':
                raise RuntimeError("can't start new thread")
            return original_start(thread)
        with patch.object(exporter, 'configured_url', return_value='http://localhost:4318/v1/traces'), \
             patch.object(exporter, '_worker', None), \
             patch.object(threading.Thread, 'start', start):
            response = self.client.post('/v1/spans', json={'spans': [self.sample()]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['inserted'], 1)
        after = self.client.get('/v1/series?metric=ui.session_switch').json()['metrics'][0]['n']
        self.assertEqual(after, before+1)
        self.assertEqual(self.client.post('/v1/spans', json={'spans': [self.sample()]}).status_code, 200)

    def test_export_preparation_failures_are_best_effort(self):
        exporter = importlib.import_module(api.pkg().__name__+'.exporter')
        spans = importlib.import_module(api.pkg().__name__+'.spans')
        sample = spans.validate_span(self.sample())
        # No real worker/collector is started in these fault controls.
        with patch.object(exporter, 'configured_url', return_value='http://localhost:4318/v1/traces'):
            for target, name in ((exporter, 'to_otlp'), (exporter.threading, 'Thread')):
                with self.subTest(fault=name), patch.object(exporter, '_worker', None), \
                     patch.object(target, name, side_effect=RuntimeError('fault')):
                    self.assertFalse(exporter.enqueue([sample]))
            class Alive:
                def is_alive(self):
                    return True
            with patch.object(exporter, '_worker', Alive()), \
                 patch.object(exporter._queue, 'put_nowait', side_effect=RuntimeError('fault')):
                self.assertFalse(exporter.enqueue([sample]))

    def test_prometheus_every_sampled_metric(self):
        try:
            from prometheus_client.parser import text_string_to_metric_families
        except ImportError:
            self.skipTest('optional parser: run uv run --with prometheus-client --with fastapi --with httpx --with pyyaml --with pytest python -m pytest plugins/latency/tests/test_otel.py')
        response = self.client.post('/v1/spans', json={'spans': [self.sample()]})
        self.assertEqual(response.status_code, 200, response.text)
        _, registry, store, root = api.services()
        store.ingest([dict(id='prom-'+entry['id'], metric=entry['id'], start=time.time()-1,
                           dur_ms=123, host='fixture', gateway='gateway', attrs={})
                      for entry in registry.registry(root) if entry['id'] != 'ui.session_switch'])
        response = self.client.get('/v1/metrics')
        self.assertEqual(response.status_code, 200, response.text)
        families = list(text_string_to_metric_families(response.text))
        samples = [sample for family in families for sample in family.samples]
        for entry in registry.registry(root):
            if store.view(entry['id'], time.time()-86400, time.time())['n']:
                self.assertTrue(any(s.labels.get('metric') == entry['id'] for s in samples))
        count = next(s for s in samples if s.name == 'hermes_latency_seconds_count')
        self.assertEqual(count.value, 1)
        self.assertEqual(next(s for s in samples if s.name == 'hermes_latency_seconds_bucket' and s.labels['le'] == '+Inf').value, 1)
        self.assertGreaterEqual(next(s for s in samples if s.name == 'hermes_latency_seconds_sum').value, .123)
        self.assertLess(next(s for s in samples if s.name == 'hermes_latency_seconds_sum').value, .124)
        self.assertTrue(any(s.name == 'hermes_latency_p90_seconds' and s.labels['range'] == '24h' for s in samples))

    def test_export_opt_in_and_bounded_queue(self):
        exporter = importlib.import_module(api.pkg().__name__+'.exporter')
        seen, arrived = [], threading.Event()
        class Stub(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.end_headers()
                arrived.set()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            sample = self.sample()
            with patch.object(exporter, 'configured_url', return_value=''):
                self.assertEqual(self.client.post('/v1/spans', json={'spans': [sample]}).status_code, 200)
                self.assertFalse(seen)
                self.assertFalse(exporter._queue.qsize())
            with patch.object(exporter, 'configured_url', return_value=f'http://127.0.0.1:{server.server_port}/v1/traces'):
                sample['span_id'] = 'd'*16
                self.assertEqual(self.client.post('/v1/spans', json={'spans': [sample]}).status_code, 200)
                self.assertTrue(arrived.wait(3), 'stub received no span')
            self.assertEqual(seen[0]['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['traceId'], sample['trace_id'])
            self.assertGreater(exporter._queue.maxsize, 0)
            self.assertLessEqual(exporter._queue.maxsize, 64)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_otlp_replay_id_is_still_privacy_validated(self):
        for event_id in ('private answer text', 'https://example.com/private', '', None, 123, 'x'*1025):
            sample = self.sample()
            sample['attributes']['hermes.event_id'] = event_id
            with self.subTest(event_id=event_id):
                self.assertEqual(self.client.post('/v1/spans', json={'spans': [sample]}).status_code, 400)

    def test_private_fields_invalid_ids_and_malformed_otlp_rejected(self):
        for change in ({'trace_id': '0'*32}, {'span_id': 'wrong'}, {'parent_span_id': 'f'*32},
                       {'attributes': {'body': 'private'}}, {'end_time_unix_nano': '1'},
                       {'start_time_unix_nano': True}):
            self.assertEqual(self.client.post('/v1/spans', json={'spans': [{**self.sample(), **change}]}).status_code, 400)
        for body in ({}, {'resourceSpans': []}, {'resourceSpans': [None]}, {'resourceSpans': [{'scopeSpans': [{}]}]}):
            self.assertEqual(self.client.post('/v1/otlp/traces', json=body).status_code, 400)
        self.assertEqual(self.client.post('/v1/otlp/traces', content=' '*300000).status_code, 400)
        self.assertEqual(list(self.root.glob('cache/latency/spans*')), [])
