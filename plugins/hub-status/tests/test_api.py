"""Real ASGI dispatch of the hub-status router against a fake HERMES_HOME."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "gateway_state.json").write_text(json.dumps({"gateway_state": "running", "pid": os.getpid()}))
    sys.modules.pop("_hub_status_core", None)
    spec = importlib.util.spec_from_file_location(
        "hub_status_api_test", Path(__file__).resolve().parents[1] / "dashboard/plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("HUB_STATUS_NO_SAMPLER", "1")
    spec.loader.exec_module(api)
    core = api._core()
    monkeypatch.setattr(core, "hermes_home", lambda: tmp_path)
    monkeypatch.setattr(core, "unit_state", lambda: "active")
    monkeypatch.setattr(core.status, "__defaults__", (None, None, lambda: "active"))
    log = core.EventLog(tmp_path / "hub-status")
    log.append({"ts": 1.0, "state": "down", "reason": "x", "pid": None})
    log.append({"ts": 2.0, "state": "up", "reason": "running", "pid": os.getpid()})
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/hub-status")
    yield TestClient(app)
    sys.modules.pop("_hub_status_core", None)


def test_status_and_events_routes(client):
    assert client.get("/api/plugins/hub-status/ping").json() == {"ok": True, "api": 1}
    s = client.get("/api/plugins/hub-status/status").json()
    assert s["state"] == "up" and s["pid"] == os.getpid() and s["since"] > 0
    e = client.get("/api/plugins/hub-status/events", params={"range": "7d"}).json()
    assert e["start"] == pytest.approx(e["now"] - 7 * 86400)
    # both events are older than 7 d (ts 1, 2): the window leads with the state in force
    assert [(x["state"], x.get("clipped")) for x in e["events"]] == [("up", True)]
    bad = client.get("/api/plugins/hub-status/events", params={"range": "nope"}).json()
    assert bad["start"] == pytest.approx(bad["now"] - 86400)
