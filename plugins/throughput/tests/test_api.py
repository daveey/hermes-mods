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


def test_cache_is_keyed_by_range_and_by(api, monkeypatch):
    calls = []

    def build(range_key, by="gateway"):
        calls.append((range_key, by))
        return {"range": range_key, "by": by}

    monkeypatch.setattr(api, "build", build)
    assert api.cached("6h")["by"] == "gateway" and api.cached("6h", "gateway") == api.cached("6h")
    assert api.cached("6h", "model")["by"] == "model" and api.cached("6h", "model")["by"] == "model"
    assert api.cached("1h", "model")["range"] == "1h"
    assert calls == [("6h", "gateway"), ("6h", "model"), ("1h", "model")]  # one build per (range, by) within 15 s
    api._cache["6h|model"] = (time.monotonic() - 16, {"stale": True})
    assert api.cached("6h", "model") == {"range": "6h", "by": "model"} and calls[-1] == ("6h", "model")


def test_route_by_param_validation(api, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setattr(api, "build", lambda r, by="gateway": {"range": r, "by": by})
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/throughput")
    c = TestClient(app)
    base = "/api/plugins/throughput/v1/series"
    assert c.get(base + "?range=1h").json() == {"range": "1h", "by": "gateway"}
    assert c.get(base + "?range=1h&by=gateway").json() == {"range": "1h", "by": "gateway"}
    assert c.get(base + "?range=1h&by=model").json() == {"range": "1h", "by": "model"}
    for bad in ("user", "", "Model", "model,gateway"):
        assert c.get(base + f"?range=1h&by={bad}").status_code == 400
    assert c.get(base + "?range=2d&by=model").status_code == 400


def test_real_build_by_model_end_to_end(api, tmp_path, monkeypatch):
    """The route's real build() path (store + cards + series) for both views on an isolated root."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(api, "_registry", lambda: {"me": "hub", "machines": {"hub": {}}})
    monkeypatch.setattr(api, "_known_machines", lambda: None)
    sampler, _ = api._services()
    monkeypatch.setattr(sampler, "start", lambda *a, **k: False)
    store = api._state["store"]
    assert str(tmp_path) in str(store.path), store.path  # never the real series db
    minute = int(time.time() // 60 * 60) - 120
    store.put("hub", "tokens_io:m-a", [(minute, 7.0)])
    store.put("hub", "tokens_io:unreported", [(minute, 1.0)])
    store.put("hub", "tokens_io", [(minute, 8.0)])
    app = FastAPI()
    app.include_router(api.router, prefix="/p")
    c = TestClient(app)
    g = c.get("/p/v1/series?range=1h").json()
    m = c.get("/p/v1/series?range=1h&by=model").json()
    assert "by" not in g and g["series"]["tokens_io"]["hub"][-2] == 8.0
    assert m["by"] == "model" and m["models"] == ["m-a", "unreported"] and m["series"]["tokens_io"]["m-a"][-2] == 7.0


def test_throttle_routes_end_to_end(api, tmp_path, monkeypatch):
    """GET/PUT /v1/throttle on an isolated root: level round trip, cap from the stored peak, runway, validation."""
    import importlib
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(api, "_registry", lambda: {"me": "hub", "machines": {"hub": {}}})
    monkeypatch.setattr(api, "_known_machines", lambda: None)
    api._services()
    store, root = api._state["store"], api._state["root"]
    assert str(tmp_path) in str(store.path), store.path  # never the real series db
    runway = importlib.import_module(api.pkg().__name__ + ".runway")
    pacing = tmp_path / "llm_pacing.json"
    monkeypatch.setattr(runway, "pacing_path", lambda _root: pacing)
    importlib.import_module(api.pkg().__name__ + ".throttle")._reference_cache.clear()
    now = time.time()
    end = int(now // 60 * 60)
    bucket = (end - 86400) // 900 * 900
    store.put("hub", "tokens_io", [(bucket, 900_000.0), (end - 60, 48_000.0)])  # peak 1000/s, now 800/s
    pacing.write_text(json.dumps({"openai-codex": {"samples": [
        [now - 3000, "a@example.com", "seven_day", 70.0, now + 86400], [now, "a@example.com", "seven_day", 75.0, now + 86400]]}}))
    app = FastAPI()
    app.include_router(api.router, prefix="/p")
    c = TestClient(app)

    got = c.get("/p/v1/throttle").json()
    assert got["percent"] == 0 and got["cap_per_s"] is None and got["holding"] is False and got["scope"] == "hub"
    assert got["reference_per_s"] == pytest.approx(1000.0) and got["rate_per_s"] == pytest.approx(800.0)
    (pool,) = got["pools"]
    assert pool["provider"] == "openai-codex" and pool["remaining_pct"] == 25 and pool["runway_s"] == 15000
    assert "example.com" not in json.dumps(got)

    put = c.put("/p/v1/throttle", json={"percent": 30}).json()
    assert put["percent"] == 30 and put["cap_per_s"] == pytest.approx(700.0) and put["holding"] is True
    assert json.loads((root / "cache" / "throughput" / "throttle.json").read_text())["percent"] == 30
    assert c.get("/p/v1/throttle").json()["percent"] == 30
    assert c.put("/p/v1/throttle", json={"percent": 100}).json()["cap_per_s"] == 0
    assert c.put("/p/v1/throttle", json={"percent": 0}).json()["holding"] is False
    for bad in ({"percent": 101}, {"percent": -1}, {"percent": "50"}, {"percent": True}, {"percent": None}, {}):
        assert c.put("/p/v1/throttle", json=bad).status_code == 400, bad
    for not_an_object in (50, [50]):
        assert c.put("/p/v1/throttle", json=not_an_object).status_code == 422, not_an_object
    assert c.get("/p/v1/throttle").json()["percent"] == 0  # a refused write changes nothing
