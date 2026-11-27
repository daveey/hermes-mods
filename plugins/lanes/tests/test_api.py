"""plugin_api: answers without lanes support, the read cache, validated writes (FastAPI only)."""
import contextlib
import importlib.util
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
API = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"


@pytest.fixture
def api():
    spec = importlib.util.spec_from_file_location("lanes_api_under_test", API)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Core:
    """Stand-in for core's kanban_db_connect / kanban_lanes / kanban_lanes_cli."""

    def __init__(self):
        self.snapshots, self.sets, self.admits = 0, [], []
        self.configured = True
        self.slow = None
        self.broken = None

    @contextlib.contextmanager
    def connect_closing(self):
        yield object()

    def _fleet(self, conn, local):
        assert local is False
        return None

    def policy(self):
        if self.broken:
            raise self.broken
        return object() if self.configured else None

    def snapshot(self, _conn, *, hours):
        self.snapshots += 1
        if self.broken:
            raise self.broken
        if self.slow:
            self.slow()
        if not self.configured:
            return {"configured": False}
        return {"configured": True, "window_hours": hours, "lanes": {"product": {"share": 35}}}

    def set_lane(self, lane, **changes):
        if lane != "product":
            raise ValueError(f"unknown lane: {lane}")
        self.sets.append((lane, changes))

    def held(self, _conn, lane):
        return [{"id": "t_1", "title": "infra", "tenant": lane or "infra", "created_at": 5}]

    def admit(self, _conn, card, *, actor, reason, budget):
        assert budget is False  # a panel admit never spends the planner's daily budget
        self.admits.append((card, actor, reason))
        return card == "t_1"


@pytest.fixture
def core(api, monkeypatch):
    fake = Core()
    monkeypatch.setattr(api, "core", lambda: (fake, fake, fake))
    # This fixture is a legacy core. Never import the installed core's real
    # flow on its fake connection (cold imports can exceed the REST warm budget).
    monkeypatch.setattr(api, "flow_core", lambda: None)
    monkeypatch.setattr(api, '_projector_enabled', False)
    return fake


def test_worker_limit_and_null_clear_supported(api, core):
    core.UNSET = object()
    api.change("product", {"max_workers": 2, "cards_per_day": None, "usd_per_day": None})
    assert core.sets[-1][1] == {"share": None, "admit_per_day": None, "paused": None,
                                "max_workers": 2, "cards_per_day": None, "usd_per_day": None}


@pytest.mark.parametrize("body", [{"max_workers": 0}, {"max_workers": None},
                                   {"cards_per_day": None, "usd_per_day": None}])
def test_nullable_valves_through_rest(api, core, body):
    from fastapi.testclient import TestClient
    core.UNSET = object()
    app = fastapi.FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        response = client.put("/v1/lanes/product", json=body)
    assert response.status_code == 200
    assert all(core.sets[-1][1][key] == value for key, value in body.items())


def test_old_core_null_clear_rejected_before_any_write(api, core):
    with pytest.raises(fastapi.HTTPException) as err:
        api.change("product", {"share": 40, "cards_per_day": None})
    assert err.value.status_code == 409
    assert not core.sets


def test_old_core_workers_rejected_before_any_write(api, core):
    def old(lane, *, share=None, admit_per_day=None, paused=None, cards_per_day=None, usd_per_day=None):
        core.sets.append((lane, share))
    core.set_lane = old
    with pytest.raises(fastapi.HTTPException) as err:
        api.change("product", {"share": 40, "max_workers": 2})
    assert err.value.status_code == 409
    assert not core.sets


def test_core_without_lanes_says_so(api, monkeypatch):
    monkeypatch.setattr(api, "core", lambda: None)
    assert api.read(6.0) == {"configured": False, "reason": "this Hermes core has no lanes support yet"}
    with pytest.raises(fastapi.HTTPException) as err:
        api.change("product", {"share": 1})
    assert err.value.status_code == 409


def test_unconfigured_lanes_carry_a_reason(api, core):
    core.configured = False
    out = api.read(6.0)
    assert out["configured"] is False and "kanban.admission.lanes" in out["reason"]
    for call in (lambda: api.change("product", {"share": 1}), lambda: api.held(None), lambda: api.admit("t_1", "x")):
        with pytest.raises(fastapi.HTTPException) as err:
            call()
        assert err.value.status_code == 409
    assert not core.sets and not core.admits


def test_reads_are_cached_per_window_and_a_write_drops_the_cache(api, core):
    api.read(6.0, now=100)
    api.read(6.0, now=105)
    assert core.snapshots == 1
    api.read(24.0, now=105)
    api.read(6.0, now=100 + api.CACHE_S + 1)
    assert core.snapshots == 3
    api.change("product", {"share": 50, "paused": True})
    assert core.sets == [("product", {"share": 50, "admit_per_day": None, "paused": True})]
    assert core.snapshots == 4  # the write returned a fresh read


def test_daily_limits_reach_core_and_flow_is_served_cached(api, core, monkeypatch):
    api.change("product", {"cards_per_day": 20, "usd_per_day": 12.5})
    assert core.sets == [("product", {"share": None, "admit_per_day": None, "paused": None,
                                      "cards_per_day": 20, "usd_per_day": 12.5})]
    calls = []

    class Flow:
        def snapshot(self, _conn, *, window):
            calls.append(window)
            return {"window": window, "cards": [], "lanes": {"product": {"done": 1}}}

    monkeypatch.setattr(api, "flow_core", lambda: (core, Flow()))
    assert api.flow("6h", now=100)["configured"] is True
    api.flow("6h", now=101)
    assert calls == ["6h"]
    api.change("product", {"share": 5})
    api.flow("6h", now=102)
    assert calls == ["6h", "6h"]  # a write drops the cached flow
    monkeypatch.setattr(api, "flow_core", lambda: None)
    assert api.flow("1h")["configured"] is False


@pytest.mark.parametrize("replicated,broken", [(True, False), (False, False), (True, True)])
def test_flow_uses_fleet_and_closes_replica(api, core, monkeypatch, replicated, broken):
    from types import SimpleNamespace

    closed, seen = [], []
    replica = SimpleNamespace(close=lambda: closed.append(True)) if replicated else None
    local = object()

    @contextlib.contextmanager
    def connect():
        yield local

    def fleet(conn, local):
        assert local is False
        seen.append(conn)
        return replica

    def snapshot(conn, *, window):
        assert conn is (replica if replicated else local)
        if broken:
            raise ValueError("snapshot failed")
        return {"cards": [{"id": "worker-card"}], "lanes": {"product": {}}}

    monkeypatch.setattr(core, "connect_closing", connect)
    monkeypatch.setattr(core, "_fleet", fleet)
    monkeypatch.setattr(api, "flow_core", lambda: (core, SimpleNamespace(snapshot=snapshot)))
    result = api.flow("6h")
    assert seen == [local]
    assert closed == ([True] if replicated else [])
    assert result["configured"] is (not broken)
    if not broken:
        assert result["cards"] == [{"id": "worker-card"}]


@pytest.mark.parametrize("field", ["cards_per_day", "usd_per_day"])
def test_old_core_caps_return_clear_409_without_writing(api, core, monkeypatch, field):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    def old_set(lane, *, share, admit_per_day, paused):
        core.sets.append(lane)

    monkeypatch.setattr(core, "set_lane", old_set)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.put("/v1/lanes/product", json={field: 20, "share": 5})
        assert response.status_code == 409
        assert response.json()["detail"] == "this Hermes core does not support the requested lane limits yet"
        assert not core.sets
        assert client.put("/v1/lanes/product", json={"share": 5}).status_code == 200


def test_flow_route_validates_the_window(api, core, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api, "flow_core", lambda: None)
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    assert client.get("/v1/lanes/flow?window=2h").status_code == 400
    assert client.get("/v1/lanes/flow?window=24h").json()["flow"]["configured"] is False


@pytest.mark.parametrize("body", [{"weight": 3}, {"paused": "yes"}])
def test_bad_write_bodies_are_refused(api, core, body):
    with pytest.raises(ValueError):
        api.change("product", body)
    assert not core.sets


def test_unknown_lane_is_refused(api, core):
    with pytest.raises(ValueError):
        api.change("nope", {"share": 1})


def test_held_and_admit(api, core):
    assert api.held("infra")["cards"] == [{"id": "t_1", "title": "infra", "lane": "infra", "created_at": 5}]
    assert api.admit("t_1", "needed") == {"admitted": True}
    assert api.admit("t_2", "needed") == {"admitted": False}
    assert core.admits[0] == ("t_1", "user (lanes panel)", "needed")


def test_a_read_that_began_before_a_write_is_not_cached(api, core):
    core.slow = lambda: (setattr(core, "slow", None), api.change("product", {"share": 50}))
    stale = api.read(24.0, now=100)
    assert stale["configured"] is True
    before = core.snapshots
    api.read(24.0, now=101)
    assert core.snapshots == before + 1  # the pre-write snapshot was not kept


def test_routes_over_http(api, core):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    assert client.get("/v1/lanes?hours=7").status_code == 400
    assert client.get("/v1/lanes?hours=24").json()["window_hours"] == 24
    assert client.put("/v1/lanes/nope", json={"share": 1}).status_code == 400
    assert client.put("/v1/lanes/product", json={"weight": 1}).status_code == 400
    assert client.put("/v1/lanes/product", json={"share": 40}).json()["configured"] is True
    assert client.get("/v1/held?lane=infra").json()["cards"][0]["id"] == "t_1"
    assert client.post("/v1/held/t_1/admit", json={"reason": "needed"}).json() == {"admitted": True}


@pytest.mark.parametrize("error", [ValueError("lane name bad"), OSError("config.yaml unreadable")])
def test_unreadable_or_invalid_config_is_a_reason_not_a_crash(api, core, error):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    core.broken = error
    out = api.read(6.0)
    assert out["configured"] is False and str(error) in out["reason"]
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    assert client.get("/v1/held").status_code == 409
    assert client.put("/v1/lanes/product", json={"share": 1}).status_code == 409
    assert client.post("/v1/held/t_1/admit", json={}).status_code == 409


@pytest.mark.parametrize('head_field', ['head', 'head_sha', 'headRefOid', 'published_pr', 'reviewed_pr'])
def test_provider_key_ignores_local_status_but_keeps_pr_evidence(api, head_field):
    """Core-free public-CI check of the actual context builder, with a private DB."""
    import json
    import sqlite3
    from types import SimpleNamespace

    with contextlib.closing(sqlite3.connect(':memory:')) as db:
        db.row_factory = sqlite3.Row
        db.executescript('CREATE TABLE tasks(id,status,completion_contract); '
                         'CREATE TABLE task_events(id INTEGER PRIMARY KEY,task_id,kind,payload);')
        url = 'https://github.com/example/repo/pull/1'
        db.execute("INSERT INTO tasks VALUES('fixture','running',?)", (url,))
        db.execute("INSERT INTO task_events(task_id,kind,payload) VALUES('fixture','created',?)",
                   (json.dumps({'metadata': {'published_pr': url, 'head': 'old'}}),))
        flow = SimpleNamespace(_payload=lambda event: json.loads(event['payload']))
        def key():
            evidence = SimpleNamespace(rows=db.execute('SELECT * FROM tasks').fetchall(),
                events=lambda tid: db.execute('SELECT * FROM task_events WHERE task_id=?', (tid,)).fetchall(),
                pr_url=lambda row: row['completion_contract'])
            return api._pr_contexts(db, flow, evidence=evidence)
        initial = key()
        for status in ('scheduled', 'ready', 'running'):
            db.execute('UPDATE tasks SET status=?', (status,))
            db.execute("INSERT INTO task_events(task_id,kind,payload) VALUES('fixture',?,'{}')", (status,))
            assert key() == initial
        db.execute("INSERT INTO task_events(task_id,kind,payload) VALUES('fixture','external_watch',?)",
                   (json.dumps({'metadata': {head_field: 'new'}}),))
        assert key() != initial, 'explicit provider evidence must still invalidate'


def test_clearing_admit_per_day_is_an_explicit_removal_not_a_noop(api, core, monkeypatch):
    removed = []
    monkeypatch.setattr(api, "_unset_admit_per_day", lambda lane: removed.append(lane))
    api.change("product", {"admit_per_day": None})
    assert removed == ["product"] and not core.sets  # set_lane would reject "nothing to set"
    api.change("product", {"share": 30, "admit_per_day": None})
    assert removed == ["product", "product"] and core.sets == [("product", {"share": 30, "admit_per_day": None, "paused": None})]


def test_core_backed_admit_per_day_clear_reads_back(api, tmp_path, monkeypatch):
    pytest.importorskip("hermes_cli.kanban_lanes_cli")
    import yaml
    from hermes_cli import kanban_lanes as lanes, kanban_lanes_cli as cli
    from hermes_cli import kanban_db_connect as kbc

    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"kanban": {"admission": {"lanes": {
        "product": {"share": 20, "admit_per_day": 17}, "other": {"share": 10}}}}}))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(api, "core", lambda: (kbc, lanes, cli))
    monkeypatch.setattr(api, "read", lambda *a, **k: {})
    assert lanes.policy().lanes["product"].admit_per_day == 17
    api.change("product", {"share": 30, "admit_per_day": None})  # clear + another field
    after = lanes.policy().lanes["product"]
    assert after.admit_per_day is None and after.share == 30
    cli.set_lane("product", admit_per_day=9)
    api.change("product", {"admit_per_day": None})  # clear alone
    assert lanes.policy().lanes["product"].admit_per_day is None
