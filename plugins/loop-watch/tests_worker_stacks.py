"""Sampler output parity without source-file I/O on the event loop."""
import asyncio
import contextvars
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import linecache
import sys
import threading
import time
import traceback
from collections import Counter
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("reqtrace_stacks_test", Path(__file__).with_name("reqtrace.py"))
assert _spec is not None and _spec.loader is not None
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)


@pytest.mark.parametrize("depth", [1, 3, 10, 100, 0, -2])
def test_busy_worker_output_matches_extract_stack_without_linecache(monkeypatch, depth):
    ready, stop = threading.Event(), threading.Event()

    def busy_handler():
        ready.set()
        stop.wait()

    worker = threading.Thread(target=busy_handler, name="asyncio_0")
    worker.start()
    try:
        assert ready.wait(2)
        assert worker.ident is not None
        # Wait for the stable Event.wait frame, not the ready.set transition.
        deadline = time.monotonic() + 2
        while sys._current_frames()[worker.ident].f_code.co_name != "wait":
            assert time.monotonic() < deadline
            time.sleep(0.001)
        frame = sys._current_frames()[worker.ident]
        expected = tuple("%s:%d %s" % (rt._short(fs.filename), fs.lineno, fs.name)
                         for fs in traceback.extract_stack(frame)[-depth:])
        monkeypatch.setattr(rt.sys, "_current_frames", lambda: {worker.ident: frame})
        monkeypatch.setattr(rt.threading, "enumerate", lambda: [worker])

        def forbidden(*args, **kwargs):
            raise AssertionError("stack sampling must not read or stat source files")

        # Undo the global linecache hooks before pytest formats a failing assertion.
        with monkeypatch.context() as source_io:
            source_io.setattr(linecache, "checkcache", forbidden)
            source_io.setattr(linecache, "getline", forbidden)
            actual = rt._worker_stacks(depth)
        # Depth 1 can retain only threading.wait, which is intentionally idle-filtered.
        idle = all(any(m in x for m in ("threading.py", "queue.py", "thread.py", "to_thread", "_asyncio.py"))
                   for x in expected) and expected[-1].rsplit(" ", 1)[-1] in rt._IDLE_WORKER_TAILS
        want = Counter() if not expected or idle else Counter({("asyncio", expected): 1})
        assert actual == want
    finally:
        stop.set()
        worker.join(2)
        assert not worker.is_alive()


@pytest.mark.parametrize("name,pool", [
    ("hermes-gateway-default_0", "hermes-gateway-default"),
    ("hermes-gateway-default_11", "hermes-gateway-default"),
    ("AnyIO worker thread", "AnyIO"), ("asyncio_0", "asyncio"),
    ("ThreadPoolExecutor-0_0", "ThreadPoolExecutor-0_0"), ("hub-sync_0", "hub-sync_0"),
])
@pytest.mark.parametrize("idle", [False, True])
def test_pool_lock_wait_vs_idle_metadata_only(monkeypatch, name, pool, idle):
    class Frame:
        def __init__(self, filename, func, back=None):
            self.f_code = SimpleNamespace(co_filename=filename, co_name=func)
            self.f_lineno = 42
            self.f_back = back

        @property
        def f_locals(self):
            raise AssertionError("must not inspect locals or arguments")

    outer = Frame("/stdlib/concurrent/futures/thread.py", "_worker")
    if not idle:
        outer = Frame("/app/handlers.py", "application_lock_wait", outer)
    frame = Frame("/stdlib/threading.py", "wait", outer)
    tid = -123
    monkeypatch.setattr(rt.sys, "_current_frames", lambda: {tid: frame})
    monkeypatch.setattr(rt.threading, "enumerate", lambda: [SimpleNamespace(ident=tid, name=name)])
    def forbidden(*args, **kwargs):
        raise AssertionError("must not look up source")
    with monkeypatch.context() as source_io:
        source_io.setattr(linecache, "checkcache", forbidden)
        source_io.setattr(linecache, "getline", forbidden)
        stacks = rt._worker_stacks()
    if idle:
        assert not stacks
    else:
        assert sum(stacks.values()) == 1
        (observed_pool, stack), = stacks
        assert observed_pool == pool
        assert len(stack) == 3
        assert any("application_lock_wait" in row for row in stack)


def test_scope_aware_default_pool_in_slow_report_then_idle():
    # Model ScopeAwareExecutor's context wrapper using a real stdlib worker.
    class ScopeAwareExecutor(ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            ctx = contextvars.copy_context()
            return super().submit(ctx.run, fn, *args, **kwargs)

    ready, release = threading.Event(), threading.Event()
    secret = "test-private-argument-not-for-reports"
    def application_lock_wait(argument):
        ready.set()
        release.wait(5)
        return argument

    async def exercise():
        reports = []
        trace = rt.ReqTrace(sink=reports.append, slow_s=0, sample_after_s=0, sample_s=0)
        pool = ScopeAwareExecutor(max_workers=1, thread_name_prefix="hermes-gateway-default")
        asyncio.get_running_loop().set_default_executor(pool)
        async def handler(scope, receive, send):
            await asyncio.to_thread(application_lock_wait, secret)
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b""})
        async def send(message):
            pass
        request = asyncio.create_task(trace.wrap(handler)(
            {"type": "http", "method": "GET", "path": "/api/example"}, None, send))
        try:
            deadline = time.monotonic() + 3
            while not ready.is_set():
                assert time.monotonic() < deadline
                await asyncio.sleep(0.001)
            trace.sample_on_loop()
        finally:
            release.set()
            await request
        report, = reports
        workers = report["worker_threads"]
        assert any(w["thread"] == "hermes-gateway-default" and
                   any("application_lock_wait" in row for row in w["stack"]) for w in workers)
        assert secret not in json.dumps(report)
        assert len(workers) <= rt.TOP_N
        assert all(len(w["stack"]) <= 10 for w in workers)
        # The same real worker remains alive, now waiting for its next submission.
        deadline = time.monotonic() + 3
        while any(p == "hermes-gateway-default" for p, _ in rt._worker_stacks()):
            assert time.monotonic() < deadline
            await asyncio.sleep(0.001)
        assert any(t.name.startswith("hermes-gateway-default_") for t in threading.enumerate())
    asyncio.run(exercise())
