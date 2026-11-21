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
