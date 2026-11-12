"""plugin_api: route shape, single flight and the 15 s cache (FastAPI only; skipped where it isn't installed)."""
import importlib.util
import threading
import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
API = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"


@pytest.fixture
def api(monkeypatch):
    spec = importlib.util.spec_from_file_location("throughput_api_under_test", API)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_single_flight_and_cache(api, monkeypatch):
    calls = []

    def slow(range_key):
        calls.append(range_key)
        time.sleep(0.2)
        return {"range": range_key, "n": len(calls)}

    monkeypatch.setattr(api, "build", slow)
    out = []
    threads = [threading.Thread(target=lambda: out.append(api.cached("6h"))) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == ["6h"] and all(o["n"] == 1 for o in out)
    api.cached("1h")
    assert calls == ["6h", "1h"]  # ranges are cached separately
    api._cache["6h"] = (time.monotonic() - 16, {"stale": True})
    assert api.cached("6h")["n"] == 3  # older than 15 s: rebuilt


def test_route_rejects_unknown_range(api, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setattr(api, "build", lambda r: {"range": r})
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/throughput")
    c = TestClient(app)
    assert c.get("/api/plugins/throughput/v1/series?range=24h").json() == {"range": "24h"}
    assert c.get("/api/plugins/throughput/v1/series?range=2d").status_code == 400
    assert c.get("/api/plugins/throughput/ping").json()["ok"] is True
