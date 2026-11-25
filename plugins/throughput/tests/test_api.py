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


def test_api_lifespan_owns_advisory_refresh(api):
    import importlib
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    advisory = importlib.import_module(api.pkg().__name__ + ".advisory")
    advisory.stop()
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app):
        assert not advisory._stopped
    assert advisory._stopped and not advisory._owners


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


def test_throttle_does_not_wait_for_real_cards_rebuild(api, tmp_path, monkeypatch):
    import concurrent.futures
    import importlib
    import json
    import sqlite3
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HUB_STATE", str(tmp_path / "hub"))
    monkeypatch.setattr(api, "_registry", lambda: {"me": "worker-1"})
    sampler, _ = api._services()
    monkeypatch.setattr(sampler, "start", lambda *a, **k: False)
    cards = api._state["cards"]
    cards.known = lambda: None
    replicas = tmp_path / "replicas"
    replicas.mkdir()
    cards.replica_dir = replicas
    now = time.time()
    with sqlite3.connect(replicas / "cards.db") as db:
        db.executescript("CREATE TABLE tasks (id TEXT, created_at REAL, created_on TEXT, completed_at REAL, status TEXT);"
                         "CREATE TABLE task_events (id INTEGER, task_id TEXT, kind TEXT, payload TEXT, created_at REAL);")
        db.execute("INSERT INTO tasks VALUES ('t', ?, 'worker-1', ?, 'done')", (now - 120, now - 60))
        db.execute("INSERT INTO task_events VALUES (1, 't', 'claimed', ?, ?)",
                   (json.dumps({"holder": "worker-1"}), now - 90))
    entered, release = threading.Event(), threading.Event()
    real_build = cards._build

    def blocked_build(at):
        entered.set()
        assert release.wait(5)
        return real_build(at)

    monkeypatch.setattr(cards, "_build", blocked_build)
    app = FastAPI()
    app.include_router(api.router, prefix="/p")
    slots = importlib.import_module(api.pkg().__name__ + ".slots")
    with TestClient(app) as client, concurrent.futures.ThreadPoolExecutor(3) as pool:
        series = pool.submit(client.get, "/p/v1/series?range=1h")
        try:
            assert entered.wait(1)
            started = time.monotonic()
            get = pool.submit(client.get, "/p/v1/throttle")
            put = pool.submit(client.put, "/p/v1/throttle", json={"percent": 37})
            assert get.result(timeout=0.8).status_code == 200
            response = put.result(timeout=0.8)
            assert time.monotonic() - started < 1
            assert response.status_code == 200 and response.json()["percent"] == 37
            saved = json.loads((tmp_path / "cache/throughput/throttle.json").read_text())
            assert saved["percent"] == 37 and response.json()["set_at"] == saved["set_at"]
            assert response.json()["gateways"] == [{"name": "worker-1", "seen": True}]
            assert not series.done()
        finally:
            release.set()
        result = series.result(timeout=2)
        assert result.status_code == 200
        assert sum(result.json()["series"]["cards_out"]["worker-1"]) == 1
    if hasattr(slots, "stop"):
        slots.stop()


def test_throttle_routes_do_not_wait_for_slots_sqlite(api, tmp_path, monkeypatch):
    import concurrent.futures
    import importlib
    import sqlite3
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_slots import _replica
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HUB_STATE", str(tmp_path / "hub"))
    monkeypatch.setattr(api, "_registry", lambda: {"me": "worker-1"})
    api._services()
    advisory = importlib.import_module(api.pkg().__name__ + ".advisory")
    replicas = tmp_path / "kanban/sync-shadow"
    replicas.mkdir(parents=True)
    replica = _replica(replicas, [])
    blocker = sqlite3.connect(replica)
    blocker.execute("BEGIN EXCLUSIVE")
    entered, processes = threading.Event(), []
    real = advisory.subprocess.Popen
    def spawn(*args, **kwargs):
        process = real(*args, **kwargs)
        processes.append(process)
        entered.set()
        return process
    monkeypatch.setattr(advisory.subprocess, "Popen", spawn)
    app = FastAPI()
    app.include_router(api.router, prefix="/p")
    try:
        with TestClient(app) as client, concurrent.futures.ThreadPoolExecutor(2) as pool:
            assert client.get("/p/v1/throttle").json()["slots"] is None
            # setup, not the property: the slots reader process has started (a contended runner took over 1 s
            # to spawn it). The routes' own no-wait bound is the timed block below.
            assert entered.wait(15)
            started = time.monotonic()
            get = pool.submit(client.get, "/p/v1/throttle")
            put = pool.submit(client.put, "/p/v1/throttle", json={"percent": 42})
            assert get.result(timeout=0.8).json()["slots"] is None
            result = put.result(timeout=0.8).json()
            assert result["percent"] == 42 and result["slots"] is None
            assert time.monotonic() - started < 1
            assert len(processes) == 1 and processes[0].poll() is None
            assert client.get("/p/v1/throttle").json()["percent"] == 42
        assert processes[0].poll() is not None  # shutdown reaped even while the replica remains locked
    finally:
        blocker.rollback()
        blocker.close()


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
    bucket = (end - 6 * 3600) // 900 * 900
    store.put("hub", "tokens_io", [(bucket, 900_000.0), (end - 60, 48_000.0)])  # peak 1000/s, now 800/s
    pacing.write_text(json.dumps({"openai-codex": {"samples": [
        [now - 3000, "a@example.com", "seven_day", 70.0, now + 86400], [now, "a@example.com", "seven_day", 75.0, now + 86400]]}}))
    app = FastAPI()
    app.include_router(api.router, prefix="/p")
    c = TestClient(app)

    got = c.get("/p/v1/throttle").json()
    assert got["percent"] == 0 and got["cap_per_s"] is None and got["holding"] is False and got["scope"] == "hub"
    assert got["fleet"] is False and got["gateways"] == [{"name": "hub", "seen": False}]  # no hub link here
    assert got["set_at"] is None and got["set_by"] is None
    assert got["reference_per_s"] == pytest.approx(1000.0) and got["rate_per_s"] == pytest.approx(800.0)
    (pool,) = got["pools"]
    assert pool["provider"] == "openai-codex" and pool["remaining_pct"] == 25 and pool["runway_s"] == 15000
    assert "example.com" not in json.dumps(got)

    put = c.put("/p/v1/throttle", json={"percent": 30}).json()
    assert put["percent"] == 30 and put["cap_per_s"] == pytest.approx(700.0) and put["holding"] is True
    stored = json.loads((root / "cache" / "throughput" / "throttle.json").read_text())
    assert stored["percent"] == 30 and stored["cap_per_s"] == pytest.approx(700.0)  # the cap is stored in tokens/s
    assert put["set_at"] == stored["set_at"] and put["set_by"] is None
    assert put["gateways"] == [{"name": "hub", "seen": True}]  # this gateway acknowledges at once
    seen = json.loads((root / "cache" / "throughput" / "throttle-seen.json").read_text())
    assert seen["set_at"] == stored["set_at"] and seen["percent"] == 30
    store.put("hub", "tokens_io", [(bucket + 900, 1_800_000.0)])  # the peak doubles later: the stored cap stays
    importlib.import_module(api.pkg().__name__ + ".throttle")._reference_cache.clear()
    later = c.get("/p/v1/throttle").json()
    assert later["reference_per_s"] == pytest.approx(2000.0) and later["cap_per_s"] == pytest.approx(700.0)
    assert c.get("/p/v1/throttle").json()["percent"] == 30
    assert c.put("/p/v1/throttle", json={"percent": 100}).json()["cap_per_s"] == 0
    assert c.put("/p/v1/throttle", json={"percent": 0}).json()["holding"] is False
    # a hub link on this host: the fleet, another gateway's acknowledgement, and a setter whose clock runs ahead
    import sqlite3
    hub_dir = tmp_path / "hublink"
    hub_dir.mkdir()
    monkeypatch.setenv("HERMES_HUB_STATE", str(hub_dir))
    con = sqlite3.connect(hub_dir / "hub.db")
    con.execute("CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, version INTEGER, seq INTEGER, data TEXT, "
                "updated_at INTEGER, PRIMARY KEY (gw, coll, key))")
    con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    ahead = time.time() + 120
    con.execute("INSERT INTO replica VALUES ('mini', 'gateway', 'info', 1, 1, ?, ?)", (json.dumps(
        {"throttle": {"percent": 100, "cap_per_s": 0.0, "set_at": ahead}}), int(time.time() * 1000)))
    con.commit()
    got = c.get("/p/v1/throttle").json()
    assert got["fleet"] is True and got["percent"] == 100 and got["set_by"] == "mini" and got["holding"] is True
    assert got["gateways"] == [{"name": "hub", "seen": False}, {"name": "mini", "seen": False}]
    put = c.put("/p/v1/throttle", json={"percent": 0}).json()  # set here now: it must outrank mini's later stamp
    assert put["percent"] == 0 and put["set_by"] is None and put["set_at"] > ahead and put["holding"] is False
    con.execute("UPDATE replica SET data = ?", (json.dumps(
        {"throttle": {"percent": 100, "cap_per_s": 0.0, "set_at": ahead, "seen_set_at": put["set_at"]}}),))
    con.commit()
    con.close()
    assert c.get("/p/v1/throttle").json()["gateways"] == [{"name": "hub", "seen": True}, {"name": "mini", "seen": True}]
    for bad in ({"percent": 101}, {"percent": -1}, {"percent": "50"}, {"percent": True}, {"percent": None}, {}):
        assert c.put("/p/v1/throttle", json=bad).status_code == 400, bad
    for not_an_object in (50, [50]):
        assert c.put("/p/v1/throttle", json=not_an_object).status_code == 422, not_an_object
    assert c.get("/p/v1/throttle").json()["percent"] == 0  # a refused write changes nothing


def test_put_on_a_budget_level_keeps_its_pool_caps(api, tmp_path, monkeypatch):
    """The slider on the budget-publish host's level: only percent and set_at change, a reserve cap of 0 stays."""
    import importlib
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(api, "_registry", lambda: {"me": "hub", "machines": {"hub": {}}})
    monkeypatch.setattr(api, "_known_machines", lambda: None)
    api._services()
    root = api._state["root"]
    runway = importlib.import_module(api.pkg().__name__ + ".runway")
    monkeypatch.setattr(runway, "pacing_path", lambda _root: tmp_path / "llm_pacing.json")
    throttle = importlib.import_module(api.pkg().__name__ + ".throttle")
    now = time.time()
    level = {"mode": "auto", "percent": 0, "cap_per_s": 1000.0, "pool_caps": {"anthropic": 0.0, "openai-codex": 600.0},
             "budget_at": now - 60, "set_at": now - 30}
    throttle.level_path(root).parent.mkdir(parents=True, exist_ok=True)
    throttle.level_path(root).write_text(json.dumps(level))
    app = FastAPI()
    app.include_router(api.router, prefix="/p")
    put = TestClient(app).put("/p/v1/throttle", json={"percent": 25}).json()
    stored = json.loads(throttle.level_path(root).read_text())
    assert stored["mode"] == "auto" and stored["percent"] == 25 and stored["pool_caps"] == level["pool_caps"]
    assert stored["cap_per_s"] == 1000.0 and stored["budget_at"] == pytest.approx(level["budget_at"], abs=1e-3)
    assert stored["set_at"] > level["set_at"]
    assert put["mode"] == "auto" and put["cap_per_s"] == pytest.approx(750.0)
    assert put["budget"]["pool_caps"] == {"anthropic": 0.0, "openai-codex": pytest.approx(450.0)}
    gate = throttle.Gate(root, tmp_path / "none.sqlite")
    assert gate.plan(time.time(), "anthropic") == ("hold",)


def _isolated(api, tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(api, "_registry", lambda: {"me": "hub", "machines": {"hub": {}}})
    monkeypatch.setattr(api, "_known_machines", lambda: None)
    api._services()
    assert str(tmp_path) in str(api._state["store"].path), api._state["store"].path  # never the real series db
    runway = importlib.import_module(api.pkg().__name__ + ".runway")
    monkeypatch.setattr(runway, "pacing_path", lambda _root: tmp_path / "llm_pacing.json")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(api.router, prefix="/p")
    return TestClient(app), importlib.import_module(api.pkg().__name__ + ".throttle"), api._state["root"]


def test_burst_routes_set_end_and_refuse(api, tmp_path, monkeypatch):
    """PUT/DELETE /v1/throttle/burst: refused (409) on a manual level, validated (400), stored on a budget level with
    newest-wins stamping, shown in the payload, ended at once by DELETE; the slider keeps it."""
    import json
    c, throttle, root = _isolated(api, tmp_path, monkeypatch)
    manual = c.put("/p/v1/throttle/burst", json={"factor": 2, "hours": 1})
    assert manual.status_code == 409 and "manual" in manual.json()["detail"]
    assert c.get("/p/v1/throttle").json()["burst"] is None
    now = time.time()
    level = {"mode": "auto", "percent": 0, "cap_per_s": 1000.0, "pool_caps": {"anthropic": 0.0, "openai-codex": 600.0},
             "budget_at": now - 60, "set_at": now - 30}
    throttle.level_path(root).write_text(json.dumps(level))
    for bad in ({"factor": 1, "hours": 1}, {"factor": 4.5, "hours": 1}, {"factor": 2, "hours": 0},
                {"factor": 2, "hours": 25}, {"factor": "2", "hours": 1}, {"factor": True, "hours": 1}, {"factor": 2}, {}):
        assert c.put("/p/v1/throttle/burst", json=bad).status_code == 400, bad
    got = c.put("/p/v1/throttle/burst", json={"factor": 2, "hours": 1}).json()
    stored = json.loads(throttle.level_path(root).read_text())
    assert stored["set_at"] > level["set_at"] and stored["pool_caps"] == level["pool_caps"]
    assert stored["burst"]["factor"] == 2.0 and stored["burst"]["until"] == pytest.approx(stored["set_at"] + 3600)
    assert got["burst"]["active"] is True and got["burst"]["remaining_s"] == pytest.approx(3600, abs=5)
    assert got["cap_per_s"] == pytest.approx(2000.0)
    assert got["budget"]["pool_caps"] == {"anthropic": 0.0, "openai-codex": pytest.approx(1200.0)}
    moved = c.put("/p/v1/throttle", json={"percent": 50}).json()  # the slider keeps the burst
    assert moved["burst"]["factor"] == 2.0 and moved["cap_per_s"] == pytest.approx(1000.0)
    ended = c.delete("/p/v1/throttle/burst").json()
    assert ended["burst"] is None and ended["cap_per_s"] == pytest.approx(500.0) and ended["percent"] == 50
    assert "burst" not in json.loads(throttle.level_path(root).read_text())
    assert c.delete("/p/v1/throttle/burst").status_code == 200  # nothing to end: unchanged


def test_pacing_route_switches_budget_pacing_off_and_on(api, tmp_path, monkeypatch):
    """PUT /v1/throttle/pacing {on}: off stores an explicit manual level (burst and pool caps dropped, the burst route
    then refuses, the slider keeps it off), on stores a level the publisher folds in; validated (400 / 422) without a
    write; answers with the GET payload and its ``pacing``."""
    import json
    c, throttle, root = _isolated(api, tmp_path, monkeypatch)
    now = int(time.time()) + 0.0006
    # Keep the response on the write's clock tick: millisecond rounding and
    # newest-wins stamps can legitimately put the saved level just ahead of now.
    monkeypatch.setattr(time, "time", lambda: now)
    level = {"mode": "auto", "percent": 20.0, "cap_per_s": 1000.0, "pool_caps": {"anthropic": 0.0},
             "budget_at": now - 60, "set_at": now - 30, "burst": {"factor": 2.0, "until": now + 3000, "set_at": now - 30}}
    throttle._write_json(throttle.level_path(root), level)
    assert c.get("/p/v1/throttle").json()["pacing"] == {"state": "on", "publisher": "hub", "budget_seen": True}
    before = throttle.level_path(root).read_text()
    for bad in ({}, {"on": "false"}, {"on": 1}, {"on": 0}, {"on": None}):
        got = c.put("/p/v1/throttle/pacing", json=bad)
        assert got.status_code == 400 and got.json()["detail"] == "on must be true or false", bad
    assert c.put("/p/v1/throttle/pacing", json=[False]).status_code == 422
    assert throttle.level_path(root).read_text() == before  # a refused request writes nothing
    off = c.put("/p/v1/throttle/pacing", json={"on": False})
    assert off.status_code == 200
    off = off.json()
    stored = json.loads(throttle.level_path(root).read_text())
    assert set(stored) == {"mode", "percent", "cap_per_s", "set_at"} and stored["mode"] == "manual"
    assert stored["percent"] == 20.0 and stored["set_at"] > level["set_at"]
    assert off["pacing"] == {"state": "off", "publisher": None, "budget_seen": True} and off["mode"] == "manual"
    assert off["burst"] is None and off["budget"] is None and off["gateways"] == [{"name": "hub", "seen": True}]
    assert c.put("/p/v1/throttle/burst", json={"factor": 2, "hours": 1}).status_code == 409
    moved = c.put("/p/v1/throttle", json={"percent": 50}).json()
    assert moved["pacing"]["state"] == "off" and json.loads(throttle.level_path(root).read_text())["mode"] == "manual"
    again = json.loads(throttle.level_path(root).read_text())
    assert c.put("/p/v1/throttle/pacing", json={"on": False}).json()["pacing"]["state"] == "off"
    assert json.loads(throttle.level_path(root).read_text()) == again  # already off: nothing written
    on = c.put("/p/v1/throttle/pacing", json={"on": True}).json()
    stored = json.loads(throttle.level_path(root).read_text())
    assert "mode" not in stored and stored["percent"] == 50.0 and stored["set_at"] > again["set_at"]
    assert on["pacing"] == {"state": "pending", "publisher": None, "budget_seen": True}  # the On just clicked here


def test_budget_view_endpoint_shape(api, tmp_path, monkeypatch):
    """GET /v1/budget: this host's v2 for the chart (no account readings), 7 days of hourly spend per provider, the
    burst in force, and the burst projection when factor and hours are given."""
    import json
    c, throttle, root = _isolated(api, tmp_path, monkeypatch)
    now = time.time()
    until = int(now // 60 * 60)
    store = api._state["store"]
    store.put("hub", "tokens_io:claude-opus-5-5", [(until - 120, 7200.0)])
    store.put("hub", "tokens_io:unreported", [(until - 120, 0.0)])
    v2 = {"computed_at": now - 30, "margin_s": 10800, "global_rate_tokens_per_s": 100.0,
          "pools": {"anthropic": {"rate_tokens_per_s": 100.0, "remaining_tokens": 1_000_000.0,
                                  "exhaust_plan_at": now + 10_000, "at_reserve": False,
                                  "accounts": [{"id": "0123abcd", "used": 50.0}]}},
          "events": [{"ts": now - 600, "provider": "anthropic", "account": "0123abcd", "type": "reset",
                      "detail": {"kind": "seven_day"}}],
          "curve": {"anthropic": [[now - 30, 1_000_000.0, 0.0], [now + 10_000, 2_000_000.0, 1_000_000.0]]}}
    (tmp_path / "llm_pacing.json").write_text(json.dumps({"anthropic": {"samples": []}, "v2": v2}))
    throttle.level_path(root).parent.mkdir(parents=True, exist_ok=True)
    throttle.level_path(root).write_text(json.dumps({
        "mode": "auto", "percent": 0, "cap_per_s": 100.0, "pool_caps": {"anthropic": 100.0}, "budget_at": now - 30,
        "set_at": now - 20, "burst": {"factor": 2.0, "until": now + 1800, "set_at": now - 20}}))
    started = time.monotonic()
    got = c.get("/p/v1/budget").json()
    assert time.monotonic() - started < 2.0
    assert set(got) == {"now", "computed_at", "margin_s", "global_rate_tokens_per_s", "pools", "events", "curve",
                        "history", "burst", "projection"}
    assert got["pools"]["anthropic"]["accounts"] == 1 and got["pools"]["anthropic"]["rate_tokens_per_s"] == 100.0
    assert got["events"] == [{"ts": pytest.approx(now - 600), "provider": "anthropic", "type": "reset",
                              "kind": "seven_day"}]
    assert len(got["curve"]["anthropic"]) == 2 and got["projection"] is None
    h = got["history"]
    assert h["bucket_s"] == 3600 and len(h["t"]) == len(h["providers"]["anthropic"]["rate"]) in (168, 169)
    assert h["providers"]["anthropic"]["cumulative"][-1] == 7200.0
    assert got["burst"]["factor"] == 2.0 and got["burst"]["active"] is True
    # the history is read at most once a minute per process: a new series row shows only after the cache expires
    store.put("hub", "tokens_io:claude-opus-5-5", [(until - 60, 3600.0)])
    assert c.get("/p/v1/budget").json()["history"]["providers"]["anthropic"]["cumulative"][-1] == 7200.0
    api._history_cache.clear()
    assert c.get("/p/v1/budget").json()["history"]["providers"]["anthropic"]["cumulative"][-1] == 10800.0
    only = c.get("/p/v1/budget?factor=2&hours=1&projection_only=1").json()
    assert set(only) == {"now", "computed_at", "burst", "projection"}
    assert only["projection"]["pools"]["anthropic"]["extra_tokens"] == pytest.approx(360_000.0)
    assert c.get("/p/v1/budget?projection_only=1").status_code == 400
    proj = c.get("/p/v1/budget?factor=2&hours=1").json()["projection"]
    assert proj["factor"] == 2.0 and proj["hours"] == 1.0
    assert proj["pools"]["anthropic"]["extra_tokens"] == pytest.approx(360_000.0)
    assert proj["pools"]["anthropic"]["earlier_s"] == pytest.approx(3600.0)
    for bad in ("factor=2", "hours=1", "factor=1&hours=1", "factor=2&hours=30", "factor=x&hours=1"):
        assert c.get("/p/v1/budget?" + bad).status_code in (400, 422), bad
    (tmp_path / "llm_pacing.json").unlink()  # a host without core's capacity model: empty, never an error
    bare = c.get("/p/v1/budget?factor=2&hours=1").json()
    assert bare["pools"] == {} and bare["curve"] == {} and bare["projection"]["pools"] == {}


def test_put_cap_per_min_sets_a_persons_absolute_cap(api, tmp_path, monkeypatch):
    """PUT /v1/throttle {cap_per_min}: a manual level in tokens/s, the slot gate's limit in the payload, null
    removes the cap, and bad values are refused without a write."""
    import importlib
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(api, "_registry", lambda: {"me": "hub", "machines": {"hub": {}}})
    monkeypatch.setattr(api, "_known_machines", lambda: None)
    api._services()
    root = api._state["root"]
    runway = importlib.import_module(api.pkg().__name__ + ".runway")
    monkeypatch.setattr(runway, "pacing_path", lambda _root: tmp_path / "llm_pacing.json")
    slotgate = importlib.import_module(api.pkg().__name__ + ".slotgate")
    monkeypatch.setattr(slotgate, "active_runs", lambda: 4)
    throttle = importlib.import_module(api.pkg().__name__ + ".throttle")
    throttle._write_json(slotgate.estimate_path(root), {"per_s": 50.0, "at": time.time() - 30})
    app = FastAPI()
    app.include_router(api.router, prefix="/p")
    c = TestClient(app)

    put = c.put("/p/v1/throttle", json={"cap_per_min": 18_000}).json()  # 300 tokens/s
    stored = json.loads(throttle.level_path(root).read_text())
    assert stored["mode"] == "manual" and stored["cap_per_s"] == pytest.approx(300.0) and 0 < stored["percent"] < 1  # older code reads 0 % as off
    assert put["cap_per_s"] == pytest.approx(300.0) and put["user_cap_per_s"] == pytest.approx(300.0)
    assert put["mode"] == "manual" and put["gateways"] == [{"name": "hub", "seen": True}]
    gate = put["slot_gate"]
    assert gate["running"] == 4 and gate["per_slot_per_s"] == 50.0 and gate["source"] == "measured"
    assert gate["allowed"] == 6  # the only gateway: share 1, floor(300 / 50)
    assert c.put("/p/v1/throttle", json={"cap_per_min": 0}).json()["slot_gate"]["allowed"] == 0
    off = c.put("/p/v1/throttle", json={"cap_per_min": None}).json()
    assert off["cap_per_s"] is None and off["slot_gate"]["allowed"] is None
    for bad in ({"cap_per_min": -5}, {"cap_per_min": "lots"}, {"cap_per_min": True}, {"cap_per_min": 1e13}):
        assert c.put("/p/v1/throttle", json=bad).status_code == 400, bad
    assert c.get("/p/v1/throttle").json()["cap_per_s"] is None  # a refused write changes nothing
