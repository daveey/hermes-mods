"""Real REST GETs never wait behind a refresh of a completed exact-key view."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_view import load


def settled(cache):
    with cache.condition:
        assert cache.condition.wait_for(lambda: not cache.busy, timeout=3)


def test_get_during_revision_refresh_returns_previous_without_waiting(monkeypatch):
    api = load()
    entered, release = threading.Event(), threading.Event()
    calls = []
    def compute(*params):
        calls.append(params)
        if len(calls) > 1:
            entered.set()
            assert release.wait(5)
        return {'configured': True, 'version': len(calls)}
    monkeypatch.setattr(api, '_lane_view', compute)
    cache = api._shared_views()
    clock = [100.]
    cache.clock = lambda: clock[0]
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client, ThreadPoolExecutor() as pool:
        assert client.get('/v1/lanes?hours=6').json() == {
            'configured': True, 'version': 1, 'stale_seconds': 0.}
        clock[0] += 2
        api._refresh_cursor('new-board-revision')
        try:
            first = pool.submit(client.get, '/v1/lanes?hours=6')
            assert first.result(1).json()['version'] == 1
            assert entered.wait(1)
            for _ in range(5):
                response = pool.submit(client.get, '/v1/lanes?hours=6').result(1)
                assert response.json() == {'configured': True, 'version': 1, 'stale_seconds': 2.}
            assert len(calls) == 2
        finally:
            release.set()
        settled(cache)
        assert client.get('/v1/lanes?hours=6').json()['version'] == 2
        assert 'stale_seconds' not in cache.values[(6., '6h', False)][2]


@pytest.mark.parametrize('other', [(24., '6h', False), (6., '1h', False), (6., '6h', True)])
def test_no_cross_parameter_reuse(monkeypatch, other):
    api = load()
    calls = []
    def compute(*params):
        calls.append(params)
        return {'params': list(params)}
    monkeypatch.setattr(api, '_lane_view', compute)
    api.rest_view(6., '6h', False)
    assert api.rest_view(*other)['params'] == list(other)
    assert calls == [(6., '6h', False), other]


def test_expiry_and_refresh_failure_retain_value_and_allow_retry(monkeypatch):
    api = load()
    cache = api._shared_views()
    clock = [10.]
    cache.clock = lambda: clock[0]
    monkeypatch.setattr(api, '_lane_view', lambda *p: {'version': 1})
    assert api.rest_view(6., '6h', False)['version'] == 1
    def fail(*params):
        raise RuntimeError('fixture')
    monkeypatch.setattr(api, '_lane_view', fail)
    clock[0] += api.CACHE_S
    assert api.rest_view(6., '6h', False)['stale_seconds'] == api.CACHE_S
    settled(cache)
    assert cache.values[(6., '6h', False)][2] == {'version': 1}
    monkeypatch.setattr(api, '_lane_view', lambda *p: {'version': 2})
    api.rest_view(6., '6h', False)
    settled(cache)
    assert api.rest_view(6., '6h', False)['version'] == 2


def test_thread_start_failure_does_not_wedge_previous_view(monkeypatch):
    api = load()
    cache = api._shared_views()
    monkeypatch.setattr(api, '_lane_view', lambda *p: {'version': 1})
    api.rest_view(6., '6h', False)
    api._written()
    def fail(self):
        raise RuntimeError('cannot start')
    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, 'start', fail)
        assert api.rest_view(6., '6h', False)['version'] == 1
        assert not cache.busy
    api.rest_view(6., '6h', False)
    settled(cache)
