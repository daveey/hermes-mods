"""GET /requests, /steps and /session-cards run on core's ``feed`` thread lane (hermes_cli.web_thread_lanes);
a core without lanes keeps the shared pool. Live 2026-11-21 these reads held shared-pool threads for minutes
and starved /api/status on the hub's always-on backend."""
import importlib.util
import asyncio
import threading
import time

import anyio
import httpx


def test_decision_owner_write_and_receipt_bypass_a_saturated_read_pool(setup, monkeypatch):
    api, client = setup
    monkeypatch.setattr(api, 'RETRY_INTERVAL_S', 0)
    app = client().app
    request_id = client().get('/requests', params={'session': 'session-a'}).json()['requests'][0]['requestId']
    ft = load_module('decision_lane_feed_tests', ROOT.parent / 'session-panel/tests/test_feed.py')
    r = ft.Readers()
    r.links = {}; r.finished = {}; r.cards = {}
    st = app.state.action_store
    initial = client().get('/requests/' + request_id, params={'session': 'session-a'}).json()['request']
    def canonical(session, lineage):
        return {'outcome_json': {}, 'requests': st.views(initial['owner'], include_withdrawn=True)}
    r.panel_inputs = r.decision_inputs = canonical
    f = ft.feedmod.Feed(r, clock=lambda: 1000, emit=lambda payload: None)
    f.watch(['session-a'])
    f._publish({'session-a': f.compute('session-a')})
    assert f.bundles['session-a']['bundle']['model']['header']['counts']['needs_you'] == 1
    board_clock = f.bundles['session-a']['bundle']['computed_at']

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            # Warm startup before saturating all shared read capacity.
            assert (await c.get('/requests/' + request_id, params={'session': 'session-a'})).status_code == 200
            limiter = anyio.to_thread.current_default_thread_limiter()
            old = limiter.total_tokens
            limiter.total_tokens = 1
            entered, release = threading.Event(), threading.Event()
            def blocked_read():
                entered.set()
                assert release.wait(5)
            slow = asyncio.create_task(anyio.to_thread.run_sync(blocked_read))
            try:
                while not entered.is_set():
                    await asyncio.sleep(0)
                started = time.monotonic()
                async def decision():
                    response = await c.get('/requests/' + request_id, params={'session': 'session-a'})
                    assert response.status_code == 200
                    view = response.json()['request']
                    posted = await c.post('/requests/' + request_id + '/decisions', json={
                        'session': 'session-a', 'expectedRevision': view['revision'],
                        'choiceId': view['choices'][0]['id'], 'idempotencyKey': 'isolated-lane'})
                    assert posted.status_code == 200, posted.text
                    result = posted.json()
                    assert result['request']['resolution']['operationId'] == result['operation']['operationId']
                    receipt = await c.get('/operations/' + result['operation']['operationId'], params={'session': 'session-a'})
                    assert receipt.status_code == 200
                    # Keep the background decision sweep blocked too: interactive
                    # publication reads only this session and never waits for it.
                    with f._decision_lock:
                        refreshed = f.refresh_decision('session-a', readers=r)
                    assert refreshed['bundle']['model']['header']['counts']['needs_you'] == 0
                    assert refreshed['bundle']['computed_at'] == board_clock
                await asyncio.wait_for(decision(), timeout=0.95)
                assert time.monotonic() - started < 1
                assert not release.is_set()
            finally:
                release.set()
                await slow
                limiter.total_tokens = old
    asyncio.run(exercise())
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


def test_cold_recovery_is_single_flight_not_four_occupied_decision_slots(setup, monkeypatch):
    api, client = setup
    monkeypatch.setattr(api, 'RETRY_INTERVAL_S', 0)
    app = client().app
    st = app.state.action_store
    original = st.pending_owners
    def slow():
        time.sleep(1)
        return original()
    monkeypatch.setattr(st, 'pending_owners', slow)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            responses = await asyncio.gather(*(c.get('/requests/missing', params={'session': 'session-a'}) for _ in range(8)))
            assert [r.status_code for r in responses] == [404] * 8
    asyncio.run(exercise())


def test_decision_lane_bounds_queue_and_keeps_cancelled_worker_capacity(setup):
    api, _ = setup
    async def exercise():
        entered = 0
        release = threading.Event()
        lock = threading.Lock()
        def hold():
            nonlocal entered
            with lock:
                entered += 1
            assert release.wait(5)
        tasks = [asyncio.create_task(api._on_decision(hold)) for _ in range(4)]
        try:
            while entered < 4:
                await asyncio.sleep(0)
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            ran = []
            with pytest.raises(HTTPException) as exc:
                await api._on_decision(lambda: ran.append(True))
            assert exc.value.status_code == 503
            assert exc.value.detail == 'Decision not started: capacity busy'
            assert ran == []
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            # The shielded first worker also must finish before fixture teardown.
            gate, _ = api._DECISION_LIMITERS[asyncio.get_running_loop()]
            while gate._value != 4:
                await asyncio.sleep(0)
    asyncio.run(exercise())


@pytest.fixture
def setup(tmp_path):
    api = load_module('user_steps_lanes_api', ROOT / 'dashboard' / 'plugin_api.py')
    decisions = load_module('user_steps_lanes_decisions', HERE.parent / 'test_decisions.py')
    cards = load_module('user_steps_lanes_cards', ROOT / 'cards.py')
    s = decisions.store(tmp_path)
    s.create(decisions.OWNER, decisions.request())

    def client():
        app = FastAPI()
        app.state.action_store = s
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
