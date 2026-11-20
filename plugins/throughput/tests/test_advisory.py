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
    from types import SimpleNamespace
    # Patch both module globals. setattr(time, "monotonic") does not reach slots.payload's
    # `import time` on every interpreter, so the CACHE_S boundary can stay "fresh".
    mono = [NOW]
    fake_time = SimpleNamespace(time=lambda: NOW, monotonic=lambda: mono[0])
    monkeypatch.setattr(A, "time", fake_time)
    monkeypatch.setattr(S, "time", fake_time)
    series, replica = measurement(tmp_path)
    assert S.payload(tmp_path, series, 1000, now=NOW, replicas=[replica]) is None
    owner = join(tmp_path)
    with owner.lock:
        owner.retry_at = float("inf")
        owner.next_at = float("inf")
    got = S.payload(tmp_path, series, 1000, now=NOW, replicas=[replica])
    assert got["gateways"]["worker-1"]["allowed"] == 1
    assert got["observed_at"] == NOW and got["window_since"] == START and got["window_until"] == END
    assert got["valid"] is True and got["stale"] is False
    assert S.payload(tmp_path, series, 2000, now=NOW, replicas=[replica])["gateways"]["worker-1"]["allowed"] == 2
    got["gateways"]["worker-1"]["allowed"] = 999
    assert S.payload(tmp_path, series, 0, now=NOW, replicas=[replica])["gateways"]["worker-1"]["allowed"] == 0
    stamp = owner.snapshot[0]
    mono[0] = stamp + S.CACHE_S - 0.001
    assert S.payload(tmp_path, series, 1000, now=NOW, replicas=[replica]) is not None
    mono[0] = stamp + S.CACHE_S
    assert S.payload(tmp_path, series, 1000, now=NOW, replicas=[replica]) is None
    mono[0] = stamp
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


@pytest.mark.parametrize("interval", [15, 30])
def test_steady_poll_availability(tmp_path, monkeypatch, interval):
    """Drive the real coordinator/worker across minutes, without waiting wall minutes."""
    from types import SimpleNamespace
    clock = [NOW]
    fake_time = SimpleNamespace(time=lambda: clock[0], monotonic=lambda: clock[0])
    monkeypatch.setattr(A, "time", fake_time)
    monkeypatch.setattr(S, "time", fake_time)
    series, replica = measurement(tmp_path)
    # Every later share window still contains a sampled minute and this completed run.
    read = lambda: S.payload(tmp_path, series, 1000, replicas=[replica])
    assert read() is None
    owner = join(tmp_path)
    available = []
    for step in range(5, 181, 5):
        clock[0] = NOW + step
        if hasattr(owner, "wake"):
            owner.wake.set()
        # Give a due background flight its execution turn BEFORE the next client read.
        # No read is allowed to trigger or repair that flight in this synchronization.
        if step % 10 == 0 or int(clock[0]) % 60 == 0:
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                if owner.snapshot and owner.snapshot[1]["observed_at"] == clock[0]:
                    break
                time.sleep(0.005)
        if step % interval == 0:
            got = read()
            available.append(got is not None)
            if got:
                assert got["window_until"] == int(clock[0]) // 60 * 60
                assert 0 <= clock[0] - got["observed_at"] < S.CACHE_S
    assert all(available), available


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


def test_scheduler_idle_restart_and_failed_refresh(tmp_path, monkeypatch):
    from types import SimpleNamespace
    clock = [NOW]
    fake_time = SimpleNamespace(time=lambda: clock[0], monotonic=lambda: clock[0])
    monkeypatch.setattr(A, "time", fake_time)
    monkeypatch.setattr(S, "time", fake_time)
    series, replica = measurement(tmp_path)
    read = lambda: S.payload(tmp_path, series, 1000, replicas=[replica])
    assert read() is None
    owner = join(tmp_path)
    scheduler = owner.scheduler
    # A refresh failure must invalidate even a not-yet-expired old observation.
    replica.unlink()
    first = owner.thread
    clock[0] += 10
    owner.wake.set()
    wait_for(lambda: owner.thread is not first)
    join(tmp_path)
    assert owner.snapshot is None and read() is None
    failed = owner.thread
    assert read() is None and owner.thread is failed
    # Repair, then let the scheduler retry without a request initiating the flight.
    repaired = tmp_path / "repair"
    repaired.mkdir()
    _, new_replica = measurement(repaired)
    replica.write_bytes(new_replica.read_bytes())
    clock[0] += A.RETRY_S
    owner.wake.set()
    wait_for(lambda: owner.thread is not failed)
    join(tmp_path)
    assert read() is not None
    # No polling -> stop background work at the exact activity boundary.
    clock[0] += A.ACTIVE_S
    owner.wake.set()
    scheduler.join(2)
    assert not scheduler.is_alive()
    last = owner.thread
    clock[0] += 60
    assert owner.thread is last
    assert read() is None
    join(tmp_path)
    new_scheduler = owner.scheduler
    assert new_scheduler is not scheduler and new_scheduler.is_alive()
    A.stop()
    assert not new_scheduler.is_alive() and not owner.thread.is_alive()


def test_proactive_flight_current_cap_and_joined_cancellation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    clock = [NOW]
    fake_time = SimpleNamespace(time=lambda: clock[0], monotonic=lambda: clock[0])
    monkeypatch.setattr(A, "time", fake_time)
    monkeypatch.setattr(S, "time", fake_time)
    series, replica = measurement(tmp_path)
    assert S.payload(tmp_path, series, 1000, replicas=[replica]) is None
    owner = join(tmp_path)
    scheduler = owner.scheduler
    spawned = []
    real = A.subprocess.Popen
    def spawn(*args, **kwargs):
        process = real(*args, **kwargs)
        spawned.append(process)
        return process
    monkeypatch.setattr(A.subprocess, "Popen", spawn)
    blocker = sqlite3.connect(replica)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        clock[0] += 10
        owner.wake.set()
        wait_for(lambda: bool(spawned))
        assert spawned[0].poll() is None
        for _ in range(10):
            got = S.payload(tmp_path, series, 2000, replicas=[replica])
            assert got["gateways"]["worker-1"]["allowed"] == 2
        assert len(spawned) == 1
        clock[0] += 30
        assert S.payload(tmp_path, series, 2000, replicas=[replica]) is None
        started = time.monotonic()
        A.stop()
        assert time.monotonic() - started < 1
        assert not scheduler.is_alive() and not owner.thread.is_alive()
        assert spawned[0].returncode is not None
        assert spawned[0].stdin.closed and spawned[0].stdout.closed
    finally:
        blocker.rollback()
        blocker.close()


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
