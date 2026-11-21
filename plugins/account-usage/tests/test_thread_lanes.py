"""The bar's /forecast runs on core's ``control`` thread lane and /selection on the short-deadline ``probe`` lane
(hermes_cli.web_thread_lanes); a core without lanes keeps the shared pool. Live 2026-11-21: /forecast timed out
behind slow session reads and the bar's sparklines turned into dots."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.concurrency import run_in_threadpool


@pytest.fixture
def api():
    path = Path(__file__).parents[1] / 'dashboard' / 'plugin_api.py'
    spec = importlib.util.spec_from_file_location('account_usage_lanes_api', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _client(api):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.selection_service] = lambda: SimpleNamespace(get=lambda sid: {'session_id': sid})
    return TestClient(app)


@pytest.mark.parametrize('lanes', [True, False], ids=['core-lanes', 'core-without-lanes'])
def test_forecast_on_control_and_selection_on_probe(api, monkeypatch, lanes):
    seen = []

    async def fake_lane(lane, fn, *args, request=None, deadline=None, **kwargs):
        seen.append((lane, request is not None, deadline))
        return await run_in_threadpool(lambda: fn(*args, **kwargs))

    monkeypatch.setattr(api, '_run_in_lane', fake_lane if lanes else None)
    monkeypatch.setattr(api, 'forecasts', lambda body: {'posted': len(body.providers)})
    c = _client(api)
    assert c.post('/forecast', json={'providers': []}).json() == {'posted': 0}
    assert c.get('/selection', params={'session_id': 'runtime-A'}).json() == {'session_id': 'runtime-A'}
    assert seen == ([('control', True, None), ('probe', True, api._SELECTION_DEADLINE_S)] if lanes else [])


def test_a_busy_lane_answers_503_with_retry_after(api, monkeypatch):
    async def busy(lane, fn, *args, **kwargs):
        raise HTTPException(503, detail={'error': 'lane_busy', 'lane': lane}, headers={'Retry-After': '2'})

    monkeypatch.setattr(api, '_run_in_lane', busy)
    out = _client(api).post('/forecast', json={'providers': []})
    assert out.status_code == 503 and out.headers['Retry-After'] == '2'
