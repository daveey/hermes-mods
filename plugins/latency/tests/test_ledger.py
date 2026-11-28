import importlib
from unittest.mock import patch
from test_api import API, api
import time


class Ledger(API):
    def test_server_groups_count_worst_and_eight_calendar_days(self):
        for metric, duration in [('ui.session_switch', 501), ('ui.new_session', 1000), ('ui.card_read', 3000)]:
            response = self.client.post('/v1/spans', json={'spans': [dict(metric=metric, start=time.time()-10, dur_ms=duration, host='desktop', gateway='gateway', attrs={})]})
            self.assertEqual(response.status_code, 200, response.text)
        response = self.client.get('/v1/ledger?range=30d')
        self.assertEqual(response.status_code, 200, response.text)
        view = response.json()
        self.assertEqual([g['label'] for g in view['groups']], ['Using Hermes', 'Cards & workers', 'Ship'])
        self.assertEqual(view['chip']['over_target_count'], 3)  # histogram upper edges: 1000ms > 2x500
        self.assertEqual(len(view['chip']['spark']), 8)
        self.assertTrue(all(p['p90'] is None for p in view['chip']['spark']))
        self.assertEqual(view['chip']['worst'][0]['id'], 'ui.card_read')
        self.assertTrue(all(r['status'] == 'none' for g in view['groups'] for r in g['rows'] if not r['n']))
        self.assertEqual(self.client.get('/v1/ledger?range=forever').status_code, 400)

    def test_socket_fails_closed_without_canonical_auth(self):
        from starlette.websockets import WebSocketDisconnect
        # A bare fixture has no dashboard canonical authentication module.
        with patch.dict('sys.modules', {'hermes_cli.web_server_chat': None}):
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect('/v1/events'):
                    self.fail('unauthorized socket accepted')

    def test_hub_span_yields_joinable_context(self):
        spans = importlib.import_module(api.pkg().__name__+'.spans')
        with patch.object(spans, '_home', return_value=self.root):
            with spans.span('ui.card_read', trace_id='a'*32, parent_span_id='b'*16, card='card-1') as context:
                self.assertEqual(context['trace_id'], 'a'*32)
                self.assertRegex(context['span_id'], r'^[0-9a-f]{16}$')
        import json
        value = json.loads(next(self.root.glob('cache/latency/spans-*.jsonl')).read_text())
        self.assertEqual(value['trace_id'], 'a'*32)
        self.assertEqual(value['parent_span_id'], 'b'*16)
        self.assertEqual(value['span_id'], context['span_id'])
