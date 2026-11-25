"""Default REST delivery stays fresh across idle intervals, without stale extension."""
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_view import load
from test_stale_get import settled


def test_default_rest_is_ready_after_five_minutes_without_requests(monkeypatch):
    api = load()
    cache = api._shared_views()
    clock = [0.]
    cache.clock = lambda: clock[0]
    # Accelerate only the lifecycle's wait; snapshot validity stays unchanged.
    monkeypatch.setattr(api, 'REST_REFRESH_S', .01, raising=False)
    completed = threading.Semaphore(0)
    calls = []

    def compute(*params):
        calls.append(params)
        completed.release()
        return {'configured': True, 'version': len(calls)}

    monkeypatch.setattr(api, '_lane_view', compute)
    app = FastAPI()
    app.include_router(api.router)
    handle = api.start_legacy()
    try:
        assert completed.acquire(timeout=2)
        for _ in range(6):
            settled(cache)
            clock[0] += 61.
            assert completed.acquire(timeout=2), 'one-shot startup warm expired without requests'
        settled(cache)
        with TestClient(app) as client:
            response = client.get('/v1/lanes')
        assert response.status_code == 200
        assert not response.json().get('warming')
        assert response.json()['stale_seconds'] < 120
        assert all(params == (6., '6h', False) for params in calls)
    finally:
        handle.close()
    assert len(calls) == 7
    assert not handle.worker.is_alive()


def test_blocked_scheduled_compute_keeps_expiry_and_shutdown_custody(monkeypatch):
    import pytest
    api = load()
    cache = api._shared_views()
    clock = [0.]
    cache.clock = lambda: clock[0]
    monkeypatch.setattr(api, 'REST_REFRESH_S', .01)
    first, blocked, release = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def compute(*params):
        calls.append(params)
        if len(calls) == 1:
            first.set()
        else:
            blocked.set()
            assert release.wait(10)
        return {'version': len(calls)}

    monkeypatch.setattr(api, '_lane_view', compute)
    handle = api.start_legacy()
    try:
        assert first.wait(2)
        settled(cache)
        clock[0] = 61.
        assert blocked.wait(2)
        clock[0] = 301.
        assert api.rest_view(6., '6h', False) == {'configured': True, 'warming': True}
        assert len(calls) == 2  # GET cannot bypass the occupied default-key owner.
        with pytest.raises(RuntimeError, match='still draining'):
            handle.close()
        assert handle.worker.is_alive()
    finally:
        release.set()
        handle.close()
    assert not handle.worker.is_alive()
    assert len(calls) == 2


def test_default_warmer_retries_failure_and_close_stops_future_work(monkeypatch, caplog):
    api = load()
    assert api.REST_REFRESH_S == 60.
    monkeypatch.setattr(api, 'REST_REFRESH_S', .01)
    completed = threading.Event()
    calls = []

    def compute(*params):
        calls.append(params)
        if len(calls) == 1:
            raise RuntimeError('controlled compute failure')
        completed.set()
        return {'configured': True}

    monkeypatch.setattr(api, '_lane_view', compute)
    handle = api.start_legacy()
    try:
        assert completed.wait(2)
    finally:
        handle.close()
    assert len(calls) == 2
    assert not handle.worker.is_alive()
    assert 'default lanes warm refresh failed' in caplog.text
