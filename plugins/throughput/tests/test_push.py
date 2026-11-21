"""The per-minute snapshot push (dashboard/plugin_api.py Pusher) and the /v1/watch catch-up."""
import importlib.util
import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
API = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"
MINUTE = 1_800_000_000


@pytest.fixture
def api():
    spec = importlib.util.spec_from_file_location("throughput_push_api_under_test", API)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make(api, wall, mono, throttle=None, series=None):
    sent = []
    p = api.Pusher(sent.append, throttle or (lambda: {"percent": 50, "slots": {"gateways": {}}, "cap_per_s": float("nan")}),
                   series or (lambda r, by: {"range": r, "by": by, "series": {}}),
                   clock=lambda: mono[0], wall=lambda: wall[0])
    return p, sent


def test_one_push_per_minute_after_the_advisory_flight_and_none_without_interest(api):
    wall, mono = [MINUTE + 1.0], [100.0]
    p, sent = make(api, wall, mono)
    assert p.step(kicked=False) is False and sent == []  # nobody interested: nothing is built or pushed
    p.note([("6h", "gateway")])
    assert p.step(kicked=False) is True and len(sent) == 1  # first interest: an immediate push
    for t in (5, 10, 19):  # the rest of the minute: no deadline push, the minute already has one
        wall[0] = MINUTE + t
        assert p.step(kicked=False) is False
    wall[0] = MINUTE + 63.5  # next minute: its advisory flight finished -> push
    assert p.step(kicked=True) is True and len(sent) == 2
    wall[0] = MINUTE + 60 + api.PUSH_DEADLINE_S
    assert p.step(kicked=False) is False  # already pushed this minute
    wall[0] = MINUTE + 120 + api.PUSH_DEADLINE_S  # a minute whose flight never finished: the deadline pushes
    assert p.step(kicked=False) is True and len(sent) == 3
    mono[0] += api.INTEREST_S  # five minutes without a read or watch: silence, nothing built
    wall[0] = MINUTE + 240 + api.PUSH_DEADLINE_S
    assert p.step(kicked=True) is False and len(sent) == 3
    assert [b["seq"] for b in sent] == [1, 2, 3] and len({b["epoch"] for b in sent}) == 1


def test_payload_is_json_safe_carries_watched_series_and_is_bounded(api, monkeypatch):
    wall, mono = [MINUTE + 30.0], [0.0]
    p, sent = make(api, wall, mono)
    for i, key in enumerate([("1h", "gateway"), ("6h", "gateway"), ("24h", "gateway"), ("7d", "gateway"),
                             ("6h", "model")]):
        mono[0] = float(i)
        p.note([key])
    p.step(kicked=True)
    body = sent[-1]
    json.loads(json.dumps(body, allow_nan=False))  # NaN became null
    assert body["throttle"]["cap_per_s"] is None and body["feed"] == api.FEED_ID
    # The four most recently watched views; the oldest (1h) dropped.
    assert body["series_keys"] == ["24h|gateway", "6h|gateway", "6h|model", "7d|gateway"]
    assert body["series"]["6h|model"] == {"range": "6h", "by": "model", "series": {}}
    assert body["minute"] == MINUTE and body["interval_s"] == 60
    monkeypatch.setattr(api, "PUSH_MAX_BYTES", 200)
    p.step(kicked=True)
    big = sent[-1]
    assert big["series"] == {} and big["series_keys"] == body["series_keys"]  # by name only: clients read them


def test_a_failing_build_still_pushes_what_it_has(api):
    wall, mono = [MINUTE + 30.0], [0.0]
    def broken():
        raise OSError("db locked")
    p, sent = make(api, wall, mono, throttle=broken)
    p.note([("6h", "gateway")])
    p.step(kicked=True)
    assert sent[-1]["throttle"] is None and "6h|gateway" in sent[-1]["series"]


def test_advisory_flight_kicks_the_push_thread_for_this_root_only(api, tmp_path):
    wall, mono = [MINUTE + 30.0], [0.0]
    p, _ = make(api, wall, mono)
    api._state["root"] = tmp_path
    p.on_flight(str(tmp_path / "other"), True)
    assert not p.kick.is_set()
    p.on_flight(str(tmp_path), True)
    assert p.kick.is_set()


def test_no_push_thread_without_a_gateway_transport(api):
    wall, mono = [MINUTE + 30.0], [0.0]
    p, _ = make(api, wall, mono)
    p.note()
    assert p.thread is None  # tests and CLI imports have no tui_gateway: nobody to push to


def test_watch_route_catches_up_and_get_throttle_names_the_feed(api, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(api, "_registry", lambda: {"me": "worker-1"})
    monkeypatch.setattr(api, "cached", lambda r, by="gateway": {"range": r, "by": by})
    app = FastAPI()
    app.include_router(api.router, prefix="/p")
    with TestClient(app) as client:
        got = client.post("/p/v1/watch", json={"series": [{"range": "1h", "by": "model"}, {"range": "bad"}]}).json()
        assert got["feed"] == api.FEED_ID and got["interest_s"] == 300.0
        assert got["series"] == {"1h|model": {"range": "1h", "by": "model"}}
        assert got["throttle"]["feed"] == api.FEED_ID and "percent" in got["throttle"]
        assert client.get("/p/v1/throttle").json()["feed"] == api.FEED_ID
        assert api.pusher().interested()


def test_push_thread_pushes_on_a_kick_and_exits_without_interest(api, monkeypatch):
    import threading
    monkeypatch.setattr(api.Pusher, "can_push", staticmethod(lambda: True))
    mono = [100.0]
    got = threading.Event()
    sent = []
    p = api.Pusher(lambda b: (sent.append(b), got.set()), lambda: {"percent": 1}, lambda r, by: {},
                   clock=lambda: mono[0], wall=lambda: MINUTE + 1.0)
    p.note()
    thread = p.thread
    assert got.wait(2) and len(sent) == 1  # the first interest pushes at once
    got.clear()
    p.kick.set()  # the minute's advisory flight finished
    assert got.wait(2) and len(sent) == 2
    mono[0] += api.INTEREST_S
    p.kick.set()
    thread.join(2)
    assert not thread.is_alive() and p.thread is None  # no interest: the thread is gone, nothing more pushed
    assert len(sent) == 2
    p.stop()


@pytest.mark.parametrize("advisory", [True, False])
def test_watch_builds_the_throttle_once_with_advisory_interest_only_when_asked(api, tmp_path, monkeypatch, advisory):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(api, "_registry", lambda: {"me": "worker-1"})
    builds = []
    monkeypatch.setattr(api, "throttle_payload", lambda now=None, interest=True: builds.append(interest) or {"percent": 3})
    app = FastAPI()
    app.include_router(api.router, prefix="/p")
    with TestClient(app) as client:
        got = client.post("/p/v1/watch", json={"series": [], "advisory": advisory}).json()
    assert got["throttle"] == {"percent": 3}
    assert builds == [advisory]  # one build, recording advisory interest only when asked


def test_watch_advisory_flag_reaches_the_real_slots_interest(api, tmp_path, monkeypatch):
    import importlib
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(api, "_registry", lambda: {"me": "worker-1"})
    app = FastAPI()
    app.include_router(api.router, prefix="/p")
    with TestClient(app) as client:
        advisory = importlib.import_module(api.pkg().__name__ + ".advisory")
        client.post("/p/v1/watch", json={"advisory": False})
        owner = advisory._owners.get(str(api._state["root"]))
        assert owner is None or not owner.interested()  # a chart-only watcher keeps nothing computing
        client.post("/p/v1/watch", json={"advisory": True})
        assert advisory._owners[str(api._state["root"])].interested()


def test_lifespan_shutdown_stops_the_pusher(api, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(api, "_registry", lambda: {"me": "worker-1"})
    app = FastAPI()
    app.include_router(api.router, prefix="/p")
    with TestClient(app) as client:
        client.get("/p/v1/throttle")
        p = api.pusher()
        assert not p.stopped.is_set()
    assert p.stopped.is_set() and "p" not in api._pusher
