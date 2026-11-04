"""Sampler output parity without source-file I/O on the event loop."""
import importlib.util
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
