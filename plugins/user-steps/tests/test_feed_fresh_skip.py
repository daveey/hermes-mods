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
    mirror_mod._LOCAL_SEEN.clear()
    yield
    assert mirror_mod._background_idle(5), 'background remote pass still running'
    mirror_mod._REMOTE_FRESH.clear()
    mirror_mod._LOCAL_SEEN.clear()


def idle():
    assert mirror_mod._background_idle(5), 'background remote pass did not finish'


def fake(remote_result=None, local_clean=True):
    remote_calls, local_calls = [], []

    def remote(owner):
        remote_calls.append(owner)
        return remote_result

    def local(owner, strict, *, force_full=False):
        local_calls.append((owner, strict))
        if strict and not local_clean:
            raise RuntimeError('Card reconciliation deferred')

    self = SimpleNamespace(core=SimpleNamespace(validate_owner=lambda owner: None),
                           remote=SimpleNamespace(reconcile=remote), _reconcile_local=local)
    self.reconcile = lambda owner, **kw: mirror_mod.Mirror.reconcile(self, owner, **kw)
    self._reconcile_remote = lambda owner: mirror_mod.Mirror._reconcile_remote(self, owner)
    return self, remote_calls, local_calls


def test_poll_skips_remote_after_a_clean_strict_pass_but_runs_local():
    self, remote, local = fake()
    mirror_mod.Mirror.reconcile(self, OWNER, strict=True)  # the feed producer
    assert len(remote) == 1
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)  # the poll
    idle()  # a warm owner's local pass runs in the background (test_warm_poll_answers_from_the_store)
    assert len(remote) == 1  # skipped
    assert local[-1] == (OWNER, False)  # local still ran


def test_poll_without_a_feed_pass_runs_local_now_and_remote_in_background():
    self, remote, local = fake()
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)
    assert local == [(OWNER, False)]
    idle()
    assert remote == [OWNER]


def test_a_deferred_or_failed_strict_pass_is_not_fresh():
    for result, clean in ((False, True), (None, False)):
        mirror_mod._REMOTE_FRESH.clear()
        self, remote, _ = fake(remote_result=result, local_clean=clean)
        with pytest.raises(RuntimeError):
            mirror_mod.Mirror.reconcile(self, OWNER, strict=True)
        mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)
        idle()
        assert len(remote) == 2  # the poll queued the remote pass


def test_a_non_strict_pass_never_marks_fresh():
    self, remote, _ = fake()
    mirror_mod.Mirror.reconcile(self, OWNER)
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)
    idle()
    assert len(remote) == 2


def test_freshness_expires_and_is_per_owner(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(mirror_mod.time, 'monotonic', lambda: clock[0])
    self, remote, _ = fake()
    mirror_mod.Mirror.reconcile(self, OWNER, strict=True)
    other = dict(OWNER, storedSessionId='session-b')
    mirror_mod.Mirror.reconcile_poll(self, other, 15)
    idle()
    assert remote[-1] == other  # another owner is not covered
    clock[0] += 15.5
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)
    idle()
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
    # The fixture mirror comes from its own copy of the module: wait on that copy's background worker.
    assert type(mirror).reconcile_poll.__globals__['_background_idle'](5)
    assert first.status_code == 200 and len(calls) == 1  # no feed pass yet: the poll queued the remote pass
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
    idle()
    assert len(remote) == 2  # the poll queued the remote pass


def test_a_failed_local_strict_pass_clears_an_earlier_fresh_mark():
    self, remote, _ = fake()
    mirror_mod.Mirror.reconcile(self, OWNER, strict=True)
    failing, _, _ = fake(local_clean=False)
    with pytest.raises(RuntimeError):
        mirror_mod.Mirror.reconcile(failing, OWNER, strict=True)
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 60)
    idle()
    assert len(remote) == 2


def test_poll_never_waits_for_a_slow_remote_pass():
    """Hub 2026-11-23: the inline strict pass took 3-21 s and starved the backend's web threads."""
    import threading
    started, release = threading.Event(), threading.Event()
    self, remote, local = fake()

    def slow(owner):
        remote.append(owner)
        started.set()
        assert release.wait(5)
    self.remote.reconcile = slow
    try:
        mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)  # returns while the remote pass is blocked
        assert local == [(OWNER, False)]
        assert started.wait(5)
        mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)  # stale and already running: not queued twice
        # warm now: its local pass is not run on the request thread either (answered from the store)
        assert remote == [OWNER] and len(local) == 1
    finally:
        release.set()
    idle()
    assert remote == [OWNER]
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)  # the next poll queues the local (and stale remote) pass
    idle()
    assert local[-1] == (OWNER, False) and len(local) == 2


def test_a_clean_background_pass_marks_fresh_and_a_failed_one_does_not(caplog):
    self, remote, _ = fake()
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)
    idle()
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)
    idle()
    assert len(remote) == 1  # the clean background pass made the next poll skip

    mirror_mod._REMOTE_FRESH.clear()
    failing, failing_remote, _ = fake(remote_result=False)
    mirror_mod.Mirror.reconcile_poll(failing, OWNER, 15)
    idle()
    mirror_mod.Mirror.reconcile_poll(failing, OWNER, 15)
    idle()
    assert len(failing_remote) == 2  # deferred: still stale, the next poll queued again
    assert 'Background remote card pass failed' not in caplog.text  # a deferral is routine, not a traceback


def test_fresh_window_zero_keeps_the_inline_full_pass():
    self, remote, local = fake()
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 0)
    assert remote == [OWNER] and local == [(OWNER, False)]  # synchronous, no worker


def test_a_background_pass_older_than_a_failed_strict_pass_does_not_mark_fresh(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(mirror_mod.time, 'monotonic', lambda: clock[0])
    key = mirror_mod.encoded(OWNER)
    started = clock[0]  # a background pass starts
    clock[0] += 1
    mirror_mod._clear_remote_fresh(key)  # a newer strict pass fails
    clock[0] += 1
    mirror_mod._mark_remote_fresh(key, started)  # the older pass finishes clean
    assert not mirror_mod._remote_fresh(key, 60)


def test_a_dead_worker_gives_its_slot_back(monkeypatch):
    """A BaseException inside a pass must not leave a counted worker that never drains again."""
    import threading
    died, hooked = [], threading.Event()

    def hook(args):  # CPython calls it after the worker function returned: wait on it, not on idle()
        died.append(args.exc_type)
        hooked.set()
    monkeypatch.setattr(mirror_mod.threading, 'excepthook', hook)  # the worker dies on purpose
    self, _, _ = fake()

    def die(owner):
        raise KeyboardInterrupt  # BaseException, not Exception
    self.remote.reconcile = die
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)
    assert hooked.wait(5)
    idle()
    assert died == [KeyboardInterrupt]
    healthy, remote2, _ = fake()
    mirror_mod.Mirror.reconcile_poll(healthy, OWNER, 15)
    idle()
    assert remote2 == [OWNER]  # a new worker ran it


def test_thread_start_failure_is_retried_by_the_next_poll(monkeypatch, caplog):
    self, remote, local = fake()
    real_thread = mirror_mod.threading.Thread

    class NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")
    monkeypatch.setattr(mirror_mod.threading, 'Thread', NoThread)
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)  # the poll itself still answers
    assert local == [(OWNER, False)] and remote == []
    assert 'Background card pass not started' in caplog.text
    assert mirror_mod._BACKGROUND_WORKERS_ALIVE == 0  # no slot leaked
    monkeypatch.setattr(mirror_mod.threading, 'Thread', real_thread)
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)  # a duplicate poll still starts the missing worker
    idle()
    assert remote == [OWNER]


def test_a_pass_queued_while_workers_exit_is_not_stranded(monkeypatch):
    """Workers counted but leaving (queue seen empty) when a poll queues: the next poll must start one."""
    self, remote, _ = fake()
    monkeypatch.setattr(mirror_mod, '_BACKGROUND_WORKERS_ALIVE', mirror_mod.BACKGROUND_WORKERS)  # all mid-exit
    assert mirror_mod._schedule_remote(self, OWNER) is True  # queued, no free slot
    monkeypatch.setattr(mirror_mod, '_BACKGROUND_WORKERS_ALIVE', 0)  # they finished leaving
    assert mirror_mod._schedule_remote(self, OWNER) is False  # duplicate, but it starts a worker
    idle()
    assert remote == [OWNER]


def test_the_background_pass_runs_in_the_polls_context():
    """Hub profiles are multiplexed through contextvars (HERMES_HOME overrides); a new thread would see the
    process default, so the pass must run under the poll's captured context."""
    import contextvars
    profile = contextvars.ContextVar('profile', default='process-default')
    seen = []
    self, _, _ = fake()
    self.remote.reconcile = lambda owner: seen.append(profile.get())
    token = profile.set('profile-b')
    try:
        mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)
    finally:
        profile.reset(token)
    idle()
    assert seen == ['profile-b']


def test_warm_poll_answers_from_the_store_and_reconciles_local_in_background():
    """Hub 2026-11-23: the local pass re-read 222 card bindings through one kept connection per board on every
    poll; concurrent polls queued 10-86 s and Desktop showed "Action queue unavailable". After an owner's first
    local pass, a poll returns at once (the store holds the last good requests) and the pass runs on a worker."""
    import threading
    self, remote, local = fake()
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)  # cold: inline
    assert local == [(OWNER, False)]
    idle()
    gate, entered = threading.Event(), threading.Event()

    def slow_local(owner, strict):
        entered.set()
        gate.wait(5)
        local.append((owner, strict))
    self._reconcile_local = slow_local
    try:
        mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)  # warm: returns while its local pass is blocked
        assert entered.wait(5), 'the warm poll queued no local pass'
        assert len(local) == 1, 'the warm poll ran its local pass inline'
        mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)  # running: not queued twice, still no inline pass
        assert len(local) == 1
    finally:
        gate.set()
    idle()
    assert local[1:] == [(OWNER, False)], 'the background local pass did not run exactly once'


def test_a_queued_remote_pass_is_widened_to_local_not_duplicated():
    self, remote, local = fake()
    mirror_mod._mark_local_seen(mirror_mod._local_key(self, OWNER))
    mirror_mod._BACKGROUND_WORKERS_ALIVE, saved = mirror_mod.BACKGROUND_WORKERS, mirror_mod._BACKGROUND_WORKERS_ALIVE
    try:  # no free worker slot: the passes stay queued so the merge is observable
        assert mirror_mod._schedule(self, OWNER, ('remote',)) is True
        assert mirror_mod._schedule(self, OWNER, ('local',)) is False
        assert mirror_mod._BACKGROUND_PENDING[mirror_mod.encoded(OWNER)][3] == frozenset({'remote', 'local'})
    finally:
        mirror_mod._BACKGROUND_WORKERS_ALIVE = saved
    mirror_mod._schedule(self, OWNER, ())  # a free slot again: a worker drains the queued pass
    idle()
    assert remote == [OWNER] and local == [(OWNER, False)]


def test_a_failed_background_local_pass_is_logged_and_retried_by_the_next_poll(caplog):
    self, remote, local = fake()
    mirror_mod.Mirror.reconcile(self, OWNER, strict=True)  # fresh, warm
    calls = []

    def failing(owner, strict):
        calls.append(owner)
        raise OSError('board busy')
    self._reconcile_local = failing
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)
    idle()
    assert 'Background local card pass failed' in caplog.text
    mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)
    idle()
    assert len(calls) == 2


def test_a_cold_local_pass_that_raises_still_queues_the_remote_pass():
    """Review 2026-11-23: queue the background pass before the cold inline local pass, so a local pass that keeps
    failing (busy board) never keeps the owner's remote asks out of the queue."""
    self, remote, local = fake()

    def failing(owner, strict, *, force_full=False):
        local.append((owner, strict))
        raise OSError('board busy')
    self._reconcile_local = failing
    with pytest.raises(OSError):
        mirror_mod.Mirror.reconcile_poll(self, OWNER, 15)
    idle()
    assert remote == [OWNER], 'a failed cold local pass stranded the remote pass'
    assert not mirror_mod._local_seen(mirror_mod._local_key(self, OWNER)), 'a failed cold pass marked the owner warm'
