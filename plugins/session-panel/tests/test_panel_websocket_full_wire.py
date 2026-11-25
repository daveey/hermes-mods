"""Full prepared event bytes over real core WS transport and exec child."""
import json
import hashlib
import threading
import time

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

import test_panel_exec_full_wire as full
home = full.home


def test_real_exec_events_over_core_websocket_complete_payload(home, monkeypatch):
    from hermes_cli import serve_producer_runtime as runtime
    from tui_gateway import server
    from tui_gateway import ws as ws_mod
    # Isolate unrelated skin/orphan/heartbeat services, retaining the actual
    # register/broadcast/WSTransport/JSON serialization and handle_ws loop.
    for name in ('_start_backend_heartbeat_refresher', '_schedule_startup_orphan_sweep',
                 '_start_kanban_unloaded_sweeper', '_ensure_skin_watcher', '_ensure_lease_watcher'):
        monkeypatch.setattr(server, name, lambda *a, **k: None)
    monkeypatch.setattr(server, '_WS_ORPHAN_REAP_GRACE_S', 0)
    monkeypatch.setattr(server, '_close_sessions_for_transport', lambda *a, **k: (0, 0))
    monkeypatch.setattr(server, '_release_wake_for_transport', lambda *a, **k: None)
    monkeypatch.setattr(server, 'resolve_skin', dict)
    monkeypatch.setattr(ws_mod, '_note_dashboard_client_activity', lambda *a, **k: None)
    monkeypatch.setattr(ws_mod, '_authority_connection', lambda *a, **k: None)
    monkeypatch.setattr(server, '_live_transports', set())
    ready = threading.Event()
    register = server.register_live_transport
    def enrolled(transport):
        register(transport)
        ready.set()
    monkeypatch.setattr(server, 'register_live_transport', enrolled)
    api = full.load('_ws_full_wire_legacy_api', 'plugin_api.py')
    control = full.helper()
    legacy = full.legacy_feed(api)
    events = []
    legacy.emit = events.append
    with control.clocks():
        legacy.changes_bytes(['chat'])
        legacy._drain()
        legacy.tick()
        expected = api.push_payload(events[-1])
    want = json.dumps(expected, ensure_ascii=False, separators=(',', ':')).encode()
    fixture = full.ROOT / 'tests' / 'controlled_exec_provider.py'
    relay = runtime.ProjectionRelay({'path': str(fixture), 'sha256': hashlib.sha256(fixture.read_bytes()).hexdigest()},
        home=home, runtime=home / 'runtime', generation='ws-full-wire')
    monkeypatch.setattr(api, '_request_profile', lambda: api.LAUNCH_PROFILE)
    app = FastAPI()
    app.include_router(api.router, prefix='/api/plugins/session-panel')
    @app.websocket('/api/ws')
    async def websocket(ws: WebSocket):
        await ws_mod.handle_ws(ws)
    relay.start_for_fixture()
    try:
        with TestClient(app) as client, client.websocket_connect('/api/ws') as ws:
            assert ws.receive_json()['params']['type'] == 'gateway.ready'
            assert ready.wait(5)
            api.attach_external(relay)
            reset = ws.receive_json()
            assert reset['params']['type'] == 'plugin.session-panel.changed'
            assert reset['params']['payload']['reset'] is True
            deadline = time.monotonic() + 30
            while True:
                response = client.post('/api/plugins/session-panel/changes', json={'sessions': ['chat']})
                if response.status_code == 200:
                    break
                assert response.status_code == 503 and response.headers['retry-after'] == '1'
                assert time.monotonic() < deadline
                threading.Event().wait(.05)
            frame = ws.receive_json()
            assert frame['params']['type'] == 'plugin.session-panel.changed'
            assert json.dumps(frame['params']['payload'], ensure_ascii=False, separators=(',', ':')).encode() == want
    finally:
        api.detach_external(relay)
        assert relay.close()
        legacy.readers.release()
        legacy.stopped.set()
