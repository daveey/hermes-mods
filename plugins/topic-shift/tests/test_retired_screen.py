"""Retired vendor screening must not be revived by old environment settings."""
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_topic_shift import FakeLLM, NEW, PRIOR, ROOT, core, load, make_db


@pytest.mark.parametrize("mode", ["on", "shadow", "off"])
@pytest.mark.parametrize("probability", [0.1, 0.9])
def test_production_route_uses_only_llm(tmp_path, monkeypatch, mode, probability):
    monkeypatch.setenv("SYSTEM_ONE_MODE_TOPIC_SHIFT", mode)
    monkeypatch.setenv("SYSTEM_ONE_MODE", mode)
    def forbidden(*args, **kwargs):
        pytest.fail("retired vendor screen was called")
    monkeypatch.setitem(sys.modules, "_system_one_client", SimpleNamespace(mode=lambda *a, **k: mode, decide=forbidden, p=forbidden))
    api = load("retirement_api", ROOT / "dashboard/plugin_api.py")
    llm = FakeLLM('{"p_new": %s, "label": "New task"}' % probability)
    monkeypatch.setattr(api._core(), "aux_llm", llm)
    app = FastAPI()
    app.include_router(api.create_router(db_path=make_db(tmp_path, PRIOR)))
    response = TestClient(app).post("/check", json={"session_id": "s1", "text": NEW})
    assert response.status_code == 200
    assert response.json()["shift"] is (probability >= 0.6)
    assert response.json()["confidence"] == probability
    assert len(llm.calls) == 1


def test_screen_removed_from_core():
    assert not hasattr(core, "jev_p_new")
    assert not hasattr(core, "JEV_DEFAULT_MODE")
