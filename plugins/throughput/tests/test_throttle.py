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


def test_reference_is_the_7d_peak_15_minute_bucket_across_gateways(tmp_path):
    bucket = (END - 3 * 86400) // 900 * 900
    db = _series(tmp_path, [
        ("hub", bucket, 600_000.0), ("worker-1", bucket + 60, 300_000.0),  # one bucket: 900k over 900 s = 1000/s
        ("hub", bucket + 900, 450_000.0),  # the next bucket: 500/s
        ("hub", END - 8 * 86400, 9e9),  # older than 7 days: ignored
        ("hub", END, 9e9),  # the current minute: ignored
    ])
    assert T.reference_rate(db, NOW) == pytest.approx(1000.0)
    assert T.reference_rate(tmp_path / "missing.sqlite", NOW) is None


def test_reference_is_cached_per_db(tmp_path):
    bucket = (END - 86400) // 900 * 900
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
    bucket = (END - 86400) // 900 * 900
    db = _series(tmp_path, [("hub", bucket, 900_000.0), ("hub", END - 60, 48_000.0)])  # peak 1000/s, now 800/s
    assert T.status(tmp_path, db, NOW) == {"percent": 0.0, "reference_per_s": pytest.approx(1000.0),
                                           "cap_per_s": None, "rate_per_s": pytest.approx(800.0), "holding": False}
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
    bucket = (END - 86400) // 900 * 900
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
    bucket = (END - 86400) // 900 * 900
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


def test_register_adds_one_llm_execution_middleware(tmp_path, monkeypatch):
    import throughput_under_test as plugin
    from throughput_under_test import sampler
    from types import SimpleNamespace
    monkeypatch.setattr(sampler, "hermes_root", lambda: tmp_path)
    monkeypatch.setattr(sampler, "eligible_process", lambda *a, **k: False)
    registered = []
    plugin.register(SimpleNamespace(register_middleware=lambda kind, fn: registered.append((kind, fn))))
    assert [kind for kind, _ in registered] == ["llm_execution"]
    assert registered[0][1](next_call=lambda request: ("sent", request), request={"m": 1}) == ("sent", {"m": 1})
    plugin.register(SimpleNamespace())  # a core without middleware: loading still succeeds
