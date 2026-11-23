"""Bounded bootstrap and stale-view delivery through the actual router."""
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_view import load
from test_stale_get import settled


def test_router_activation_warms_default_and_cold_get_is_bounded(monkeypatch):
    api = load()
    entered, release = threading.Event(), threading.Event()
    calls = []
    def compute(*params):
        calls.append(params)
        entered.set()
        assert release.wait(10)
        return {'configured': True, 'params': list(params)}
    monkeypatch.setattr(api, '_lane_view', compute)
    app = FastAPI()
    handle = api.start_legacy()
    app.include_router(api.router)
    try:
        with TestClient(app) as client:
            assert entered.wait(1)  # startup, before any GET
            assert calls == [(6., '6h', False)]
            start = time.monotonic()
            response = client.get('/v1/lanes?hours=24&window=1h&expanded=true')
            assert time.monotonic() - start < 5
            assert response.status_code == 200
            assert response.json() == {'configured': True, 'warming': True}
    finally:
        release.set()
        settled(api._shared_views())
        handle.close()
    assert api.rest_view(24., '1h', True)['params'] == [24., '1h', True]


def test_real_mount_warms_with_custom_lifespan(monkeypatch):
    mount = pytest.importorskip('hermes_cli.fact_plugin_mount')
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def lifespan(app):
        yield
    api = load()
    entered = threading.Event()
    monkeypatch.setattr(api, '_lane_view', lambda *p: (entered.set() or {'configured': True}))
    app = FastAPI(lifespan=lifespan)
    mount.include_router(app, api, api.__file__)
    try:
        assert entered.wait(1)  # mount itself, no startup or request required
        settled(api._shared_views())
    finally:
        mount.lifecycles(app).close()


@pytest.mark.parametrize('failed', [False, True])
def test_refresh_never_serves_past_max_age(monkeypatch, caplog, failed):
    api = load()
    cache = api._shared_views()
    clock = [10.]
    cache.clock = lambda: clock[0]
    monkeypatch.setattr(api, '_lane_view', lambda *p: {'version': 1})
    assert api.rest_view(6., '6h', False)['version'] == 1
    entered, release = threading.Event(), threading.Event()
    def compute(*p):
        entered.set()
        if failed:
            raise RuntimeError('refresh fixture')
        assert release.wait(5)
        return {'version': 2}
    monkeypatch.setattr(api, '_lane_view', compute)
    # No epoch/cursor change: age alone must start refresh.
    clock[0] += 121
    try:
        assert api.rest_view(6., '6h', False) == {'configured': True, 'warming': True}
        assert entered.wait(1)
        if failed:
            settled(cache)
            assert 'lanes view refresh failed' in caplog.text
    finally:
        release.set()
        settled(cache)
    monkeypatch.setattr(api, '_lane_view', lambda *p: {'version': 3})
    api.rest_view(6., '6h', False)
    settled(cache)
    assert api.rest_view(6., '6h', False)['stale_seconds'] < 120
