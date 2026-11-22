"""POST /changes runs on core's ``feed`` thread lane (hermes_cli.web_thread_lanes); a core without lanes keeps
the shared pool. Live 2026-11-21: /changes held shared-pool threads for up to 926 s and starved /api/status."""
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.concurrency import run_in_threadpool

HERE = Path(__file__).resolve()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def api(monkeypatch):
    api = load('_spf_lanes_api', HERE.parents[1] / 'dashboard' / 'plugin_api.py')
    fakes = load('_spf_lanes_fakes', HERE.parent / 'test_feed.py')
    monkeypatch.setattr(api, 'CoreReaders', fakes.Readers)
    core = api._load_feed()
    core.PROCESS_FEED = core.Feed(fakes.Readers(), emit=lambda *_: None)
    try:
        yield api
    finally:
        core.PROCESS_FEED.stopped.set()
        lane = getattr(core.PROCESS_FEED, '_decision_thread', None)
        if lane:
            lane.join(2)
        core.PROCESS_FEED = None


def _client(api):
    app = FastAPI()
    app.include_router(api.router, prefix='/api/plugins/session-panel')
    return TestClient(app)


@pytest.mark.parametrize('lanes', [True, False], ids=['core-lanes', 'core-without-lanes'])
def test_changes_runs_on_the_feed_lane(api, monkeypatch, lanes):
    seen = []

    async def fake_lane(lane, fn, *args, request=None, deadline=None, **kwargs):
        seen.append((lane, request is not None))
        return await run_in_threadpool(lambda: fn(*args, **kwargs))

    monkeypatch.setattr(api, '_run_in_lane', fake_lane if lanes else None)
    out = _client(api).post('/api/plugins/session-panel/changes', json={'sessions': ['S1']})
    assert out.status_code == 200 and set(out.json()['bundles']) == {'S1'}
    # The request goes along so a waiter whose client left is dropped.
    assert seen == ([('feed', True)] * 2 if lanes else [])  # getting the feed (a resume may wait) and the read


def test_a_busy_lane_answers_503_with_retry_after(api, monkeypatch):
    async def busy(lane, fn, *args, **kwargs):
        raise HTTPException(503, detail={'error': 'lane_busy', 'lane': lane}, headers={'Retry-After': '2'})

    monkeypatch.setattr(api, '_run_in_lane', busy)
    out = _client(api).post('/api/plugins/session-panel/changes', json={'sessions': ['S1']})
    assert out.status_code == 503 and out.headers['Retry-After'] == '2'
