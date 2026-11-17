"""Throttle level, cap maths, the hold decision and the per-process gate, against hand-computed fixtures."""
import json

import pytest

from throughput_under_test import throttle as T
from throughput_under_test.store import Store

NOW = 1_800_000_030  # 30 s into a minute: the current minute is excluded
END = NOW // 60 * 60


@pytest.fixture(autouse=True)
def _fresh_reference_cache():
    T._reference_cache.clear()
    yield
    T._reference_cache.clear()


def _series(tmp_path, rows):
    st = Store(tmp_path / "series.sqlite")
    for gateway, minute, value in rows:
        st.put(gateway, "tokens_io", [(minute, value)])
    return tmp_path / "series.sqlite"


def test_level_round_trip_and_clamp(tmp_path):
    assert T.read_level(tmp_path) == 0.0  # no file: not throttled
    assert T.write_level(tmp_path, 40, now=NOW) == 40.0 and T.read_level(tmp_path) == 40.0
    assert json.loads(T.level_path(tmp_path).read_text())["set_at"] == NOW
    assert T.write_level(tmp_path, 250) == 100.0 and T.write_level(tmp_path, -3) == 0.0
    for bad in ("x", None, float("nan")):
        assert T.clamp(bad) == 0.0
    T.level_path(tmp_path).write_text("{not json")
    assert T.read_level(tmp_path) == 0.0  # an unreadable level never throttles
    T.level_path(tmp_path).write_text("[50]")
    assert T.read_level(tmp_path) == 0.0


def test_cap_ends_and_between():
    assert T.cap(0, 2000.0) is None  # 0 %: nothing is throttled
    assert T.cap(100, 2000.0) == 0.0  # 100 %: everything
    assert T.cap(100, None) == 0.0
    assert T.cap(25, 2000.0) == pytest.approx(1500.0)
    assert T.cap(50, 2000.0) == pytest.approx(1000.0)
    assert T.cap(50, None) is None  # nothing to scale: no cap


def test_should_hold():
    assert not T.should_hold(0, 10**9, 2000.0)
    assert T.should_hold(100, None, None) and T.should_hold(100, 0.0, 2000.0)
    assert T.should_hold(50, 1000.1, 2000.0) and not T.should_hold(50, 1000.0, 2000.0)
    assert not T.should_hold(50, None, 2000.0)  # no measured rate: fail open
    assert not T.should_hold(50, 5000.0, None)  # no reference: fail open


def test_reference_is_the_24h_peak_15_minute_bucket_across_gateways(tmp_path):
    bucket = (END - 6 * 3600) // 900 * 900
    db = _series(tmp_path, [
        ("hub", bucket, 600_000.0), ("worker-1", bucket + 60, 300_000.0),  # one bucket: 900k over 900 s = 1000/s
        ("hub", bucket + 900, 450_000.0),  # the next bucket: 500/s
        ("hub", END - 25 * 3600, 9e9),  # older than 24 hours: ignored
        ("hub", END, 9e9),  # the current minute: ignored
    ])
    assert T.reference_rate(db, NOW) == pytest.approx(1000.0)
    assert T.reference_rate(tmp_path / "missing.sqlite", NOW) is None


def test_reference_is_cached_per_db(tmp_path):
    bucket = (END - 6 * 3600) // 900 * 900
    db = _series(tmp_path, [("hub", bucket, 900_000.0)])
    assert T.reference_rate(db, NOW) == pytest.approx(1000.0)
    Store(db).put("hub", "tokens_io", [(bucket + 900, 1_800_000.0)])  # a new peak of 2000/s
    assert T.reference_rate(db, NOW + T.REFERENCE_TTL_S - 1) == pytest.approx(1000.0)  # still the cached scan
    assert T.reference_rate(db, NOW + T.REFERENCE_TTL_S) == pytest.approx(2000.0)


def test_concurrent_writes_do_not_collide(tmp_path):
    import threading
    errors = []

    def write(p):
        try:
            for _ in range(50):
                T.write_level(tmp_path, p)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(p,)) for p in (10, 20, 30, 40)]
    [t.start() for t in threads], [t.join() for t in threads]
    assert errors == [] and T.read_level(tmp_path) in (10.0, 20.0, 30.0, 40.0)


def test_fleet_rate_is_the_mean_of_the_sampled_recent_minutes(tmp_path):
    db = _series(tmp_path, [
        ("hub", END - 60, 60_000.0), ("worker-1", END - 60, 30_000.0),  # 90k in that minute
        ("hub", END - 120, 30_000.0),  # 30k
        ("hub", END - 180, None),  # sampled as missing: not a zero
        ("hub", END - 240, 9e9),  # outside the 3-minute window
    ])
    assert T.fleet_rate(db, NOW) == pytest.approx((90_000 + 30_000) / 120.0)
    assert T.fleet_rate(tmp_path / "missing.sqlite", NOW) is None


def test_status(tmp_path):
    bucket = (END - 6 * 3600) // 900 * 900
    db = _series(tmp_path, [("hub", bucket, 900_000.0), ("hub", END - 60, 48_000.0)])  # peak 1000/s, now 800/s
    assert T.status(tmp_path, db, NOW) == {"percent": 0.0, "reference_per_s": pytest.approx(1000.0),
                                           "cap_per_s": None, "rate_per_s": pytest.approx(800.0), "holding": False,
                                           "set_at": None, "set_by": None}  # never set, by no one
    T.write_level(tmp_path, 10)
    assert T.status(tmp_path, db, NOW)["cap_per_s"] == pytest.approx(900.0)
    assert T.status(tmp_path, db, NOW)["holding"] is False
    T.write_level(tmp_path, 30)
    out = T.status(tmp_path, db, NOW)
    assert out["cap_per_s"] == pytest.approx(700.0) and out["holding"] is True


class Clock:
    def __init__(self):
        self.t, self.sleeps, self.on_sleep = float(NOW), [], None

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds
        if self.on_sleep:
            self.on_sleep(len(self.sleeps))


def _gate(tmp_path, db):
    clock = Clock()
    return T.Gate(tmp_path, db, sleep=clock.sleep, clock=clock.now), clock


def test_gate_passes_at_zero_and_under_the_cap(tmp_path):
    bucket = (END - 6 * 3600) // 900 * 900
    db = _series(tmp_path, [("hub", bucket, 900_000.0), ("hub", END - 60, 48_000.0)])
    gate, clock = _gate(tmp_path, db)
    assert gate.wait() == 0.0 and clock.sleeps == []
    T.write_level(tmp_path, 10)  # cap 900/s, rate 800/s
    gate, clock = _gate(tmp_path, db)
    assert gate.wait() == 0.0 and clock.sleeps == []


def test_gate_holds_at_100_until_the_level_is_lowered(tmp_path):
    db = _series(tmp_path, [])
    T.write_level(tmp_path, 100)
    gate, clock = _gate(tmp_path, db)
    clock.on_sleep = lambda n: T.write_level(tmp_path, 0) if n == 3 else None
    assert gate.wait() == pytest.approx(3 * T.POLL_S) and len(clock.sleeps) == 3


def test_gate_holds_over_the_cap_until_the_rate_drops(tmp_path):
    bucket = (END - 6 * 3600) // 900 * 900
    db = _series(tmp_path, [("hub", bucket, 900_000.0), ("hub", END - 60, 48_000.0)])
    T.write_level(tmp_path, 30)  # cap 700/s, rate 800/s: hold
    gate, clock = _gate(tmp_path, db)
    st = Store(db)
    clock.on_sleep = lambda n: st.put("hub", "tokens_io", [(END - 60, 30_000.0)]) if n == 2 else None  # 500/s
    assert gate.wait() == pytest.approx(2 * T.POLL_S)


def test_gate_reads_no_series_when_off_or_fully_on(tmp_path, monkeypatch):
    db = _series(tmp_path, [])
    monkeypatch.setattr(T, "status", lambda *a: (_ for _ in ()).throw(AssertionError("series read")))
    gate, clock = _gate(tmp_path, db)
    assert gate.wait() == 0.0  # 0 %: the level file alone decides
    T.write_level(tmp_path, 100)
    gate, clock = _gate(tmp_path, db)
    clock.on_sleep = lambda n: T.write_level(tmp_path, 0)
    assert gate.wait() == pytest.approx(T.POLL_S)


def test_gate_caches_a_passing_state_and_fails_open(tmp_path, monkeypatch):
    db = _series(tmp_path, [])
    T.write_level(tmp_path, 50)
    gate, clock = _gate(tmp_path, db)
    calls = []
    monkeypatch.setattr(T, "status", lambda *a: calls.append(a) or {"holding": False})
    gate.wait(), gate.wait()
    assert len(calls) == 1  # one status read per STATE_TTL_S
    clock.t += T.STATE_TTL_S + 1
    monkeypatch.setattr(T, "status", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert gate.wait() == 0.0  # a broken read never stalls a model call


def test_middleware_waits_then_calls_through(tmp_path):
    db = _series(tmp_path, [])
    T.write_level(tmp_path, 100)
    gate, clock = _gate(tmp_path, db)
    clock.on_sleep = lambda n: T.write_level(tmp_path, 0)
    seen = []
    out = gate.middleware(next_call=lambda request: seen.append(request) or "response", request={"model": "m"},
                          session_id="s", original_request={"model": "m"})
    assert out == "response" and seen == [{"model": "m"}] and len(clock.sleeps) == 1


def test_register_adds_the_execution_middleware_and_the_auxiliary_hold(tmp_path, monkeypatch):
    import throughput_under_test as plugin
    from throughput_under_test import sampler
    from types import SimpleNamespace
    monkeypatch.setattr(sampler, "hermes_root", lambda: tmp_path)
    monkeypatch.setattr(sampler, "eligible_process", lambda *a, **k: False)
    registered, hooks = [], []
    plugin.register(SimpleNamespace(register_middleware=lambda kind, fn: registered.append((kind, fn)),
                                    register_hook=lambda name, fn: hooks.append((name, fn))))
    assert [kind for kind, _ in registered] == ["llm_execution"]
    assert registered[0][1](next_call=lambda request: ("sent", request), request={"m": 1}) == ("sent", {"m": 1})
    assert [name for name, _ in hooks] == ["pre_auxiliary_call"]
    assert hooks[0][1].hermes_hook_timeout == T.AUX_HOOK_TIMEOUT_S  # core reads the budget off the callback
    assert hooks[0][1](aux_task="title_generation", provider="p", model="m") is None  # off: returns at once
    monkeypatch.setattr(sampler, "eligible_process", lambda *a, **k: True)  # a gateway / serve / dashboard process
    monkeypatch.setattr(sampler, "start", lambda *a, **k: False)
    registered.clear(), hooks.clear()
    plugin.register(SimpleNamespace(register_middleware=lambda kind, fn: registered.append((kind, fn)),
                                    register_hook=lambda name, fn: hooks.append((name, fn))))
    assert [kind for kind, _ in registered] == ["llm_execution"] and hooks == []  # never block a server's event loop
    monkeypatch.setattr(sampler, "eligible_process", lambda *a, **k: False)
    registered.clear()
    plugin.register(SimpleNamespace(register_middleware=lambda kind, fn: registered.append((kind, fn))))
    assert [kind for kind, _ in registered] == ["llm_execution"]  # a core without hooks: the main loop still holds
    plugin.register(SimpleNamespace())  # a core without middleware: loading still succeeds


# --- the fleet level ------------------------------------------------------------------------------------------

def _hub_db(tmp_path, records, now=NOW, own=None):
    """A hermes-hub replica holding each gateway's ``gateway/info`` record: ``{gw: (info, age_s)}``."""
    import sqlite3
    path = tmp_path / "hub.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS replica (gw TEXT, coll TEXT, key TEXT, version INTEGER, seq INTEGER, "
              "data TEXT, updated_at INTEGER, PRIMARY KEY (gw, coll, key))")
    c.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    c.execute("DELETE FROM replica")
    c.execute("DELETE FROM meta")
    if own:
        c.execute("INSERT INTO meta VALUES ('gateway', ?)", (own,))
    for gw, (info, age_s) in records.items():
        c.execute("INSERT INTO replica VALUES (?, 'gateway', 'info', 1, 1, ?, ?)",
                  (gw, info if isinstance(info, str) else json.dumps(info), int((now - age_s) * 1000)))
    c.execute("INSERT INTO replica VALUES ('hub', 'tasks', 'default/t_1', 1, 1, ?, ?)",
              (json.dumps({"throttle": {"percent": 100, "set_at": now}}), int(now * 1000)))  # not a gateway record
    c.commit()
    c.close()
    return path


def test_write_level_stores_the_cap_and_status_keeps_it_when_the_peak_moves(tmp_path):
    bucket = (END - 6 * 3600) // 900 * 900
    db = _series(tmp_path, [("hub", bucket, 900_000.0), ("hub", END - 60, 48_000.0)])  # peak 1000/s, now 800/s
    T.write_level(tmp_path, 30, now=NOW - 5, reference=T.reference_rate(db, NOW))
    assert T.read_record(tmp_path) == {"percent": 30.0, "cap_per_s": pytest.approx(700.0), "set_at": NOW - 5}
    Store(db).put("hub", "tokens_io", [(bucket + 900, 3_600_000.0)])  # a new peak of 4000/s
    T._reference_cache.clear()
    out = T.status(tmp_path, db, NOW)
    assert out["reference_per_s"] == pytest.approx(4000.0) and out["cap_per_s"] == pytest.approx(700.0)
    assert out["holding"] is True and out["set_at"] == NOW - 5 and out["set_by"] is None
    T.write_level(tmp_path, 100, reference=1000.0)
    assert T.read_record(tmp_path)["cap_per_s"] == 0.0
    T.write_level(tmp_path, 0, reference=1000.0)
    assert T.read_record(tmp_path)["cap_per_s"] is None
    T.write_level(tmp_path, 40)  # nothing to scale against yet
    assert T.read_record(tmp_path)["cap_per_s"] is None


def test_published_reads_only_gateway_info_records(tmp_path):
    hub = _hub_db(tmp_path, {
        "hub": ({"host": "h", "throttle": {"percent": 30, "cap_per_s": 700.0, "set_at": NOW - 50}}, 20),
        "worker-1": ({"host": "w", "throttle": {"seen_set_at": NOW - 50}}, 30),
        "mini": ({"host": "m"}, 40),  # a client that publishes no throttle block
        "macbook": ({"throttle": "30"}, 40),  # not a block
        "worker-2": ("{broken", 5),
        "worker-3": ("[1]", 5),
    })
    got = T.published(hub)
    assert sorted(got) == ["hub", "macbook", "mini", "worker-1", "worker-3"]
    assert got["hub"] == {"throttle": {"percent": 30, "cap_per_s": 700.0, "set_at": NOW - 50}, "updated_at": NOW - 20}
    assert got["mini"]["throttle"] is None and got["macbook"]["throttle"] is None and got["worker-3"]["throttle"] is None
    assert T.published(tmp_path / "missing.db") == {}
    (tmp_path / "not-sqlite.db").write_text("nope")
    assert T.published(tmp_path / "not-sqlite.db") == {}


def test_effective_is_the_newest_level_any_gateway_published(tmp_path):
    T.write_level(tmp_path, 10, now=NOW - 100, reference=1000.0)
    assert T.effective(tmp_path, None, NOW) == {"percent": 10.0, "cap_per_s": pytest.approx(900.0),
                                                "set_at": NOW - 100, "source": None}
    assert T.effective(tmp_path, tmp_path / "missing.db", NOW)["source"] is None  # no hub link: the local level
    hub = _hub_db(tmp_path, {
        "hub": ({"throttle": {"percent": 60, "cap_per_s": 400.0, "set_at": NOW - 50}}, 20),  # newer than ours
        "mini": ({"throttle": {"percent": 5, "cap_per_s": 950.0, "set_at": NOW - 200}}, 20),  # older
        "worker-9": ({"throttle": {"percent": 100, "cap_per_s": 0, "set_at": NOW + 3600}}, 20),  # a wrong clock
        "worker-1": ({"throttle": {"seen_set_at": NOW - 50}}, 20),  # an acknowledgement is not a setting
        "worker-2": ({"throttle": {"percent": "100", "set_at": NOW}}, 20),  # a malformed level
    })
    assert T.effective(tmp_path, hub, NOW) == {"percent": 60.0, "cap_per_s": 400.0, "set_at": NOW - 50, "source": "hub"}
    T.write_level(tmp_path, 20, now=NOW - 10, reference=1000.0)  # set here after the hub's: ours again
    assert T.effective(tmp_path, hub, NOW)["source"] is None and T.effective(tmp_path, hub, NOW)["percent"] == 20.0
    # a gateway that has been off for a day still counts as a setter: its record is the fleet's last word
    hub = _hub_db(tmp_path, {"worker-5": ({"throttle": {"percent": 100, "cap_per_s": 0.0, "set_at": NOW - 5}}, 90_000)})
    assert T.effective(tmp_path, hub, NOW)["source"] == "worker-5"


def test_stamps_match_at_millisecond_precision_on_the_setter_and_everywhere_else(tmp_path):
    """The hub link rounds published floats to 3 decimals. Gateway A sets at a microsecond stamp; B reads the
    rounded stamp, obeys it and acknowledges it; A's panel must list B as having the level."""
    a_root, b_root = tmp_path / "a", tmp_path / "b"
    T.write_level(a_root, 30, now=1794945300.123456, reference=1000.0)
    assert T.read_record(a_root)["set_at"] == 1794945300.123  # stored as it will be published
    published_a = {"throttle": {"percent": 30, "cap_per_s": 700, "set_at": 1794945300.123}}
    now = 1794945360.0
    hub_b = _hub_db(b_root.parent / "hb", {"A": (published_a, 5)}, now=now) if (b_root.parent / "hb").mkdir() is None else None
    level = T.note_seen(b_root, hub_b, now)
    assert level["source"] == "A" and level["set_at"] == 1794945300.123
    seen_b = json.loads(T.seen_path(b_root).read_text())
    (tmp_path / "ha").mkdir()
    hub_a = _hub_db(tmp_path / "ha", {"B": ({"throttle": {"seen_percent": 30, "seen_set_at": seen_b["set_at"]}}, 5)}, now=now)
    T.note_seen(a_root, hub_a, now)
    assert T.gateways(a_root, hub_a, now, "A") == [{"name": "A", "seen": True}, {"name": "B", "seen": True}]
    # a level file written before this fix (a microsecond stamp on disk) still matches its rounded acknowledgement
    T.level_path(a_root).write_text(json.dumps({"percent": 30.0, "cap_per_s": 700.0, "set_at": 1794945300.123456}))
    T.seen_path(a_root).write_text(json.dumps({"percent": 30.0, "set_at": 1794945300.123456, "at": now}))
    assert T.gateways(a_root, hub_a, now, "A") == [{"name": "A", "seen": True}, {"name": "B", "seen": True}]
    assert T.stamp(None) == 0.0 and T.stamp(True) == 0.0 and T.stamp("x") == 0.0


def test_this_gateways_own_replica_row_is_not_a_second_opinion(tmp_path):
    """The link's replica also holds this gateway's own record, which can lag the local file."""
    T.write_level(tmp_path, 0, now=NOW - 10)
    stale_own = {"throttle": {"percent": 100, "cap_per_s": 0.0, "set_at": NOW - 5, "seen_set_at": NOW - 5}}
    hub = _hub_db(tmp_path, {"macbook": (stale_own, 5), "hub": ({"host": "h"}, 5)}, own="macbook")
    assert sorted(T.published(hub)) == ["hub"]
    assert T.effective(tmp_path, hub, NOW)["percent"] == 0.0
    assert [g["name"] for g in T.gateways(tmp_path, hub, NOW, "this-mac")] == ["this-mac", "hub"]  # listed once
    hub = _hub_db(tmp_path, {"macbook": (stale_own, 5)})  # a replica without the link's own name: it counts
    assert T.effective(tmp_path, hub, NOW)["source"] == "macbook"


def test_equal_stamps_keep_the_local_level_and_the_slack_boundary_is_inclusive(tmp_path):
    T.write_level(tmp_path, 10, now=NOW - 50, reference=1000.0)
    hub = _hub_db(tmp_path, {"hub": ({"throttle": {"percent": 60, "cap_per_s": 400.0, "set_at": NOW - 50}}, 5)})
    assert T.effective(tmp_path, hub, NOW)["source"] is None  # a tie: this gateway's own setting
    hub = _hub_db(tmp_path, {"hub": ({"throttle": {"percent": 60, "set_at": NOW + T.FUTURE_SLACK_S}}, 5)})
    assert T.effective(tmp_path, hub, NOW)["source"] == "hub"
    hub = _hub_db(tmp_path, {"hub": ({"throttle": {"percent": 60, "set_at": NOW + T.FUTURE_SLACK_S + 1}}, 5)})
    assert T.effective(tmp_path, hub, NOW)["source"] is None


def test_an_unusable_call_object_still_holds(tmp_path, monkeypatch):
    db = _series(tmp_path, [])
    T.write_level(tmp_path, 100)
    gate, clock = _gate(tmp_path, db)
    clock.on_sleep = lambda n: T.write_level(tmp_path, 0) if n == 2 else None
    monkeypatch.setattr(T, "agent_of", lambda call: (_ for _ in ()).throw(RuntimeError("odd callable")))
    assert gate.middleware(next_call=lambda request: "r", request={}) == "r" and len(clock.sleeps) == 2


def test_sampler_loop_acknowledges_the_level_each_pass(tmp_path, monkeypatch):
    import threading
    from throughput_under_test import sampler
    T.write_level(tmp_path, 40, now=NOW)
    stop = threading.Event()

    class OneTick:
        def __init__(self, root):
            pass

        def tick(self):
            pass

    monkeypatch.setattr(sampler, "Sampler", OneTick)
    monkeypatch.setattr(stop, "wait", lambda timeout=None: True)  # one pass, then the loop ends
    sampler._loop(stop, tmp_path)
    seen = json.loads(T.seen_path(tmp_path).read_text())
    assert seen["percent"] == 40.0 and seen["set_at"] == NOW


def test_status_and_gate_obey_another_gateways_level(tmp_path):
    bucket = (END - 6 * 3600) // 900 * 900
    db = _series(tmp_path, [("hub", bucket, 900_000.0), ("hub", END - 60, 48_000.0)])  # this host: peak 1000, now 800/s
    hub = _hub_db(tmp_path, {"hub": ({"throttle": {"percent": 30, "cap_per_s": 500.0, "set_at": NOW - 50}}, 20)})
    out = T.status(tmp_path, db, NOW, hub)
    assert out["percent"] == 30.0 and out["cap_per_s"] == 500.0 and out["holding"] is True and out["set_by"] == "hub"
    clock = Clock()
    gate = T.Gate(tmp_path, db, hub_db=hub, sleep=clock.sleep, clock=clock.now)
    clock.on_sleep = lambda n: _hub_db(
        tmp_path, {"hub": ({"throttle": {"percent": 0, "cap_per_s": None, "set_at": NOW + 1}}, 0)}) if n == 2 else None
    assert gate.wait() == pytest.approx(2 * T.POLL_S)  # held by the hub's level, released when the hub turns it off
    hub = _hub_db(tmp_path, {"mini": ({"throttle": {"percent": 100, "cap_per_s": 0.0, "set_at": NOW - 1}}, 5)})
    monkeypatch_free = T.Gate(tmp_path, tmp_path / "no-series.sqlite", hub_db=hub, sleep=clock.sleep, clock=clock.now)
    assert monkeypatch_free.holding() is True  # 100 % from another gateway holds without any local series


def test_note_seen_acknowledges_the_level_and_gateways_lists_who_has_it(tmp_path):
    hub = _hub_db(tmp_path, {
        "hub": ({"throttle": {"percent": 60, "cap_per_s": 400.0, "set_at": NOW - 50, "seen_set_at": NOW - 50}}, 20),
        "worker-1": ({"throttle": {"seen_set_at": NOW - 50}}, 30),
        "mini": ({"throttle": {"seen_set_at": NOW - 900}}, 30),  # still on the previous level
        "macbook": ({"host": "m"}, 30),  # no throttle block: an older client or no plugin
        "worker-2": ({"throttle": {"seen_set_at": NOW - 50}}, T.LIVE_S + 1),  # not running: left out
    })
    assert T.gateways(tmp_path, hub, NOW, "worker-7")[0] == {"name": "worker-7", "seen": False}  # not acknowledged yet
    level = T.note_seen(tmp_path, hub, NOW)
    assert level["source"] == "hub" and level["percent"] == 60.0
    assert json.loads(T.seen_path(tmp_path).read_text()) == {"percent": 60.0, "set_at": NOW - 50, "at": NOW}
    T.note_seen(tmp_path, hub, NOW + 60)
    assert json.loads(T.seen_path(tmp_path).read_text())["at"] == NOW  # unchanged level: the file is not rewritten
    assert T.gateways(tmp_path, hub, NOW, "worker-7") == [
        {"name": "worker-7", "seen": True}, {"name": "hub", "seen": True}, {"name": "macbook", "seen": False},
        {"name": "mini", "seen": False}, {"name": "worker-1", "seen": True}]
    assert T.gateways(tmp_path, hub, NOW, "hub")[0] == {"name": "hub", "seen": True}  # this gateway is listed once
    assert [g["name"] for g in T.gateways(tmp_path, hub, NOW, "hub")] == ["hub", "macbook", "mini", "worker-1"]
    T.write_level(tmp_path, 0, now=NOW + 5)  # a newer level set here
    assert T.gateways(tmp_path, hub, NOW + 6, "worker-7")[:2] == [{"name": "worker-7", "seen": False},
                                                                  {"name": "hub", "seen": False}]
    T.note_seen(tmp_path, hub, NOW + 6)
    assert json.loads(T.seen_path(tmp_path).read_text()) == {"percent": 0.0, "set_at": NOW + 5, "at": NOW + 6}
    assert T.gateways(tmp_path, None, NOW + 6, "solo") == [{"name": "solo", "seen": True}]  # no hub link


# --- liveness while held, and auxiliary calls -----------------------------------------------------------------

class FakeAgent:
    def __init__(self):
        self.touched, self._interrupt_requested = [], False

    def _touch_activity(self, desc):
        self.touched.append(desc)


def _api_call_of(agent, sent):
    def _perform_api_call(request):  # closes over the agent, like core's turn_api_call
        sent.append((agent, request))
        return "response"
    return _perform_api_call


def test_agent_of_finds_the_agent_behind_a_wrapped_call():
    import functools
    agent, sent = FakeAgent(), []
    call = _api_call_of(agent, sent)
    assert T.agent_of(call) is agent
    assert T.agent_of(functools.partial(call)) is agent
    assert T.agent_of(lambda request: call(request)) is agent  # another middleware's wrapper
    assert T.agent_of(agent._touch_activity) is agent  # a bound method
    assert T.agent_of(lambda request: request) is None and T.agent_of(None) is None and T.agent_of(len) is None
    deep = call
    for _ in range(6):
        deep = (lambda inner: lambda request: inner(request))(deep)
    assert T.agent_of(deep) is None  # the search is bounded


def test_a_held_turn_reports_activity_every_poll_and_a_stop_request_ends_the_wait(tmp_path):
    db = _series(tmp_path, [])
    T.write_level(tmp_path, 100)
    gate, clock = _gate(tmp_path, db)
    agent, sent = FakeAgent(), []
    clock.on_sleep = lambda n: T.write_level(tmp_path, 0) if n == 3 else None
    assert gate.middleware(next_call=_api_call_of(agent, sent), request={"m": 1}) == "response"
    assert agent.touched == ["token throttle: waiting (0s)", "token throttle: waiting (5s)",
                             "token throttle: waiting (10s)"]  # once per poll, for as long as it waits
    assert sent == [(agent, {"m": 1})]

    T.write_level(tmp_path, 100)
    gate, clock = _gate(tmp_path, db)
    agent, sent = FakeAgent(), []
    clock.on_sleep = lambda n: setattr(agent, "_interrupt_requested", True) if n == 2 else None
    assert gate.middleware(next_call=_api_call_of(agent, sent), request={"m": 2}) == "response"
    assert len(clock.sleeps) == 2 and sent == [(agent, {"m": 2})]  # the user's stop reaches core's own handling

    gate, clock = _gate(tmp_path, db)
    agent = FakeAgent()
    agent._touch_activity = lambda desc: (_ for _ in ()).throw(RuntimeError("boom"))
    clock.on_sleep = lambda n: T.write_level(tmp_path, 0)
    assert gate.middleware(next_call=_api_call_of(agent, []), request={}) == "response"  # a failing report is ignored


def test_core_execution_chain_hands_the_gate_a_call_that_leads_to_the_agent(tmp_path, monkeypatch):
    """Through core's real ``run_llm_execution_middleware`` (two middlewares registered, ours first): the
    ``next_call`` core builds still leads to the agent, so the held turn reports activity."""
    middleware = pytest.importorskip("hermes_cli.middleware")
    plugins = pytest.importorskip("hermes_cli.plugins")
    db = _series(tmp_path, [])
    T.write_level(tmp_path, 100)
    gate, clock = _gate(tmp_path, db)
    clock.on_sleep = lambda n: T.write_level(tmp_path, 0) if n == 2 else None
    order = []

    def other(*, next_call, request, **_context):
        order.append("other")
        return next_call(request)

    failures = []
    manager = type("Manager", (), {"_middleware": {"llm_execution": [gate.middleware, other]},
                                   "_report_hook_failure": lambda self, *a, **k: failures.append((a, k))})()
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: manager)
    agent, sent = FakeAgent(), []
    out = middleware.run_llm_execution_middleware({"model": "m"}, _api_call_of(agent, sent), session_id="s",
                                                  model="m", provider="p", api_call_count=1)
    assert out == "response" and sent == [(agent, {"model": "m"})] and order == ["other"] and failures == []
    assert agent.touched == ["token throttle: waiting (0s)", "token throttle: waiting (5s)"]


def test_a_held_card_without_an_agent_renews_its_claim(tmp_path, monkeypatch):
    import sys
    import types
    beats = []
    tools = types.ModuleType("tools")
    kanban_tools = types.ModuleType("tools.kanban_tools")
    kanban_tools.heartbeat_current_worker_from_env = lambda: beats.append(1)
    tools.kanban_tools = kanban_tools
    monkeypatch.setitem(sys.modules, "tools", tools)
    monkeypatch.setitem(sys.modules, "tools.kanban_tools", kanban_tools)
    db = _series(tmp_path, [])
    T.write_level(tmp_path, 100)
    gate, clock = _gate(tmp_path, db)
    clock.on_sleep = lambda n: T.write_level(tmp_path, 0) if n == 2 else None
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    gate.middleware(next_call=lambda request: "r", request={})
    assert beats == []  # not a card run
    T.write_level(tmp_path, 100)
    gate, clock = _gate(tmp_path, db)
    clock.on_sleep = lambda n: T.write_level(tmp_path, 0) if n == 2 else None
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    gate.middleware(next_call=lambda request: "r", request={})
    assert beats == [1, 1]


def test_auxiliary_calls_wait_up_to_the_hook_budget_and_the_admission_judge_never_waits(tmp_path):
    db = _series(tmp_path, [])
    T.write_level(tmp_path, 100)
    gate, clock = _gate(tmp_path, db)
    assert gate.aux_hold(aux_task="title_generation", provider="p", model="m") is None
    assert sum(clock.sleeps) == pytest.approx(T.AUX_HOLD_MAX_S)  # still held: the call goes ahead at the bound
    assert T.AUX_HOLD_MAX_S < T.AUX_HOOK_TIMEOUT_S and gate.aux_hold.hermes_hook_timeout == T.AUX_HOOK_TIMEOUT_S
    gate, clock = _gate(tmp_path, db)
    gate.aux_hold(aux_task="hub_admission")
    assert clock.sleeps == []  # the dispatcher's judge is never held
    gate, clock = _gate(tmp_path, db)
    clock.on_sleep = lambda n: T.write_level(tmp_path, 0) if n == 2 else None
    gate.aux_hold(aux_task="compression")
    assert len(clock.sleeps) == 2  # released as soon as the level allows
    gate, clock = _gate(tmp_path, db)
    gate.aux_hold()
    assert clock.sleeps == []  # off: no wait
