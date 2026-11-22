"""The real HTTP entry serves pre-serialized publication bytes only."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse
from test_routes import load
from test_feed import Readers


def test_http_read_matches_legacy_bytes_without_lane_json_or_compute(monkeypatch):
    api = load('_spf_published_route', Path(__file__).resolve().parents[1] / 'dashboard/plugin_api.py')
    core = api._load_feed()
    f = core.Feed(Readers(), emit=lambda p: None)
    monkeypatch.setattr(core, 'PROCESS_FEED', f)
    expected = JSONResponse(api.json_safe({'feed': api.FEED_ID, 'profile': api.LAUNCH_PROFILE,
                                          **f.changes(['S1'])})).body
    def forbidden(*a, **kw):
        raise AssertionError('interactive read invoked compute, serialization or a lane')
    monkeypatch.setattr(api, '_on_lane', forbidden)
    monkeypatch.setattr(api, 'json_safe', forbidden)
    monkeypatch.setattr(api, 'feed', forbidden)
    monkeypatch.setattr(f, 'compute', forbidden)
    app = FastAPI()
    app.include_router(api.router)
    response = TestClient(app).post('/changes', json={'sessions': ['S1']})
    assert response.status_code == 200
    assert response.content == expected
    cold = TestClient(app).post('/changes', json={'sessions': ['S2']})
    assert cold.json()['bundles'] == {}
