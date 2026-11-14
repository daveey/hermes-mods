"""Fixture-only lifecycle proofs; no production database or plugin manager."""
import gc
import importlib.util
import traceback
import weakref
import sqlite3
import sys
import threading
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parents[1]


def load_generation(monkeypatch, number):
    name = f"risk_lifecycle_generation_{number}"
    spec = importlib.util.spec_from_file_location(name, HERE / "__init__.py",
                                               submodule_search_locations=[str(HERE)])
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, mod)
    spec.loader.exec_module(mod)
    return mod


def event(number):
    return {"tool": "read_file", "args": {"path": "~/.ssh/config"},
            "at": 1.0, "call_id": str(number)}


def track_connections(monkeypatch, store):
    opened, closed = [], []
    real_connect = store.connect

    class Tracked:
        def __init__(self, db):
            self.db = db
            self.owner = threading.get_ident()
            opened.append(self)

        def execute(self, *args):
            return self.db.execute(*args)

        def close(self):
            assert threading.get_ident() == self.owner
            self.db.close()
            closed.append(self)

    monkeypatch.setattr(store, "connect", lambda *a, **kw: Tracked(real_connect(*a, **kw)))
    return opened, closed


def test_successive_generations_unload_threads_and_connections(tmp_path, monkeypatch):
    retired = []
    for generation in range(4):
        mod = load_generation(monkeypatch, generation)
        mod.WRITER.path = tmp_path / f"generation-{generation}.sqlite"
        mod.WRITER.detector = False
        opened, closed = track_connections(monkeypatch, mod.store)
        callbacks = []
        mod.register(SimpleNamespace(register_hook=lambda *a: None, on_unload=callbacks.append))
        mod.WRITER.submit(event(generation))
        assert mod.WRITER.flush(2)
        assert len(opened) == 1
        assert len(callbacks) == 1, "plugin must register writer retirement on unload"
        callbacks[0]()
        retired.append(mod.WRITER._thread)
        assert not any(t.is_alive() for t in retired)
        assert closed == opened
        mod.WRITER.submit(event("late"))
        assert mod.WRITER.queue.unfinished_tasks == 0
        assert mod.WRITER.dropped == 1
        assert not mod.WRITER._thread.is_alive()
        assert mod.WRITER.shutdown(0)  # repeated retirement is safe


def test_write_error_closes_before_reconnect(tmp_path, monkeypatch):
    mod = load_generation(monkeypatch, "error")
    writer = mod.store.Writer(tmp_path / "error.sqlite", detector=False)
    opened, closed = track_connections(monkeypatch, mod.store)
    real_write = mod.store.write_rows

    def fail_once(db, rows):
        if len(opened) == 1:
            raise sqlite3.OperationalError("fixture write failure")
        assert closed == opened[:-1], "failed handle must close before reconnect"
        real_write(db, rows)

    monkeypatch.setattr(mod.store, "write_rows", fail_once)
    try:
        writer.submit(event(1))
        assert writer.flush(2)
        assert closed == opened, "error path leaked the connection"
        writer.submit(event(2))
        assert writer.flush(2)
        assert writer.errors == 1
    finally:
        assert writer.shutdown(2)
    assert closed == opened
    with closing(sqlite3.connect(writer.path)) as db:
        assert db.execute("SELECT count(*) FROM risk_events").fetchone()[0] == 1


def test_connection_setup_failure_closes_handle(tmp_path, monkeypatch):
    mod = load_generation(monkeypatch, "setup")
    real_connect = sqlite3.connect
    opened, closed = [], []

    class BrokenSchema(sqlite3.Connection):
        def executescript(self, sql):
            raise sqlite3.OperationalError("fixture schema failure")

        def close(self):
            closed.append(self)
            super().close()

    def connect(*args, **kwargs):
        db = real_connect(*args, **kwargs, factory=BrokenSchema)
        opened.append(db)
        return db

    monkeypatch.setattr(mod.store.sqlite3, "connect", connect)
    writer = mod.store.Writer(tmp_path / "setup.sqlite", detector=False)
    try:
        writer.submit(event(1))
        assert writer.flush(2)
        assert writer.errors == 1
        assert opened and closed == opened
    finally:
        assert writer.shutdown(2)


def test_full_queue_drains_after_bounded_shutdown_timeout(tmp_path, monkeypatch):
    mod = load_generation(monkeypatch, "drain")
    monkeypatch.setattr(mod.store, "QUEUE_MAX", 3)
    writer = mod.store.Writer(tmp_path / "drain.sqlite", detector=False)
    opened, closed = track_connections(monkeypatch, mod.store)
    entered, release = threading.Event(), threading.Event()
    real_write = mod.store.write_rows

    def stalled_write(db, rows):
        entered.set()
        assert release.wait(3), "test must release its own stalled writer"
        real_write(db, rows)

    monkeypatch.setattr(mod.store, "write_rows", stalled_write)
    try:
        writer.submit(event(0))
        assert entered.wait(2)
        for number in range(1, 4):
            writer.submit(event(number))
        assert writer.queue.full()
        writer.submit(event("overflow"))
        assert writer.dropped == 1
        start = time.monotonic()
        assert not writer.shutdown(0.02)
        assert time.monotonic() - start < 0.5
        writer.submit(event("retired"))
        assert writer.dropped == 2
        assert closed == [], "caller must not close a handle still used by its owner"
    finally:
        release.set()
        assert writer.shutdown(2)
    assert opened == closed
    assert writer.queue.unfinished_tasks == 0
    with closing(sqlite3.connect(writer.path)) as db:
        assert db.execute("SELECT count(*) FROM risk_events").fetchone()[0] == 4


def test_unused_writer_retirement_cannot_start_later(tmp_path, monkeypatch):
    mod = load_generation(monkeypatch, "unused")
    writer = mod.store.Writer(tmp_path / "unused.sqlite", detector=False)
    assert writer.shutdown(0)
    writer.submit(event(0))
    assert writer._thread is None
    assert writer.dropped == 1
    assert not writer.path.exists()


def test_idle_writer_blocks_until_shutdown(tmp_path, monkeypatch):
    mod = load_generation(monkeypatch, "idle")
    writer = mod.store.Writer(tmp_path / "idle.sqlite", detector=False)
    calls = []
    real_get = writer.queue.get

    def observed_get(*args, **kwargs):
        calls.append((args, kwargs))
        return real_get(*args, **kwargs)

    monkeypatch.setattr(writer.queue, "get", observed_get)
    try:
        writer.submit(event(0))
        assert writer.flush(2)
        # Only the submitted item should cause a queue read while unretired.
        time.sleep(0.2)
        assert len(calls) == 1, "idle writer must not poll an empty queue"
    finally:
        assert writer.shutdown(2)
    assert writer.queue.unfinished_tasks == 0


def test_contended_shutdown_is_bounded(tmp_path, monkeypatch):
    mod = load_generation(monkeypatch, "contended")
    writer = mod.store.Writer(tmp_path / "contended.sqlite", detector=False)
    with writer._lock:
        assert not writer.shutdown(0)
    writer.submit(event(1))
    assert writer.dropped == 1
    assert writer.shutdown(0)
    assert writer._thread is None


def capture_exit_callbacks(monkeypatch, store):
    """Isolate only the process-global registry, retaining real bound callbacks."""
    callbacks = []

    def register(callback, *args):
        callbacks.append((callback, args))

    def unregister(callback):
        callbacks[:] = [entry for entry in callbacks if entry[0] != callback]

    monkeypatch.setattr(store, "atexit", SimpleNamespace(register=register, unregister=unregister))
    return callbacks


def test_idle_lock_timeout_shutdown_closes_and_releases_callback(tmp_path, monkeypatch):
    mod = load_generation(monkeypatch, "idle_timeout")
    callbacks = capture_exit_callbacks(monkeypatch, mod.store)
    writer = mod.store.Writer(tmp_path / "idle-timeout.sqlite", detector=False)
    opened, closed = track_connections(monkeypatch, mod.store)
    idle = threading.Event()
    ready = writer._ready

    class ObservedSemaphore:
        def acquire(self):
            # The second acquire follows task_done for the only submitted item.
            if opened and writer.queue.unfinished_tasks == 0:
                idle.set()
            return ready.acquire()

        def release(self):
            ready.release()

    writer._ready = ObservedSemaphore()
    try:
        writer.submit(event(0))
        assert writer.flush(2)
        assert idle.wait(2), "writer must reach the empty semaphore wait"
        assert len(callbacks) == 1
        with writer._lock:
            start = time.monotonic()
            assert not writer.shutdown(0)
            assert time.monotonic() - start < 0.5
        # No second shutdown: it would hide a missing timeout wake/unregister.
        writer._thread.join(2)
        assert not writer._thread.is_alive(), "one timeout retirement must wake the idle writer"
        assert opened and closed == opened
        assert callbacks == [], "retired writer must release its exit callback"
    finally:
        # Mutation failure cleanup only; this is not the behavioral proof above.
        writer._retired.set()
        ready.release()
        writer._thread.join(2)
        callbacks.clear()
    ref = weakref.ref(writer)
    del writer
    gc.collect()
    assert ref() is None, "completed writer must not remain retained"


def test_registered_exit_callback_drains_and_retires(tmp_path, monkeypatch):
    mod = load_generation(monkeypatch, "exit_callback")
    callbacks = capture_exit_callbacks(monkeypatch, mod.store)
    writer = mod.store.Writer(tmp_path / "exit.sqlite", detector=False)
    opened, closed = track_connections(monkeypatch, mod.store)
    entered, release = threading.Event(), threading.Event()
    real_write = mod.store.write_rows

    def stalled_write(db, rows):
        entered.set()
        assert release.wait(3)
        real_write(db, rows)

    monkeypatch.setattr(mod.store, "write_rows", stalled_write)
    exit_thread = None
    results = []
    try:
        writer.submit(event(0))
        assert entered.wait(2)
        for number in range(1, 4):
            writer.submit(event(number))
        assert writer.queue.unfinished_tasks == 4
        assert len(callbacks) == 1, "first submit must register an exit fallback"
        callback, args = callbacks[0]
        assert args == (1.0,)
        exit_thread = threading.Thread(target=lambda: results.append(callback(*args)))
        exit_thread.start()
        assert writer._retired.wait(0.5), "exit fallback must retire, not merely flush"
        writer.submit(event("late"))
        assert writer.dropped == 1
        release.set()
        exit_thread.join(2)
        assert not exit_thread.is_alive()
        assert results == [True]
        assert not writer._thread.is_alive()
        assert writer.queue.unfinished_tasks == 0
        assert opened and closed == opened
        assert callbacks == []
        with closing(sqlite3.connect(writer.path)) as db:
            assert db.execute("SELECT count(*) FROM risk_events").fetchone()[0] == 4
    finally:
        release.set()
        if exit_thread is not None:
            exit_thread.join(2)
        assert writer.shutdown(2)


def writer_progress(writer):
    frame = sys._current_frames().get(writer._thread.ident)
    return {"unfinished": writer.queue.unfinished_tasks, "queued": writer.queue.qsize(),
            "errors": writer.errors, "dropped": writer.dropped,
            "alive": writer._thread.is_alive(),
            "stack": "".join(traceback.format_stack(frame)) if frame else "no writer frame"}


def test_concurrent_producers_do_not_drop_with_queue_capacity(tmp_path, monkeypatch):
    mod = load_generation(monkeypatch, "producers")
    writer = mod.store.Writer(tmp_path / "producers.sqlite", detector=False)
    attempted = threading.Event()
    real_lock = writer._lock

    class ObservedLock:
        def acquire(self, *args, **kwargs):
            attempted.set()
            return real_lock.acquire(*args, **kwargs)

        def release(self):
            real_lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *args):
            self.release()

    writer._lock = ObservedLock()
    threads = [threading.Thread(target=lambda n=n: [writer.submit(event((n, i)))
               for i in range(250)]) for n in range(8)]
    try:
        # Force ordinary contention once; the remaining submissions race naturally.
        with real_lock:
            for thread in threads:
                thread.start()
            assert attempted.wait(2)
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
        assert writer.dropped == 0
        assert writer.flush(30), writer_progress(writer)
    finally:
        for thread in threads:
            thread.join(5)
        assert writer.shutdown(30)
    with closing(sqlite3.connect(writer.path)) as db:
        assert db.execute("SELECT count(*) FROM risk_events").fetchone()[0] == 2000

