"""Nonblocking advisory snapshots, real SQLite workers and joined cancellation."""
import concurrent.futures
import sqlite3
import threading
import time

import pytest

from throughput_under_test import advisory as A, slots as S
from throughput_under_test.cards import Cards
from throughput_under_test.store import Store
from test_slots import _replica, NOW, END, START


@pytest.fixture(autouse=True)
def lifecycle():
    A.stop()
    A.start()
    yield
    A.stop()


def measurement(tmp_path):
    series = tmp_path / "series.db"
    Store(series).put("worker-1", "tokens_io", [(END - 60, 900000)])
    replica = _replica(tmp_path, [("t", "done", START, END, {"holder": "worker-1"}, False)])
    return series, replica


def request(tmp_path, series, replica, now=NOW):
    return {"root": str(tmp_path), "series_db": str(series), "replicas": [str(replica)],
            "now": now, "boot_map": {}}


def join(root):
    owner = A._owners[str(root)]
    owner.thread.join(3)
    assert not owner.thread.is_alive()
    return owner


def test_cold_fresh_expiry_and_current_cap(tmp_path, monkeypatch):
    series, replica = measurement(tmp_path)
    assert S.payload(tmp_path, series, 1000, now=NOW, replicas=[replica]) is None
    owner = join(tmp_path)
    got = S.payload(tmp_path, series, 1000, now=NOW, replicas=[replica])
    assert got["gateways"]["worker-1"]["allowed"] == 1
    assert got["observed_at"] == NOW and got["window_since"] == START and got["window_until"] == END
    assert got["valid"] is True and got["stale"] is False
    assert S.payload(tmp_path, series, 2000, now=NOW, replicas=[replica])["gateways"]["worker-1"]["allowed"] == 2
    got["gateways"]["worker-1"]["allowed"] = 999
    assert S.payload(tmp_path, series, 0, now=NOW, replicas=[replica])["gateways"]["worker-1"]["allowed"] == 0
    # Prevent a new refresh so assertions cover the exact old observation boundary.
    owner.retry_at = float("inf")
    stamp = owner.snapshot[0]
    monkeypatch.setattr(A.time, "monotonic", lambda: stamp + S.CACHE_S - 0.001)
    assert S.payload(tmp_path, series, 1000, now=NOW, replicas=[replica]) is not None
    monkeypatch.setattr(A.time, "monotonic", lambda: stamp + S.CACHE_S)
    assert S.payload(tmp_path, series, 1000, now=NOW, replicas=[replica]) is None
    monkeypatch.setattr(A.time, "monotonic", lambda: stamp)
    assert S.payload(tmp_path, series, 1000, now=NOW + 30, replicas=[replica]) is None
    assert S.payload(tmp_path, series, 1000, now=NOW - 1, replicas=[replica]) is None


def test_boot_snapshot_nonblocking_expiry_and_copy(tmp_path):
    clock = [NOW]
    cards = Cards(tmp_path, clock=lambda: clock[0], replica_dir=tmp_path / "absent")
    assert cards.advisory_snapshot() is None
    cards.events()
    cards._cache[1]["boot_map"]["boot"] = "worker-1"
    snapshot = cards.advisory_snapshot()
    snapshot["boot_map"]["boot"] = "changed"
    assert cards.advisory_snapshot()["boot_map"]["boot"] == "worker-1"
    with cards._lock:
        started = time.monotonic()
        assert cards.advisory_snapshot() is None
        assert time.monotonic() - started < 0.1
    clock[0] += 30
    assert cards.advisory_snapshot() is None


def test_singleflight_blocked_real_replica_and_current_cap(tmp_path, monkeypatch):
    series, replica = measurement(tmp_path)
    blocker = sqlite3.connect(replica)
    blocker.execute("BEGIN EXCLUSIVE")
    entered = threading.Event()
    spawned = []
    real = A.subprocess.Popen

    def spawn(*args, **kwargs):
        proc = real(*args, **kwargs)
        spawned.append(proc)
        entered.set()
        return proc

    monkeypatch.setattr(A.subprocess, "Popen", spawn)
    try:
        assert S.payload(tmp_path, series, 1000, now=NOW, replicas=[replica]) is None
        assert entered.wait(1)
        started = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            out = list(pool.map(lambda _: S.payload(tmp_path, series, 2000, now=NOW, replicas=[replica]), range(20)))
        assert time.monotonic() - started < 1 and out == [None] * 20
        assert len(spawned) == 1 and spawned[0].poll() is None
    finally:
        blocker.rollback()
        blocker.close()
    join(tmp_path)
    assert S.payload(tmp_path, series, 2000, now=NOW, replicas=[replica])["gateways"]["worker-1"]["allowed"] == 2


@pytest.mark.parametrize("fault", ["missing_replica", "missing_series", "empty_usage", "broken_schema", "missing_boot"])
def test_unavailable_is_not_zero_and_retries(tmp_path, fault):
    series, replica = measurement(tmp_path)
    if fault == "missing_replica":
        replica.unlink()
    elif fault == "missing_series":
        series.unlink()
    elif fault == "empty_usage":
        with sqlite3.connect(series) as db:
            db.execute("DELETE FROM minutes")
    elif fault == "broken_schema":
        with sqlite3.connect(replica) as db:
            db.execute("DROP TABLE task_events")
    else:
        with sqlite3.connect(replica) as db:
            db.execute("UPDATE task_events SET payload = ?", ('{"lock":"boot-unknown:1"}',))
    assert S.payload(tmp_path, series, 1000, now=NOW, replicas=[replica]) is None
    owner = join(tmp_path)
    assert owner.snapshot is None
    first = owner.thread
    assert S.payload(tmp_path, series, 1000, now=NOW, replicas=[replica]) is None
    assert owner.thread is first  # failed refresh has a bounded retry backoff
    repaired = tmp_path / "repaired"
    repaired.mkdir()
    series, replica = measurement(repaired)
    owner.retry_at = 0
    S.payload(tmp_path, series, 1000, now=NOW, replicas=[replica])
    join(tmp_path)
    assert S.payload(tmp_path, series, 1000, now=NOW, replicas=[replica]) is not None


@pytest.mark.parametrize("teardown", [False, True])
def test_cancel_kills_reaps_and_joins_real_sqlite_worker(tmp_path, monkeypatch, teardown):
    series, replica = measurement(tmp_path)
    blocker = sqlite3.connect(replica)
    blocker.execute("BEGIN EXCLUSIVE")
    spawned, entered = [], threading.Event()
    real = A.subprocess.Popen

    def spawn(*args, **kwargs):
        proc = real(*args, **kwargs)
        spawned.append(proc)
        entered.set()
        return proc

    monkeypatch.setattr(A.subprocess, "Popen", spawn)
    monkeypatch.setattr(A, "REFRESH_TIMEOUT_S", 0.2)
    started = time.monotonic()
    try:
        S.payload(tmp_path, series, 1000, now=NOW, replicas=[replica])
        assert entered.wait(1)
        owner = A._owners[str(tmp_path)]
        if teardown:
            A.stop()
        else:
            join(tmp_path)
        assert time.monotonic() - started < 1
        assert spawned[0].returncode is not None and spawned[0].returncode != 0
        assert not owner.thread.is_alive() and owner.snapshot is None
        assert all(p.closed for p in (spawned[0].stdin, spawned[0].stdout))
        if teardown:
            assert not A._owners
            assert S.payload(tmp_path, series, 1000, now=NOW, replicas=[replica]) is None
            assert not A._owners
    finally:
        blocker.rollback()
        blocker.close()


def test_stop_and_start_are_serialized(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    owner = A.Owner()
    A._owners[str(tmp_path)] = owner
    real_close = owner.close
    def close():
        entered.set()
        assert release.wait(2)
        real_close()
    monkeypatch.setattr(owner, "close", close)
    stopping = threading.Thread(target=A.stop)
    starting = threading.Thread(target=A.start)
    stopping.start()
    try:
        assert entered.wait(1)
        starting.start()
        starting.join(0.05)
        assert starting.is_alive()
        assert A.read(tmp_path, {}, lambda _: False) is None
    finally:
        release.set()
        stopping.join(2)
        starting.join(2)
    assert not stopping.is_alive() and not starting.is_alive()
    assert not A._stopped and not A._owners


def test_register_owns_reload_cleanup(monkeypatch):
    import throughput_under_test as plugin
    from throughput_under_test import sampler
    callbacks = []
    class Context:
        def on_unload(self, callback):
            callbacks.append(callback)
    monkeypatch.setattr(sampler, "eligible_process", lambda: False)
    plugin.register(Context())
    assert callbacks
    for callback in callbacks:
        callback()
    assert A._stopped
    plugin.register(Context())
    assert not A._stopped
