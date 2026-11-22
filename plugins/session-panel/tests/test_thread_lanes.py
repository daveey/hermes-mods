"""Published reads bypass both core lanes and the shared pool."""
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
def test_changes_never_enters_the_feed_lane(api, monkeypatch, lanes):
    seen = []

    async def fake_lane(lane, fn, *args, request=None, deadline=None, **kwargs):
        seen.append((lane, request is not None))
        return await run_in_threadpool(lambda: fn(*args, **kwargs))

    monkeypatch.setattr(api, '_run_in_lane', fake_lane if lanes else None)
    api._load_feed().PROCESS_FEED.changes(['S1'])  # producer, before request
    out = _client(api).post('/api/plugins/session-panel/changes', json={'sessions': ['S1']})
    assert out.status_code == 200 and set(out.json()['bundles']) == {'S1'}
    assert seen == []


def test_a_busy_lane_cannot_delay_a_published_read(api, monkeypatch):
    async def busy(lane, fn, *args, **kwargs):
        raise HTTPException(503, detail={'error': 'lane_busy', 'lane': lane}, headers={'Retry-After': '2'})

    monkeypatch.setattr(api, '_run_in_lane', busy)
    out = _client(api).post('/api/plugins/session-panel/changes', json={'sessions': ['S1']})
    assert out.status_code == 200 and out.json()['bundles'] == {}


def test_decision_refresh_route_publishes_compact_canonical_model_and_releases_reader(api, monkeypatch):
    steps = load('_spf_interactive_steps_api', HERE.parents[2] / 'user-steps' / 'dashboard' / 'plugin_api.py')
    released = []
    BaseReaders = api.CoreReaders

    class InteractiveReaders(BaseReaders):
        _user_step_api = staticmethod(lambda: steps)

        def decision_inputs(self, session, lineage):
            return {'outcome_json': None, 'requests': []}

        def release(self):
            released.append(True)

    monkeypatch.setattr(api, 'CoreReaders', InteractiveReaders)
    feed = api._load_feed().PROCESS_FEED
    feed.changes(['S1'])
    before = feed.bundles['S1']['bundle']['computed_at']
    response = _client(api).post('/api/plugins/session-panel/decision-refresh', json={'session': 'S1'})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload['bundle']) == {'session', 'lineage', 'model', 'tasks'}
    assert payload['bundle']['session'] == 'S1'
    assert payload['bundle']['model']['version'] == 1
    assert set(payload['bundle']['tasks'][0]) <= {'id', 'gateway', 'profile', 'board'}
    assert feed.bundles['S1']['bundle']['computed_at'] == before
    assert released == [True]
    cold = _client(api).post('/api/plugins/session-panel/decision-refresh', json={'session': 'not-hydrated'})
    assert cold.status_code == 409
    assert released == [True, True]
