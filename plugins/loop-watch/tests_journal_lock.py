"""A held journal lock never freezes the event loop (t_01a1d68301a50d6c, finding from t_01a1d68c494c8717).

On hermes-hub (2026-11-13 20:09Z) two backends' event loops waited 209 s and 187 s in
journal.append's flock(LOCK_EX) under ReqTrace._finish -> default_sink, while another process held
the host-wide sidecar lock. These tests hold that lock from a separate process.
"""
import errno
import importlib.util
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("loop_watch_reqtrace_lock", _here / "reqtrace.py")
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)
journal = rt.journal

_HOLDER = r"""
import fcntl, sys, time
f = open(sys.argv[1], 'a')
fcntl.flock(f, fcntl.LOCK_EX)
print('held', flush=True)
time.sleep(float(sys.argv[2]))
"""


@pytest.fixture
def held_lock(tmp_path):
    path = tmp_path / "logs" / "loop-watch.jsonl"
    path.parent.mkdir(parents=True)
    proc = subprocess.Popen([sys.executable, "-c", _HOLDER, str(path) + ".lock", "60"], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    yield path, proc
    proc.kill()
    proc.wait(5)


def test_diagnostic_append_gives_up_within_its_bound(held_lock):
    path, _ = held_lock
    t0 = time.monotonic()
    with pytest.raises(OSError) as exc:
        journal.append(path, {"kind": "stall"})
    took = time.monotonic() - t0
    assert exc.value.errno == errno.ETIMEDOUT
    assert journal.DIAG_LOCK_TIMEOUT_S * 0.9 <= took <= journal.DIAG_LOCK_TIMEOUT_S + 0.5
    assert not path.exists()


def test_durable_append_is_bounded_and_raises_for_retry(held_lock):
    path, _ = held_lock
    t0 = time.monotonic()
    with pytest.raises(OSError):
        journal.append(path, {"kind": "request_stats"}, durable=True, lock_timeout_s=0.2)
    assert time.monotonic() - t0 < 1.0


def test_slow_request_finish_returns_fast_while_another_process_holds_the_lock(held_lock):
    path, proc = held_lock
    writer = journal.OffThreadWriter()
    trace = rt.ReqTrace(sink=rt.offloop_sink(rt.default_sink(path), writer), slow_s=0.1)
    req = rt._Req(1, "GET", "/api/slow", trace.clock() - 3.0, None)
    t0 = time.monotonic()
    trace._finish(req)  # runs on the event loop in production
    assert time.monotonic() - t0 < 0.05
    # The writer thread gives up on the held lock after its bound (the record is dropped) ...
    writer.q.join()
    # ... and once the holder is gone, the next report is written.
    proc.kill()
    proc.wait(5)
    trace._finish(rt._Req(2, "GET", "/api/slow2", trace.clock() - 3.0, None))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists() and "/api/slow2" in path.read_text():
            break
        time.sleep(0.02)
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    assert any(r.get("path") == "/api/slow2" for r in rows)
    assert not any(r.get("path") == "/api/slow" for r in rows)  # dropped on the busy lock, not waited for


def test_writer_queue_is_bounded_and_drops_when_full():
    gate = threading.Event()
    writer = journal.OffThreadWriter(maxsize=2)
    done = []
    assert writer.submit(lambda: (gate.wait(5), done.append(1)))
    time.sleep(0.05)  # writer thread is now blocked inside the first call
    assert writer.submit(done.append, 2) and writer.submit(done.append, 3)
    t0 = time.monotonic()
    assert writer.submit(done.append, 4) is False
    assert time.monotonic() - t0 < 0.01 and writer.dropped == 1
    gate.set()
    writer.q.join()
    assert done == [1, 2, 3]


def test_stats_records_keep_the_synchronous_error_path(tmp_path):
    seen = []

    def sink(rep):
        seen.append(rep["kind"])
        raise OSError("disk")
    wrapped = rt.offloop_sink(sink, journal.OffThreadWriter())
    with pytest.raises(OSError):
        wrapped({"kind": "request_stats"})
    assert seen == ["request_stats"]


def test_production_install_wraps_the_sink_off_loop(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("loop_watch_watch_lock", _here / "watch.py")
    watch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watch)
    made = []

    class _Trace:
        def __init__(self, *, sink, slow_s):
            made.append(sink)

    fake = type(sys)("fake_rt")
    fake.ReqTrace = _Trace
    fake.default_sink = rt.default_sink
    fake.offloop_sink = rt.offloop_sink
    fake.install_on_app = lambda app, trace: False
    monkeypatch.setattr(watch, "_reqtrace_mod", lambda: fake)
    ws = type(sys)(watch.WEB_MODULE)
    ws.app = object()
    monkeypatch.setitem(sys.modules, watch.WEB_MODULE, ws)
    watch._install_reqtrace(tmp_path, 2.0)
    assert made and hasattr(made[0], "_loop_watch_offloop")
