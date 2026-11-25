"""plugin_api: GET/PUT /v1/split against a temp Hermes root (FastAPI only; skipped where it isn't installed)."""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
API = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"

from test_core import CONFIG, NOW  # noqa: E402  (same fixture config)
from test_flow import flow


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    spec = importlib.util.spec_from_file_location("llm_split_api_under_test", API)
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    (tmp_path / "config.yaml").write_text(CONFIG)
    c = api.core()
    monkeypatch.setattr(c, "hermes_root", lambda: tmp_path)
    monkeypatch.setattr(c, "_forecast_module", lambda r: None)
    (tmp_path / "llm_pacing.json").write_text(
        '{"v2": {"computed_at": %f, "pools": {"anthropic": {"ratio": 0.2}, "openai-codex": {"ratio": 0.8}}}}'
        % __import__("time").time())
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app), tmp_path


def test_get_then_drag_then_auto(client):
    http, root = client
    assert http.get("/ping").json()["ok"]
    snap = http.get("/v1/split").json()
    assert [p["id"] for p in snap["pools"]] == ["anthropic", "openai-codex", "xai-oauth"]
    assert snap["quota"]["source"] == "pacer" and snap["traffic"]["weights"]["anthropic"] == 1
    r = http.put("/v1/split", json={"mode": "manual", "share": {"anthropic": 40, "openai-codex": 59, "xai-oauth": 1}})
    assert r.status_code == 200 and r.json()["traffic"]["weights"] == {"anthropic": 40, "openai-codex": 59, "xai-oauth": 1}
    assert "weight: 59" in (root / "config.yaml").read_text()
    r = http.put("/v1/split", json={"mode": "auto"})
    assert r.status_code == 200 and r.json()["mode"] == "auto"
    assert r.json()["traffic"]["weights"] == {"anthropic": 20, "openai-codex": 79, "xai-oauth": 1}


@pytest.mark.parametrize('width', [80, 100000])
def test_flow_endpoint_manual_auto_and_get_readback(client, width):
    http, root = client
    profile = root / 'profiles' / 'alpha'
    profile.mkdir(parents=True)
    (profile / 'config.yaml').write_bytes(flow(width).encode())
    for body, expected in [
        ({'mode': 'manual', 'share': {'anthropic': 16, 'openai-codex': 58, 'xai-oauth': 25}},
         {'anthropic': 16, 'openai-codex': 59, 'xai-oauth': 25}),
        ({'mode': 'auto'}, {'anthropic': 20, 'openai-codex': 79, 'xai-oauth': 1})
    ]:
        response = http.put('/v1/split', json=body)
        assert response.status_code == 200, response.text
        readback = http.get('/v1/split').json()
        assert readback['traffic']['weights'] == expected
        assert not readback['mixed_homes']
        from test_core import weights_in
        assert weights_in(profile / 'config.yaml')['deep'] == expected
        assert readback['mode'] == body['mode']


@pytest.mark.parametrize('failure', ['yaml', 'encoding', 'write', 'rollback'])
def test_save_failures_return_actionable_detail(client, monkeypatch, failure):
    import sys
    http, root = client
    c = sys.modules['hermes_llm_split_core']
    original = (root / 'config.yaml').read_bytes()
    if failure in ('yaml', 'encoding'):
        profile = root / 'profiles' / 'broken'
        profile.mkdir(parents=True)
        (profile / 'config.yaml').write_bytes(b'model_routing: [' if failure == 'yaml' else b'\xff')
        expected, text = 400, 'config'
    else:
        expected, text = 500, 'injected write failure' if failure == 'write' else 'ROLLBACK FAILED'
        def fail(*args):
            raise OSError('injected write failure') if failure == 'write' else RuntimeError('ROLLBACK FAILED: injected')
        monkeypatch.setattr(c, '_write_state', fail)
    response = http.put('/v1/split', json={'mode': 'auto'})
    assert response.status_code == expected
    assert text in response.json()['detail']
    assert (root / 'config.yaml').read_bytes() == original


def test_bad_bodies_are_400(client):
    http, _ = client
    for body in ({"mode": "manual"}, {"mode": "x"}, {"mode": "manual", "share": {"anthropic": -3}}):
        assert http.put("/v1/split", json=body).status_code == 400
