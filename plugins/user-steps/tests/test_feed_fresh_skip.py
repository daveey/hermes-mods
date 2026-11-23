"""The Desktop request poll skips the remote card pass while the feed's strict pass is fresh. [daveey]

Hub 2026-11-23: GET /requests re-ran the remote pass on every poll and waited on the linked-card projection
(3-7 s) though the session panel's feed already runs that strict pass for every watched chat."""

from types import SimpleNamespace

import pytest

from test_withdrawal import ROOT, OWNER, load_module

mirror_mod = load_module('feed_fresh_mirror', ROOT / 'kanban_mirror.py')


@pytest.fixture(autouse=True)
def clean():
    mirror_mod._REMOTE_FRESH.clear()
    yield
    mirror_mod._REMOTE_FRESH.clear()


def fake(remote_result=None, local_clean=True):
    remote_calls, local_calls = [], []

    def remote(owner):
        remote_calls.append(owner)
        return remote_result

    def local(owner, strict):
        local_calls.append((owner, strict))
        if strict and not local_clean:
            raise RuntimeError('Card reconciliation deferred')

    self = SimpleNamespace(core=SimpleNamespace(validate_owner=lambda owner: None),
                           remote=SimpleNamespace(reconcile=remote), _reconcile_local=local)
    self.reconcile = lambda owner, **kw: mirror_mod.Mirror.reconcile(self, owner, **kw)
    return self, remote_calls, local_calls


def test_poll_skips_remote_after_a_clean_strict_pass_but_runs_local():
    self, remote, local = fake()
    mirror_mod.Mirror.reconcile(self, OWNER, strict=True)  # the feed producer
    assert len(remote) == 1
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)  # the poll
    assert len(remote) == 1  # skipped
    assert local[-1] == (OWNER, False)  # local still ran


def test_poll_without_a_feed_pass_runs_everything():
    self, remote, local = fake()
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)
    assert remote == [OWNER] and local == [(OWNER, False)]


def test_a_deferred_or_failed_strict_pass_is_not_fresh():
    for result, clean in ((False, True), (None, False)):
        mirror_mod._REMOTE_FRESH.clear()
        self, remote, _ = fake(remote_result=result, local_clean=clean)
        with pytest.raises(RuntimeError):
            mirror_mod.Mirror.reconcile(self, OWNER, strict=True)
        mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)
        assert len(remote) == 2  # the poll ran the remote pass itself


def test_a_non_strict_pass_never_marks_fresh():
    self, remote, _ = fake()
    mirror_mod.Mirror.reconcile(self, OWNER)
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)
    assert len(remote) == 2


def test_freshness_expires_and_is_per_owner(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(mirror_mod.time, 'monotonic', lambda: clock[0])
    self, remote, _ = fake()
    mirror_mod.Mirror.reconcile(self, OWNER, strict=True)
    other = dict(OWNER, storedSessionId='session-b')
    mirror_mod.Mirror.reconcile_poll(self, other, 15)
    assert remote[-1] == other  # another owner is not covered
    clock[0] += 15.5
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)
    assert remote[-1] == OWNER and len(remote) == 3  # expired: ran again


def test_requests_route_skips_remote_only_when_fresh(tmp_path):
    pytest.importorskip('fastapi')
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_kanban_mirror import fixture

    mirror, store, path, _hr = fixture(tmp_path)
    calls = []
    mirror.remote.reconcile = calls.append  # no remote cards; counts passes without touching core's stores
    api = load_module('feed_fresh_api', ROOT / 'dashboard' / 'plugin_api.py')
    api.RETRY_INTERVAL_S = 0
    app = FastAPI()
    app.include_router(api.create_router(
        ledger_dir=tmp_path / 'ledger', state_db=tmp_path / 'missing.db', action_store=store,
        owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid), kanban_boards=[('default', path)],
        kanban_mirror=mirror))
    client = TestClient(app)
    first = client.get('/requests', params={'session': 'session-a'})
    assert first.status_code == 200 and len(calls) == 1  # no feed pass yet: full reconcile
    mirror.reconcile(dict(OWNER, storedSessionId='session-a'), strict=True)  # the feed's strict pass
    before = len(calls)
    second = client.get('/requests', params={'session': 'session-a'})
    assert second.status_code == 200
    assert len(calls) == before  # fresh: the poll skipped the remote pass
    assert second.json()['requests'] == first.json()['requests']


def test_queue_admission_reports_busy_before_desktop_timeout(tmp_path, monkeypatch):
    """A saturated feed must answer 503, not spend Desktop's 10s budget queued.

    Use the real core lane, held until teardown. The 2s watchdog only bounds the
    test: old code waits the lane's 25s default and hits it without reading asks.
    """
    import asyncio
    import threading
    import httpx
    from fastapi import FastAPI
    lanes = pytest.importorskip('hermes_cli.web_thread_lanes')
    from test_kanban_mirror import fixture
    mirror, store, path, _hr = fixture(tmp_path)
    api = load_module('queue_admission_api', ROOT / 'dashboard' / 'plugin_api.py')
    api.RETRY_INTERVAL_S = 0
    monkeypatch.setattr(api, '_run_in_lane', lanes.run_in_lane)
    app = FastAPI()
    router = api.create_router(
        ledger_dir=tmp_path / 'ledger', state_db=tmp_path / 'missing.db', action_store=store,
        owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid),
        kanban_boards=[('default', path)], kanban_mirror=mirror)
    app.include_router(router)
    reads = []
    mirror.reconcile_poll = lambda *args: reads.append(args)

    async def scenario():
        # Isolate this module's lane table, never a production app's configuration.
        monkeypatch.setattr(lanes, '_lanes', {})
        monkeypatch.setattr(lanes, '_deadline_s', None)
        lanes.configure(dict(lanes.DEFAULT_SIZES, feed=1), queue_deadline_s=25)
        entered, release = threading.Event(), threading.Event()
        def busy():
            entered.set()
            assert release.wait(5), 'test failed to release the occupied lane'
        holder = asyncio.create_task(lanes.run_in_lane('feed', busy))
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                try:
                    response = await asyncio.wait_for(client.get('/requests?session=session-a'), 2)
                except TimeoutError:
                    pytest.fail('Action queue timed out waiting for feed capacity instead of returning actionable 503')
                assert response.status_code == 503
                assert response.json()['detail']['error'] == 'lane_busy'
                assert response.headers['retry-after']
                assert reads == []  # refused work never starts later
                release.set()
                await holder
                recovered = await client.get('/requests?session=session-a')
                assert recovered.status_code == 200
                assert isinstance(recovered.json()['requests'], list)
                assert len(reads) == 1
        finally:
            release.set()
            await holder  # join before mocks/stores are restored
            router.stop_delivery()
    asyncio.run(scenario())


def test_a_failed_strict_pass_clears_an_earlier_fresh_mark():
    self, remote, _ = fake()
    mirror_mod.Mirror.reconcile(self, OWNER, strict=True)  # clean: fresh
    failing, failing_remote, _ = fake(remote_result=False)  # the next feed pass defers
    with pytest.raises(RuntimeError):
        mirror_mod.Mirror.reconcile(failing, OWNER, strict=True)
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 60)
    assert len(remote) == 2  # the poll ran the remote pass itself


def test_a_failed_local_strict_pass_clears_an_earlier_fresh_mark():
    self, remote, _ = fake()
    mirror_mod.Mirror.reconcile(self, OWNER, strict=True)
    failing, _, _ = fake(local_clean=False)
    with pytest.raises(RuntimeError):
        mirror_mod.Mirror.reconcile(failing, OWNER, strict=True)
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 60)
    assert len(remote) == 2
