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
        app.include_router(api.router, prefix='/api/plugins/session-panel-feed')
        c = TestClient(app)
        first = c.post('/api/plugins/session-panel-feed/changes', json={'sessions': ['S1', 'S2']}).json()
        assert first['feed'] == api.FEED_ID and first['reset'] is True
        assert set(first['bundles']) == {'S1', 'S2'}
        assert set(first['bundles']['S1']['cards']) == {'t_aaaaaa1', 't_bbbbbb2', 't_ccccccc3'}
        again = c.post('/api/plugins/session-panel-feed/changes',
                       json={'sessions': ['S1', 'S2'], 'since': first['rev'], 'epoch': first['epoch']}).json()
        assert again['bundles'] == {} and again['reset'] is False
        st = c.get('/api/plugins/session-panel-feed/status').json()
        assert st['running'] is True and st['watched'] == 2 and st['bundles'] == 2
        assert c.post('/api/plugins/session-panel-feed/changes', json={'sessions': ['x'] * 201}).status_code == 422
    finally:
        core.PROCESS_FEED = None
