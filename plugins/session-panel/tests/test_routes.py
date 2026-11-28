from test_model import canonical_outcome
"""POST /changes and GET /status through FastAPI, with the process feed over fake readers."""
import importlib.util
import sys
from pathlib import Path

from fastapi import FastAPI
import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_changes_route_watches_and_catches_up(monkeypatch):
    api = load('_spf_routes_api', HERE.parents[1] / 'dashboard' / 'plugin_api.py')
    fakes = load('_spf_routes_fakes', HERE.parent / 'test_feed.py')
    core = api._load_feed()
    monkeypatch.setattr(api, 'CoreReaders', fakes.Readers)
    pushed = []
    core.PROCESS_FEED = core.Feed(fakes.Readers(), emit=pushed.append)
    try:
        app = FastAPI()
        app.include_router(api.router, prefix='/api/plugins/session-panel')
        c = TestClient(app)
        first = c.post('/api/plugins/session-panel/changes', json={'sessions': ['S1', 'S2']}).json()
        assert first['feed'] == api.FEED_ID and first['reset'] is True
        assert first['bundles'] == {}, 'cold request only registers interest'
        core.PROCESS_FEED.tick()  # producer publishes independently of request
        first = c.post('/api/plugins/session-panel/changes', json={'sessions': ['S1', 'S2']}).json()
        assert set(first['bundles']) == {'S1', 'S2'}
        # The wire carries what clients render (model, tasks, lineage), never the hub-only card chain it came from.
        assert 'cards' not in first['bundles']['S1'] and first['bundles']['S1']['model']
        assert first['bundles']['S1']['tasks']
        again = c.post('/api/plugins/session-panel/changes',
                       json={'sessions': ['S1', 'S2'], 'since': first['rev'], 'epoch': first['epoch']}).json()
        assert again['bundles'] == {} and again['reset'] is False
        st = c.get('/api/plugins/session-panel/status').json()
        assert st['running'] is True and st['watched'] == 2 and st['bundles'] == 2 and st['failures'] == {}
        assert first['profile'] == api.LAUNCH_PROFILE
        assert c.post('/api/plugins/session-panel/changes', json={'sessions': ['x'] * 201}).status_code == 422
    finally:
        core.PROCESS_FEED.stopped.set()
        lane = getattr(core.PROCESS_FEED, '_decision_thread', None)
        if lane:
            lane.join(2)
        core.PROCESS_FEED = None


def test_a_request_for_another_profile_is_refused_not_served_from_the_wrong_state_db(monkeypatch):
    api = load('_spf_routes_api2', HERE.parents[1] / 'dashboard' / 'plugin_api.py')
    monkeypatch.setattr(api, '_request_profile', lambda: 'work')
    monkeypatch.setattr(api, 'LAUNCH_PROFILE', 'default')
    app = FastAPI()
    app.include_router(api.router, prefix='/api/plugins/session-panel')
    r = TestClient(app).post('/api/plugins/session-panel/changes', json={'sessions': ['S1']}).json()
    assert r == {'feed': api.FEED_ID, 'profile': 'work', 'profile_unsupported': True}
    assert api._load_feed().PROCESS_FEED is None, 'no feed started for it'


def test_a_plugin_reload_resumes_on_register_not_on_the_request(monkeypatch):
    # [daveey 2026-11-21] a reload (unload -> register) must not start a new revision space.
    import threading
    api = load('_spf_reload_api', HERE.parents[1] / 'dashboard' / 'plugin_api.py')
    fakes = load('_spf_reload_fakes', HERE.parent / 'test_feed.py')
    plugin = load('_spf_reload_plugin', HERE.parents[1] / '__init__.py')
    core = api._load_feed()
    monkeypatch.setattr(api, 'CoreReaders', fakes.Readers)
    monkeypatch.setattr(api, '_emit', lambda payload: None)
    core.PROCESS_FEED = None
    try:
        app = FastAPI()
        app.include_router(api.router, prefix='/api/plugins/session-panel')
        c = TestClient(app)
        api.feed()  # lifecycle producer starts before interactive reads
        first = c.post('/api/plugins/session-panel/changes', json={'sessions': ['S1', 'S2']}).json()
        # Unload: reads keep the held publication; register owns restart work.
        plugin.stop()
        # Startup hydration may publish between the first response and unload.
        # Compare against the stopped publication, not that racing earlier rev.
        held = c.post('/api/plugins/session-panel/changes', json={'sessions': ['S1', 'S2']}).json()
        assert held['epoch'] == first['epoch']
        again = c.post('/api/plugins/session-panel/changes',
                       json={'sessions': ['S1', 'S2'], 'since': held['rev'], 'epoch': held['epoch']}).json()
        assert again['epoch'] == first['epoch'] and again['reset'] is False and again['bundles'] == {}
        assert core.PROCESS_FEED.stopped.is_set() is True
        # Unload then register again: register resumes it at once, before any request.
        resumed = core.PROCESS_FEED
        plugin.stop()
        started = threading.Event()
        real = core.RESTART
        monkeypatch.setattr(core, 'RESTART', lambda: (real(), started.set()))
        plugin.register(type('Ctx', (), {'on_unload': lambda self, cb: None})())
        assert started.wait(5)
        assert core.PROCESS_FEED is not resumed and not core.PROCESS_FEED.stopped.is_set()
        assert core.PROCESS_FEED.epoch == first['epoch']
        # A resume that fails still leaves a running feed (a new revision space).
        plugin.stop()
        monkeypatch.setattr(core.Feed, 'resume', lambda self, prev: (_ for _ in ()).throw(RuntimeError('boom')))
        f = api.feed()
        assert not f.stopped.is_set() and f.epoch != first['epoch']
    finally:
        if core.PROCESS_FEED is not None:
            core.PROCESS_FEED.stopped.set()
        core.PROCESS_FEED = None



def _hot(monkeypatch, tag):
    api = load(f'_spf_hot_api_{tag}', HERE.parents[1] / 'dashboard' / 'plugin_api.py')
    fakes = load(f'_spf_hot_fakes_{tag}', HERE.parent / 'test_feed.py')
    monkeypatch.setattr(api, 'CoreReaders', fakes.Readers)
    monkeypatch.setattr(api, '_emit', lambda payload: None)
    original = sys.modules[api._FEED_KEY]
    original.PROCESS_FEED = None
    return api, original


def _restore(api, original):
    mod = sys.modules.get(api._FEED_KEY)
    if mod is not None and mod.PROCESS_FEED is not None:
        mod.PROCESS_FEED.stopped.set()
    original.PROCESS_FEED = None
    sys.modules[api._FEED_KEY] = original


def test_a_changed_feed_py_is_loaded_anew_and_the_running_feed_handed_over_at_once(monkeypatch):
    # [daveey 2026-11-21] a hot mods-reload re-imported plugin_api.py but kept the old feed.py module, so the feed
    # fix stayed inactive until the backend restarted.
    import threading
    api, old_mod = _hot(monkeypatch, 'a')
    # Observe the handover before any successor work, then exercise a real
    # publication before the client catches up. Instance readiness alone permits
    # all three producer lanes to advance rev before the equality below.
    ready, advance, published = threading.Event(), threading.Event(), threading.Event()
    feeds = []

    def park(f, *args):
        f.stopped.wait()

    def produce(f):
        feeds.append(f)
        if isinstance(f, old_mod.Feed):
            f.stopped.wait()
            return
        ready.set()
        assert advance.wait(5), 'observer never released successor publication'
        if not f.stopped.is_set():
            f.readers.cards['t_aaaaaa1']['detail']['task']['title'] = 'Updated by successor source'
            f._publish(f._compute_all(['S1']))
            published.set()
        f.stopped.wait()

    monkeypatch.setattr(api, '_run', produce)
    monkeypatch.setattr(api, '_run_decisions', park)
    monkeypatch.setattr(api, '_run_board', park)
    # This fixture owns in-memory feeds, not a serving process's checkpoint.
    monkeypatch.setattr(api, 'load_snapshot', lambda: None)
    monkeypatch.setattr(api, 'save_snapshot', lambda *a, **k: True)
    monkeypatch.setattr(api, '_save_at_exit', lambda: None)
    try:
        assert api._load_feed() is old_mod, 'an unchanged feed.py is not reloaded'
        running = api.feed()
        first = running.changes(['S1'])
        # Touched with the same bytes: nothing reloads.
        monkeypatch.setattr(old_mod, 'SOURCE_STAMP', ('touched',))
        assert api._load_feed() is old_mod and old_mod.SOURCE_STAMP == api._feed_stamp()
        # Different bytes on disk (as if feed.py changed): the new code loads and the feed is resumed under it.
        monkeypatch.setattr(old_mod, 'SOURCE_STAMP', ('older code',))
        monkeypatch.setattr(old_mod, 'SOURCE_SHA', 'older code')
        new_mod = api._load_feed()
        assert new_mod is not old_mod and new_mod.Feed is not old_mod.Feed
        assert sys.modules[api._FEED_KEY] is new_mod and new_mod.PROCESS_LOCK is old_mod.PROCESS_LOCK
        assert running.stopped.is_set()
        assert ready.wait(5), 'reload must start its successor without a request'
        f = new_mod.PROCESS_FEED
        assert isinstance(f, new_mod.Feed) and not f.stopped.is_set()
        assert f.epoch == first['epoch'] and f.rev == running.rev
        assert api.feed() is f
        held = running.rev  # what a client of the old code holds
        advance.set()
        assert published.wait(5), 'successor must publish independently of client reads'
        assert f.epoch == first['epoch'] and f.rev == held + 1
        assert running.rev == held, 'the retired predecessor remains frozen'
        out = f.changes(['S1'], since=held, epoch=first['epoch'])
        assert out['reset'] is False, 'no reset across the handover'
        assert set(out['bundles']) == {'S1'}, 'the changed publication must reach the old cursor'
        assert out['bundles']['S1']['model']['sections']['waiting'][0]['title'] == 'Updated by successor source'
        assert all(f.bundles[s]['rev'] > held for s in out['bundles']), 'only what changed since'
    finally:
        # Complete thread registration before collecting exact identities; never
        # restore mocks/module ownership while a fixture producer is still alive.
        advance.set()
        with old_mod.PROCESS_LOCK:
            current = sys.modules.get(api._FEED_KEY)
            owned = [*feeds, getattr(old_mod, 'PROCESS_FEED', None), getattr(current, 'PROCESS_FEED', None)]
            for f in owned:
                if f is not None:
                    f.stopped.set()
            threads = {getattr(f, attr, None) for f in owned if f is not None
                       for attr in ('_feed_thread', '_decision_thread', '_board_thread')}
        for worker in threads - {None}:
            worker.join(5)
            assert not worker.is_alive(), 'fixture producer did not exit'
        _restore(api, old_mod)


def test_a_feed_py_that_fails_to_load_keeps_the_running_feed(monkeypatch, tmp_path, caplog):
    api, old_mod = _hot(monkeypatch, 'b')
    try:
        running = api.feed()
        bad = tmp_path / 'feed.py'
        bad.write_text('raise RuntimeError("half-written")\n')
        monkeypatch.setattr(api, '_FEED_PATH', bad)
        monkeypatch.setattr(old_mod, 'SOURCE_STAMP', old_mod.SOURCE_STAMP)  # restored after: the test records a stamp
        gen = api._LOADER.generation
        assert api._load_feed() is old_mod and not running.stopped.is_set()
        assert f'{api._FEED_KEY}_g{gen + 1}' not in sys.modules, 'the failed generation is not left registered'
        assert 'failed to load' in caplog.text
        assert sys.modules[api._FEED_KEY] is old_mod
        caplog.clear()
        assert api._load_feed() is old_mod and caplog.text == '', 'not retried until feed.py changes again'
    finally:
        _restore(api, old_mod)



def test_a_reload_from_real_older_feed_code_hands_over_with_the_same_epoch(monkeypatch):
    # The live upgrade: the process holds feed.py as loaded by older code (no stamp, a Feed without resume, a
    # publish with no stopped-check), and the plugin's unload already stopped it before plugin_api.py is imported.
    import importlib.util
    import time
    api, current = _hot(monkeypatch, 'c')
    fakes = load('_spf_hot_fakes_c2', HERE.parent / 'test_feed.py')
    spec = importlib.util.spec_from_file_location(api._FEED_KEY, HERE.parent / 'fixtures' / 'feed_6f569abc.py')
    older = importlib.util.module_from_spec(spec)
    sys.modules[api._FEED_KEY] = older
    try:
        spec.loader.exec_module(older)
        assert not hasattr(older.Feed, 'resume')
        sent = []
        running = older.Feed(fakes.Readers(), emit=sent.append)
        first = running.changes(['S1'])
        older.PROCESS_FEED = running
        running.stopped.set()  # the old plugin's unload ran first
        new_mod = api._load_feed()
        assert new_mod is not older and sys.modules[api._FEED_KEY] is new_mod
        deadline = time.monotonic() + 5
        while not isinstance(new_mod.PROCESS_FEED, new_mod.Feed) and time.monotonic() < deadline:
            time.sleep(0.01)
        f = new_mod.PROCESS_FEED  # resumed without any request
        assert isinstance(f, new_mod.Feed) and not f.stopped.is_set()
        # The revision space survives. Recomputing the old raw-only bundle now
        # adds model, so the resumed feed may already have published a new rev.
        assert f.epoch == first['epoch'] and f.rev >= running.rev
        if f.rev > running.rev:
            assert f.bundles['S1']['bundle']['model']['version'] == 1
        sent.clear()
        rev = running.rev
        older.Feed._publish(running, {'S1': running.compute('S1')})  # the old class's own publish, still in flight
        out = running.changes(['S2'], since=first['rev'], epoch=first['epoch'])  # a stale reference to it
        assert sent == [] and running.rev == rev, 'the older object neither pushes nor moves a revision'
        assert out['epoch'] == f.epoch and 'S2' in f.interest and 'S2' in out['bundles']
    finally:
        _restore(api, current)


def test_current_api_adds_one_canonical_lane_to_feed_resumed_by_older_api(monkeypatch):
    import threading
    api, core = _hot(monkeypatch, 'mixed_decisions')
    fakes = load('_spf_mixed_decision_fakes', HERE.parent / 'test_feed.py')
    old_namespace = dict(api.__dict__)
    old_namespace.update(CoreReaders=fakes.Readers,
                         _run=lambda f: f.stopped.wait(5),
                         load_snapshot=lambda: None,
                         save_snapshot=lambda *a, **k: True,
                         _save_at_exit=lambda: None)
    source = (HERE.parent / 'fixtures' / 'feed_entry_before_decisions.py').read_text()
    exec(compile(source, 'feed_entry_before_decisions.py', 'exec'), old_namespace)
    observed, published, released = threading.Event(), threading.Event(), threading.Event()
    class CurrentReaders(fakes.Readers):
        def decision_inputs(self, session, lineage):
            observed.set()
            return {'outcome_json': canonical_outcome('done'), 'requests': []}
        def release(self):
            released.set()
    monkeypatch.setattr(api, 'CoreReaders', CurrentReaders)
    f = None
    try:
        # A saved RESTART closure from before this change resumes a head Feed,
        # but creates old readers and only its historical board thread.
        core.PROCESS_FEED = core.Feed(fakes.Readers(), emit=lambda p: None)
        core.PROCESS_FEED.stopped.set()
        core.RESTART = old_namespace['feed']
        f = core.RESTART()
        f.changes(['S1'])
        def delivered(payload):
            model = payload.get('bundles', {}).get('S1', {}).get('model', {})
            if model.get('header', {}).get('state') == 'done':
                published.set()
        f.emit = delivered
        assert not hasattr(f.readers, 'decision_inputs')
        assert not getattr(f, '_decision_thread', None)
        assert api.feed() is f
        assert observed.wait(2), 'current API must start decisions even for an existing current Feed'
        lane = f._decision_thread
        assert api.feed() is f and f._decision_thread is lane, 'idempotent under PROCESS_LOCK'
        assert published.wait(2), 'stop only after publication, not merely the start of its input read'
        f.stopped.set()
        lane.join(2)
        assert not lane.is_alive() and released.is_set(), 'lane owns/releases its current-generation readers'
        assert f.bundles['S1']['bundle']['model']['header']['state'] == 'done'
    finally:
        if f is not None:
            f.stopped.set()
            lane = getattr(f, '_decision_thread', None)
            if lane:
                lane.join(2)
        _restore(api, core)


@pytest.mark.parametrize('before_registration', [False, True, None])
def test_saved_predecessor_board_entry_gets_completion_service(monkeypatch, before_registration):
    import threading
    import time
    from test_feed import Readers
    api, core = _hot(monkeypatch, 'mixed_board_service')
    r = Readers()
    f = core.Feed(r, emit=lambda p: None)
    f.changes(['S1', 'S2'])
    f._panel_subscribers = {s: (s, f.clock() + 75) for s in f.interest}
    r.links = {s: [{**rows[0], 'status': 'ready'}] for s, rows in r.links.items()}
    entered, release = threading.Event(), threading.Event()
    r.external_stamps = lambda: 99
    def capture(held):
        entered.set()
        assert release.wait(5)
        return {s: 0 for s in held}
    r.external_capture = capture
    lanes = []
    constructing, register = threading.Event(), threading.Event()
    original_lane = core.BoardLane
    class Lane(original_lane):
        def __init__(self, f, factory):
            if before_registration:
                constructing.set()
                assert register.wait(5)
            super().__init__(f, factory, capacity=1)
            self.pending = {'S1': time.time(), 'S2': time.time() + .001}
            lanes.append(self)
    monkeypatch.setattr(core, 'BoardLane', Lane)
    monkeypatch.setattr(api, 'CoreReaders', lambda: r)
    monkeypatch.setattr(api, 'write_timing', lambda f: None)
    monkeypatch.setattr(api, '_run_decisions', lambda f: f.stopped.wait(5))
    core.PROCESS_FEED = f
    ns = dict(api.__dict__)
    source = (HERE.parent / 'fixtures' / 'board_entry_before_service.py').read_text()
    exec(compile(source, 'board_entry_before_service.py', 'exec'), ns)
    try:
        assert ns['feed']() is f
        board_thread = f._board_thread
        if before_registration:
            assert constructing.wait(1)
            assert api.feed() is f and f._board_thread is board_thread
            assert api.feed() is f and f._board_thread is board_thread
            register.set()
            assert entered.wait(1)
        else:
            assert entered.wait(1)
            # None is natural reload: only the saved predecessor closure runs.
            if before_registration is not None:
                assert api.feed() is f
                assert api.feed() is f and f._board_thread is board_thread
        end = time.monotonic() + 1
        while time.monotonic() < end:
            if all(f.bundles[s]['bundle']['tasks'][0]['status'] == 'ready' for s in ('S1', 'S2')):
                break
            time.sleep(.01)
        assert all(f.bundles[s]['bundle']['tasks'][0]['status'] == 'ready' for s in ('S1', 'S2')), \
            'predecessor closure retained a lane without completion-driven slot refill'
        assert len(lanes) == 1 and lanes[0].service_worker is not None
        assert not release.is_set()
    finally:
        register.set()
        release.set()
        f.stopped.set()
        if hasattr(f, '_board_thread'):
            f._board_thread.join(3)
        if lanes:
            lanes[0].close()
            assert not lanes[0].service_worker or not lanes[0].service_worker.is_alive()
        _restore(api, core)


def test_feed_role_is_stable_across_launches_and_tells_backends_apart():
    api = load('_spf_role_api', HERE.parents[1] / 'dashboard' / 'plugin_api.py')
    tok = '/home/u/.hermes/desktop-ssh/5caca281/b6911608.token'
    assert api._feed_role(['serve', '--isolated', '--port', '9148', '--ssh-session-token-file', tok]) == 'serve-5caca281'
    assert api._feed_role(['serve', '--port', '9149', f'--ssh-session-token-file={tok.replace("b69", "c00")}']) == \
        'serve-5caca281', 'the port and the per-launch token change; the Desktop slot does not'
    assert api._feed_role(['--profile', 'coder', 'serve', '--ssh-session-token-file', tok]) == 'serve-5caca281'
    assert api._feed_role(['-p', 'coder', 'dashboard']) == 'dashboard', 'a profile value is not the subcommand'
    assert api._feed_role(['--profile', 'coder', 'serve', '--port', '9147']) == 'serve'
    assert api._feed_role(['dashboard', '--port', '9147']) == 'dashboard'
    assert api._feed_role([]) == 'proc'
    assert api.FEED_ID.endswith(':' + api.FEED_ROLE) and str(__import__('os').getpid()) not in api.FEED_ID.split(':')


def test_snapshot_save_and_load_round_trip_and_refusals(tmp_path, monkeypatch, caplog):
    import json
    import os
    import stat
    import time
    api = load('_spf_snap_api', HERE.parents[1] / 'dashboard' / 'plugin_api.py')
    fakes = load('_spf_snap_fakes', HERE.parent / 'test_feed.py')
    core = api._load_feed()
    f = core.Feed(fakes.Readers(), emit=lambda p: None)
    first = f.changes(['S1'])
    path = tmp_path / 'feed.json'
    assert api.save_snapshot(f, path, claim=True)
    assert api.write_timing(f, tmp_path / 'timing.json')
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600, 'chat content: owner only'
    snap = api.load_snapshot(path)
    assert snap['epoch'] == first['epoch'] and snap['rev'] == f.rev and set(snap['bundles']) == {'S1'}
    head = json.loads(path.read_text().split('\n', 1)[0])
    mine = head['gen']
    assert head['feed'] == api.FEED_ID and mine == api._CLAIMS[str(path)]
    assert api.load_snapshot(path, now=time.time() + api.RESTORE_MAX_AGE_S + 1) is None, 'stale'
    for key, value in (('feed', 'otherhost:serve'), ('profile', 'other'), ('epoch', 'other')):
        bad = dict(head, **{key: value})
        (tmp_path / 'bad.json').write_text(json.dumps(bad) + '\n' + path.read_text().split('\n', 1)[1])
        assert api.load_snapshot(tmp_path / 'bad.json') is None, key
    # Adjacent valid identity fields must not mask malformed JSON container types.
    body = path.read_text().split('\n', 1)[1]
    for bad_head, bad_body in (([], body), (head, '[]')):
        (tmp_path / 'shape.json').write_text(json.dumps(bad_head) + '\n' + bad_body)
        assert api.load_snapshot(tmp_path / 'shape.json') is None
    # A newer process claimed the file (its generation is higher): this one never overwrites it, and says so once.
    body = path.read_text().split('\n', 1)[1]
    path.write_text(json.dumps(dict(head, gen=mine + 1, owner_pid=1)) + '\n' + body)
    f.changes(['S2'])
    caplog.clear()
    assert api.save_snapshot(f, path) is False and api.save_snapshot(f, path) is False
    assert set(api.load_snapshot(path)['bundles']) == {'S1'}, 'file unchanged'
    assert caplog.text.count('a newer process') == 1, 'refusal logged, not repeated'
    # Claiming again (as a new process would) takes it over with the next generation.
    assert api.save_snapshot(f, path, claim=True)
    assert json.loads(path.read_text().split('\n', 1)[0])['gen'] == mine + 2
    path.write_text('{not json')
    assert api.load_snapshot(path) is None and 'snapshot unreadable' in caplog.text
    assert api.load_snapshot(tmp_path / 'missing.json') is None
    assert not [p for p in tmp_path.iterdir() if p.name.endswith('.tmp')], 'no temp files left behind'


def test_snapshot_path_without_core_uses_hermes_home(monkeypatch, tmp_path):
    import builtins
    api = load('_spf_nocore_api', HERE.parents[1] / 'dashboard' / 'plugin_api.py')
    real_import = builtins.__import__

    def no_core(name, *a, **k):
        if name == 'hermes_constants':
            raise ImportError(name)
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, '__import__', no_core)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    assert api._snapshot_path().parent == tmp_path / 'session-panel'


def test_a_snapshot_busy_past_its_timeout_is_skipped_not_waited_for(tmp_path):
    api = load('_spf_busy_api', HERE.parents[1] / 'dashboard' / 'plugin_api.py')
    fakes = load('_spf_busy_fakes', HERE.parent / 'test_feed.py')
    f = api._load_feed().Feed(fakes.Readers(), emit=lambda p: None)
    with f.compute_lock:  # a push stuck in emit
        assert api.save_snapshot(f, tmp_path / 'feed.json', timeout=0.05) is False
    assert not (tmp_path / 'feed.json').exists()


def test_old_snapshots_of_other_backends_are_pruned_on_claim(tmp_path):
    import os
    import time
    api = load('_spf_prune_api', HERE.parents[1] / 'dashboard' / 'plugin_api.py')
    fakes = load('_spf_prune_fakes', HERE.parent / 'test_feed.py')
    f = api._load_feed().Feed(fakes.Readers(), emit=lambda p: None)
    old, recent = tmp_path / 'feed-gone-default.json', tmp_path / 'feed-other-default.json'
    for p in (old, recent):
        p.write_text('x')
    week = time.time() - api.SNAPSHOT_PRUNE_S - 60
    os.utime(old, (week, week))
    assert api.save_snapshot(f, tmp_path / 'feed-mine-default.json', claim=True)
    assert not old.exists() and recent.exists()


def test_a_new_process_restores_the_saved_feed_claims_it_and_saves_on_stop_and_exit(monkeypatch):
    import json
    import time
    import threading
    api, original = _hot(monkeypatch, 'restore')
    run_gate = threading.Event()
    monkeypatch.setattr(api, 'SNAPSHOT_EVERY_S', 10_000.0)  # no periodic save: only the paths under test write
    try:
        f1 = api.feed()
        first = f1.changes(['S1', 'S2'])
        assert api.save_snapshot(f1)
        old_claim = api._CLAIMS[str(api._snapshot_path())]
        # A new process: nothing running in it yet (the old one is gone or still finishing).
        monkeypatch.setattr(f1, '_successor', object())  # keep the stopping thread from saving over the test
        f1.stopped.set()
        time.sleep(1.5)  # its thread finishes the tick it may be in
        saved_rev = json.loads(api._snapshot_path().read_text().split('\n', 1)[0])['rev']
        original.PROCESS_FEED = None
        api._CLAIMS.clear()
        # Claim is synchronous; do not let the resumed background sweep change
        # the revision between reading that claim and comparing it to the feed.
        run = api._run
        def gated_run(feed):
            assert run_gate.wait(5)
            run(feed)
        monkeypatch.setattr(api, '_run', gated_run)
        f2 = api.feed()
        assert f2 is not f1 and f2.epoch == first['epoch']
        assert f2.rev >= saved_rev + api._load_feed().RESTORE_REV_GAP
        head = json.loads(api._snapshot_path().read_text().split('\n', 1)[0])
        assert head['rev'] == f2.rev and head['gen'] == old_claim + 1, 'the new process claimed the snapshot at once'
        run_gate.set()
        # The old process (its claim superseded) can no longer save.
        mine = api._CLAIMS[str(api._snapshot_path())]
        api._CLAIMS[str(api._snapshot_path())] = old_claim
        assert api.save_snapshot(f1) is False
        api._CLAIMS[str(api._snapshot_path())] = mine
        # The exit hook saves the running feed.
        f2.changes(['S3'])
        assert api._save_running() and 'S3' in api.load_snapshot()['bundles']
        # A plain stop saves the last state from the feed thread.
        f2.changes(['S4'])
        f2.stopped.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and 'S4' not in (api.load_snapshot() or {}).get('bundles', {}):
            time.sleep(0.05)
        assert 'S4' in api.load_snapshot()['bundles']
    finally:
        run_gate.set()
        _restore(api, original)


def test_a_claim_that_never_reached_disk_or_a_vanished_file_does_not_block_later_saves(tmp_path, monkeypatch):
    import json
    import os
    api = load('_spf_claimfail_api', HERE.parents[1] / 'dashboard' / 'plugin_api.py')
    fakes = load('_spf_claimfail_fakes', HERE.parent / 'test_feed.py')
    f = api._load_feed().Feed(fakes.Readers(), emit=lambda p: None)
    f.changes(['S1'])
    path = tmp_path / 'feed.json'
    real_replace = os.replace
    monkeypatch.setattr(api.os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    assert api.save_snapshot(f, path, claim=True) is False
    assert str(path) not in api._CLAIMS, 'a claim counts only once written'
    monkeypatch.setattr(api.os, 'replace', real_replace)
    assert api.save_snapshot(f, path) is True
    path.unlink()  # deleted by hand, or pruned
    assert api.save_snapshot(f, path) is True, 'a vanished file is written again'
    assert json.loads(path.read_text().split('\n', 1)[0])['gen'] == api._CLAIMS[str(path)]


def test_the_exit_hook_is_registered_once_and_never_waits_past_its_timeout(monkeypatch):
    import atexit
    import time
    api, original = _hot(monkeypatch, 'exit')
    registered = []
    monkeypatch.setattr(atexit, 'register', lambda fn: registered.append(fn))
    monkeypatch.setitem(api._LOADER.__dict__, 'atexit_registered', False)
    monkeypatch.setattr(api, 'EXIT_SAVE_TIMEOUT_S', 0.05)
    try:
        f = api.feed()
        assert registered == [api._save_running], "the process's first feed registers the exit hook"
        api._save_at_exit()
        assert registered == [api._save_running], 'registered once'
        import threading
        with f.compute_lock:  # a push stuck in flight
            result = []
            t = threading.Thread(target=lambda: result.append(api._save_running()), daemon=True)
            t.start()
            t.join(2)
            assert not t.is_alive() and result == [False], 'the exit is not held up'
        assert api._save_running() is True
    finally:
        _restore(api, original)



def test_an_older_process_cannot_retake_a_vanished_snapshot_from_a_newer_one(tmp_path):
    api = load('_spf_vanish_api', HERE.parents[1] / 'dashboard' / 'plugin_api.py')
    fakes = load('_spf_vanish_fakes', HERE.parent / 'test_feed.py')
    f = api._load_feed().Feed(fakes.Readers(), emit=lambda p: None)
    f.changes(['S1'])
    path = tmp_path / 'feed.json'
    assert api.save_snapshot(f, path, claim=True)
    old_claim = api._CLAIMS[str(path)]
    assert api.save_snapshot(f, path, claim=True)  # a newer process claims (same process stands in for it)
    newer = api._CLAIMS[str(path)]
    assert newer == old_claim + 1
    path.unlink()  # the snapshot vanishes while both are alive
    api._CLAIMS[str(path)] = old_claim  # the older process saves
    assert api.save_snapshot(f, path) is False, 'the newer claim survives in the lock file'
    api._CLAIMS[str(path)] = newer
    assert api.save_snapshot(f, path) is True
