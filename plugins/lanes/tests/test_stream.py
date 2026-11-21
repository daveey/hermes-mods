import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from test_view import load


def test_stream_status_change_heartbeat_and_disconnect(monkeypatch):
    api = load()
    state = {'status': 'ready', 'cursor': 0}
    monkeypatch.setattr(api, '_ws_upgrade_authorized', lambda ws: True, raising=False)
    monkeypatch.setattr(api, 'change_cursor', lambda: state['cursor'], raising=False)
    monkeypatch.setattr(api, 'lane_view', lambda *args: {'status': state['status'], 'args': list(args)}, raising=False)
    monkeypatch.setattr(api, 'HEARTBEAT_S', .15, raising=False)
    monkeypatch.setattr(api, 'WATCH_S', .02, raising=False)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        with client.websocket_connect('/v1/lanes/stream') as ws:
            assert ws.receive_json()['view']['status'] == 'ready'
            # An unchanged board emits no redundant view, just a heartbeat.
            assert ws.receive_json()['type'] == 'heartbeat'
            start = time.monotonic()
            state.update(status='running', cursor=1)
            frame = ws.receive_json()
            while frame.get('type') == 'heartbeat':
                assert time.monotonic() - start < 3
                frame = ws.receive_json()
            assert frame['view']['status'] == 'running'
            assert time.monotonic() - start < 3
            # Leave a heartbeat queued before the parameter response. Clients
            # must handle it even when the parameter projection is fast.
            time.sleep(.2)
            ws.send_json({'hours': 24, 'window': '24h', 'expanded': True})
            start = time.monotonic()
            frame = ws.receive_json()
            assert frame['type'] == 'heartbeat'
            while frame.get('type') == 'heartbeat':
                assert time.monotonic() - start < 3
                frame = ws.receive_json()
            assert frame['view']['args'] == [24, '24h', True]
        async def active():
            await asyncio.sleep(.05)
            return [t.get_name() for t in asyncio.all_tasks() if t.get_name().startswith('lanes-')]
        assert client.portal.call(active) == []


def test_disconnect_leaves_no_async_tasks(monkeypatch):
    api = load()
    monkeypatch.setattr(api, '_ws_upgrade_authorized', lambda ws: True)
    monkeypatch.setattr(api, 'change_cursor', lambda: 0)
    monkeypatch.setattr(api, 'lane_view', lambda *args: {'configured': False})
    class Socket:
        query_params = {}
        async def accept(self):
            pass
        async def send_json(self, body):
            pass
        async def receive(self):
            return {'type': 'websocket.disconnect'}
    async def run():
        baseline = asyncio.all_tasks()
        await api.stream_lanes(Socket())
        await asyncio.sleep(0)
        assert asyncio.all_tasks() == baseline
    asyncio.run(run())


def test_changed_cursor_with_identical_view_does_not_push(monkeypatch):
    api = load()
    state = {'cursor': 0}
    monkeypatch.setattr(api, '_ws_upgrade_authorized', lambda ws: True)
    monkeypatch.setattr(api, 'change_cursor', lambda: state['cursor'])
    monkeypatch.setattr(api, 'lane_view', lambda *args: {'configured': False})
    monkeypatch.setattr(api, 'WATCH_S', .01)
    monkeypatch.setattr(api, 'HEARTBEAT_S', .1)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        with client.websocket_connect('/v1/lanes/stream') as ws:
            assert ws.receive_json()['version'] == 1
            state['cursor'] += 1
            assert ws.receive_json() == {'type': 'heartbeat', 'version': 1}


def test_unauthorized_upgrade_closes_1008(monkeypatch):
    api = load()
    monkeypatch.setattr(api, '_ws_upgrade_authorized', lambda ws: False, raising=False)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as err:
            with client.websocket_connect('/v1/lanes/stream'):
                pass
        assert err.value.code == 1008
