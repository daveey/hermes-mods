"""POST /changes and GET /status through FastAPI, with the process feed over fake readers."""
import importlib.util
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_changes_route_watches_and_catches_up():
    api = load('_spf_routes_api', HERE.parents[1] / 'dashboard' / 'plugin_api.py')
    fakes = load('_spf_routes_fakes', HERE.parent / 'test_feed.py')
    core = api._load_feed()
    pushed = []
    core.PROCESS_FEED = core.Feed(fakes.Readers(), emit=pushed.append)
    try:
        app = FastAPI()
        app.include_router(api.router, prefix='/api/plugins/session-panel')
        c = TestClient(app)
        first = c.post('/api/plugins/session-panel/changes', json={'sessions': ['S1', 'S2']}).json()
        assert first['feed'] == api.FEED_ID and first['reset'] is True
        assert set(first['bundles']) == {'S1', 'S2'}
        assert set(first['bundles']['S1']['cards']) == {'t_aaaaaa1', 't_bbbbbb2', 't_ccccccc3'}
        again = c.post('/api/plugins/session-panel/changes',
                       json={'sessions': ['S1', 'S2'], 'since': first['rev'], 'epoch': first['epoch']}).json()
        assert again['bundles'] == {} and again['reset'] is False
        st = c.get('/api/plugins/session-panel/status').json()
        assert st['running'] is True and st['watched'] == 2 and st['bundles'] == 2 and st['failures'] == {}
        assert first['profile'] == api.LAUNCH_PROFILE
        assert c.post('/api/plugins/session-panel/changes', json={'sessions': ['x'] * 201}).status_code == 422
    finally:
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


def test_a_plugin_reload_resumes_the_process_feed_on_register_and_on_the_next_request(monkeypatch):
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
        first = c.post('/api/plugins/session-panel/changes', json={'sessions': ['S1', 'S2']}).json()
        # Unload: the feed stops. The next request resumes it (same epoch, no reset).
        plugin.stop()
        again = c.post('/api/plugins/session-panel/changes',
                       json={'sessions': ['S1', 'S2'], 'since': first['rev'], 'epoch': first['epoch']}).json()
        assert again['epoch'] == first['epoch'] and again['reset'] is False and again['bundles'] == {}
        assert core.PROCESS_FEED.stopped.is_set() is False
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
    import time
    api, old_mod = _hot(monkeypatch, 'a')
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
        deadline = time.monotonic() + 5
        while not isinstance(new_mod.PROCESS_FEED, new_mod.Feed) and time.monotonic() < deadline:
            time.sleep(0.01)
        f = new_mod.PROCESS_FEED  # resumed without any request
        assert isinstance(f, new_mod.Feed) and not f.stopped.is_set()
        assert f.epoch == first['epoch'] and f.rev == running.rev
        assert api.feed() is f
        held = running.rev  # what a client of the old code holds
        out = f.changes(['S1'], since=held, epoch=first['epoch'])
        assert out['reset'] is False, 'no reset across the handover'
        assert all(f.bundles[s]['rev'] > held for s in out['bundles']), 'only what changed since (its thread may tick)'
    finally:
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
    api, original = _hot(monkeypatch, 'restore')
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
        f2 = api.feed()
        assert f2 is not f1 and f2.epoch == first['epoch']
        assert f2.rev >= saved_rev + api._load_feed().RESTORE_REV_GAP
        head = json.loads(api._snapshot_path().read_text().split('\n', 1)[0])
        assert head['rev'] == f2.rev and head['gen'] == old_claim + 1, 'the new process claimed the snapshot at once'
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
        _restore(api, original)
