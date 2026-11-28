import importlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from test_api import API, api
from test_turn_stages import complete


class TurnRoutes(API):
    def spans(self):
        offset = time.time_ns()-1_500_000_000
        result = []
        for row in complete():
            start = int(row['start_time_unix_nano'])+offset
            end = int(row['end_time_unix_nano'])+offset
            result.append(dict(name=row['metric'], trace_id=row['trace_id'], span_id=row['span_id'],
                               **({'parent_span_id': row['parent_span_id']} if 'parent_span_id' in row else {}),
                               start_time_unix_nano=str(start), end_time_unix_nano=str(end),
                               attributes={'service.name': 'hermes-desktop' if row['metric'] in ('turn.send', 'turn.painted') else 'hermes-gateway',
                                           'host.name': 'fixture', 'hermes.gateway': 'fixture', 'hermes.session_id': 'fixture-turn'}))
        return result

    def test_routes_join_partial_arrival_replay_and_no_histogram_pollution(self):
        spans = self.spans()
        for row in reversed(spans):
            response = self.client.post('/v1/spans', json={'spans': [row]})
            self.assertEqual(response.status_code, 200, response.text)
        response = self.client.get('/v1/series?metric=turn_first').json()['metrics'][0]
        self.assertEqual(response['n'], 0)
        self.assertTrue(response['turn_trace']['complete'])
        self.assertEqual(response['turn_trace']['accounted_ms'], 100)
        self.assertEqual(self.client.post('/v1/spans', json={'spans': spans}).json()['inserted'], 0)
        rows = [r for g in self.client.get('/v1/ledger').json()['groups'] for r in g['rows']]
        self.assertEqual(next(r for r in rows if r['id'] == 'turn_first')['stages'], response['stages'])

    def test_missing_routes_pruning_and_journal_recovery(self):
        spans = self.spans()
        self.assertEqual(self.client.post('/v1/spans', json={'spans': spans[:2]}).status_code, 200)
        view = self.client.get('/v1/series?metric=turn_first').json()['metrics'][0]
        self.assertFalse(view['turn_trace']['complete'])
        self.assertEqual([s['dur_ms'] for s in view['stages']], [10, None, None, None])
        turns = importlib.import_module(api.pkg().__name__+'.turn_stages')
        bridge = importlib.import_module(api.pkg().__name__+'.spans')
        collectors = importlib.import_module(api.pkg().__name__+'.collectors')
        _, _, store, _ = api.services()
        path = self.root/'stages.jsonl'
        path.write_text(''.join(json.dumps(bridge.validate_span(s))+'\n' for s in spans[2:]))
        collectors.collect_journals([path], store, [], time.time())
        self.assertTrue(turns.latest(store, 0, time.time())['complete'])
        collectors.collect_journals([path], store, [], time.time())
        turns.ingest(store, [], time.time()+3601)
        self.assertIsNone(turns.latest(store, 0, time.time()+3601)['trace_id'])

    def test_actual_exporter_delivers_one_trace_with_root_parent_links(self):
        received = []
        arrived = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.end_headers()
                arrived.set()
            def log_message(self, format, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        exporter = importlib.import_module(api.pkg().__name__+'.exporter')
        try:
            url = f'http://127.0.0.1:{server.server_port}/v1/traces'
            with patch.object(exporter, 'configured_url', return_value=url):
                response = self.client.post('/v1/spans', json={'spans': self.spans()})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(arrived.wait(3), 'OTLP stub did not receive actual exporter request')
            exported = [s for r in received for resource in r['resourceSpans'] for scope in resource['scopeSpans'] for s in scope['spans']]
            self.assertEqual(len(exported), 5)
            self.assertEqual({s['traceId'] for s in exported}, {'a'*32})
            root = next(s for s in exported if s['name'] == 'turn.send')
            self.assertFalse(root.get('parentSpanId'))
            self.assertEqual({s['parentSpanId'] for s in exported if s is not root}, {root['spanId']})
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)
