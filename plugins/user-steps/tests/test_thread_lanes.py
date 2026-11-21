"""GET /requests, /steps and /session-cards run on core's ``feed`` thread lane (hermes_cli.web_thread_lanes);
a core without lanes keeps the shared pool. Live 2026-11-21 these reads held shared-pool threads for minutes
and starved /api/status on the hub's always-on backend."""
import importlib.util
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.concurrency import run_in_threadpool

HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def setup(tmp_path):
    api = load_module('user_steps_lanes_api', ROOT / 'dashboard' / 'plugin_api.py')
    decisions = load_module('user_steps_lanes_decisions', HERE.parent / 'test_decisions.py')
    cards = load_module('user_steps_lanes_cards', ROOT / 'cards.py')
    s = decisions.store(tmp_path)
    s.create(decisions.OWNER, decisions.request())

    def client():
        app = FastAPI()
        app.include_router(api.create_router(
            ledger_dir=tmp_path / 'ledger', state_db=tmp_path / 'state.db', action_store=s,
            owner_resolver=lambda session: dict(decisions.OWNER, storedSessionId=session),
            receiver=cards.DecisionInbox(tmp_path / 'inbox.db'), session_tasks=lambda ids: []))
        return TestClient(app)
    return api, client


@pytest.mark.parametrize('lanes', [True, False], ids=['core-lanes', 'core-without-lanes'])
def test_the_polled_reads_run_on_the_feed_lane(setup, monkeypatch, lanes):
    api, client = setup
    seen = []

    async def fake_lane(lane, fn, *args, request=None, deadline=None, **kwargs):
        seen.append((lane, request is not None))
        return await run_in_threadpool(lambda: fn(*args, **kwargs))

    monkeypatch.setattr(api, '_run_in_lane', fake_lane if lanes else None)
    c = client()
    requests = c.get('/requests', params={'session': 'session-a'})
    assert requests.status_code == 200 and len(requests.json()['requests']) == 1
    assert c.get('/steps', params={'session': 'session-a'}).status_code == 200
    cards = c.get('/session-cards', params={'ids': 'session-a'})
    assert cards.status_code == 200 and cards.json() == {'tasks': [], 'includes': ['done', 'archived']}
    assert seen == ([('feed', True)] * 3 if lanes else [])


def test_a_busy_lane_answers_503_with_retry_after(setup, monkeypatch):
    api, client = setup

    async def busy(lane, fn, *args, **kwargs):
        raise HTTPException(503, detail={'error': 'lane_busy', 'lane': lane}, headers={'Retry-After': '2'})

    monkeypatch.setattr(api, '_run_in_lane', busy)
    out = client().get('/steps', params={'session': 'session-a'})
    assert out.status_code == 503 and out.headers['Retry-After'] == '2'
