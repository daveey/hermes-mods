"""Cheap route-boundary controls alongside the separate real-exec parity fixtures."""
import sys
import threading
import types

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

pytest.importorskip('hermes_cli.serve_producer_runtime')
import test_panel_startup_unavailable as startup_helpers

api = startup_helpers.api


@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('path', ['/changes', '/status', '/decision-refresh'])
def test_http_selects_only_its_ready_external_or_legacy_owner(api, monkeypatch, external, path):
    from hermes_cli import serve_producer_runtime as runtime
    relay = object()
    calls = []
    def project(actual, command, **kwargs):
        assert actual is relay
        calls.append((command, kwargs))
        return Response(b'{"ready":true}', media_type='application/json')
    monkeypatch.setattr(runtime, 'projection_response', project)
    monkeypatch.setattr(api._LOADER, 'external_projection', relay if external else None, raising=False)
    monkeypatch.setattr(api._LOADER, 'legacy_fenced', False, raising=False)
    monkeypatch.setattr(api._LOADER, 'panel_lifecycle', types.SimpleNamespace(state='off', _legacy_running=True), raising=False)
    monkeypatch.setattr(api._LOADER, 'facts_registration', None, raising=False)
    legacy = types.SimpleNamespace(
        stopped=threading.Event(), _facts_mode=True, clock=lambda: 0,
        _fact_subscription=types.SimpleNamespace(status_wire=b'{"ready":true}', decision_wires={'chat': (b'{"ready":true}', None)}),
        changes_bytes=lambda *args, **kwargs: b'{"ready":true}')
    monkeypatch.setitem(sys.modules, api._FEED_KEY, types.SimpleNamespace(PROCESS_FEED=None if external else legacy))
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        response = client.get(path) if path == '/status' else client.post(path, json={'sessions': []} if path == '/changes' else {'session': 'chat'})
    assert response.status_code == 200 and response.json()['ready'] is True
    assert bool(calls) is external
    if external:
        assert calls[0][0]['_wire_identity'] == {'feed': api.FEED_ID, 'profile': api.LAUNCH_PROFILE}
        assert calls[0][1] == ({'consume': True} if path == '/decision-refresh' else {})


@pytest.mark.parametrize(('status', 'wire', 'expected', 'detail'), [
    (503, b'{"detail":"transport pending"}', 503, 'Session panel decision pending'),
    (200, b'{"_conflict":"not hydrated"}', 409, 'not hydrated'),
    (200, b'{"ready":true}', 200, None),
])
def test_external_decision_response_classification_is_not_masked_by_transport(api, monkeypatch, status, wire, expected, detail):
    from hermes_cli import serve_producer_runtime as runtime
    monkeypatch.setattr(api, '_bound_external', lambda request: object())
    monkeypatch.setattr(runtime, 'projection_response', lambda *args, **kwargs: Response(wire, status_code=status, media_type='application/json'))
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        response = client.post('/decision-refresh', json={'session': 'chat'})
    assert response.status_code == expected
    if detail is not None:
        assert response.json() == {'detail': detail}
    if expected == 503:
        assert response.headers['Retry-After'] == '1'
