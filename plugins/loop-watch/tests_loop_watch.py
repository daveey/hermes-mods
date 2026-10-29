import asyncio
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("loop_watch_watch_t", _here / "watch.py")
watch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watch)
_spec2 = importlib.util.spec_from_file_location("loop_watch_init_t", _here / "__init__.py")
lw = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(lw)


class _Loop:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.th = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.th.start()
        while not self.loop.is_running():
            time.sleep(0.01)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.th.join(5)
        self.loop.close()


def _blocking_handler_in_loop(seconds):
    time.sleep(seconds)  # sync work on the loop thread: what a stalled backend looks like


def test_healthy_loop_reports_nothing():
    lp = _Loop()
    try:
        w = watch.LoopWatch(lp.loop, lp.th.ident, sink=lambda r: None, stall_s=0.3)
        assert all(w.check_once() is None for _ in range(5))
    finally:
        lp.close()


def test_stall_names_the_blocking_code_on_the_loop_thread():
    lp = _Loop()
    try:
        w = watch.LoopWatch(lp.loop, lp.th.ident, sink=lambda r: None, stall_s=0.3, sample_s=0.05)
        lp.loop.call_soon_threadsafe(_blocking_handler_in_loop, 1.2)
        rep = w.check_once()
        assert rep is not None
        assert 0.9 <= rep["stall_s"] <= 3.0
        joined = " | ".join(" ".join(s["stack"]) for s in rep["loop_stacks"])
        assert "_blocking_handler_in_loop" in joined
        # The loop thread blocked itself; the watcher woke on time (not GIL starvation).
        assert rep["watch_lag_s"] < 0.5
        json.dumps(rep)  # report is JSON-serialisable
    finally:
        lp.close()


def _busy_python(stop):
    x = 0
    while not stop.is_set():
        x += 1


def test_busy_other_thread_is_listed():
    lp = _Loop()
    stop = threading.Event()
    hog = threading.Thread(target=_busy_python, args=(stop,), name="hog", daemon=True)
    hog.start()
    try:
        w = watch.LoopWatch(lp.loop, lp.th.ident, sink=lambda r: None, stall_s=0.3, sample_s=0.05)
        lp.loop.call_soon_threadsafe(_blocking_handler_in_loop, 0.8)
        rep = w.check_once()
        assert rep is not None
        # The hog's innermost frame is _busy_python or the Event.is_set it calls each spin; either way it's listed.
        assert any(b["thread"] == "hog" and any("_busy_python" in f for f in b["stack"]) for b in rep["busy_threads"])
    finally:
        stop.set()
        hog.join(2)
        lp.close()


def test_sink_writes_jsonl_and_log_line(tmp_path, caplog):
    sink = watch.default_sink(tmp_path / "logs" / "loop-watch.jsonl")
    rep = {"posted": "06:52:20.100", "stall_s": 8.1, "samples": 80, "watch_lag_s": 0.0, "pid": 1,
           "loop_stacks": [{"n": 80, "stack": ["a.py:1 f", "b.py:2 g"]}], "busy_threads": []}
    with caplog.at_level("WARNING", logger="loop-watch"):
        sink(rep)
    assert "stalled 8.10s" in caplog.text and "b.py:2 g" in caplog.text
    line = (tmp_path / "logs" / "loop-watch.jsonl").read_text().strip()
    assert json.loads(line)["stall_s"] == 8.1


def test_find_loop_finds_running_loop_on_named_thread():
    lp = _Loop()
    try:
        assert watch.find_loop(lp.th.ident) is lp.loop
        assert watch.find_loop(-1) is None
    finally:
        lp.close()


def test_gate_serve_only_by_default():
    assert lw.wanted({}, ["-c", "serve", "--host", "127.0.0.1"])
    assert lw.wanted({}, ["/x/hermes", "serve", "--isolated"])
    assert not lw.wanted({}, ["/x/hermes", "chat"])
    assert not lw.wanted({}, ["/x/hermes", "gateway", "run"])
    assert lw.wanted({"HERMES_LOOP_WATCH": "all"}, ["/x/hermes", "chat"])
    assert not lw.wanted({"HERMES_LOOP_WATCH": "off"}, ["-c", "serve"])


def test_start_wires_request_tracer_into_serve_app(tmp_path, monkeypatch):
    import types
    from starlette.applications import Starlette

    app = Starlette()
    monkeypatch.setitem(sys.modules, "hermes_cli.web_server", types.SimpleNamespace(app=app))
    trace = watch._install_reqtrace(tmp_path, 2.0)
    assert trace is not None
    assert getattr(app.middleware_stack, "__loop_watch_traced__", False)
    assert watch._install_reqtrace(tmp_path, 0) is None  # 0 disables
    monkeypatch.setitem(sys.modules, "hermes_cli.web_server", types.SimpleNamespace())
    assert watch._install_reqtrace(tmp_path, 2.0) is None  # not a web server process


def test_run_calls_on_tick():
    lp = _Loop()
    try:
        w = watch.LoopWatch(lp.loop, lp.th.ident, sink=lambda r: None, stall_s=0.3, period_s=0.05)
        ticks = []
        w.on_tick = lambda: (ticks.append(1), w.stop.set() if len(ticks) >= 3 else None)
        t = threading.Thread(target=w.run, daemon=True)
        t.start()
        t.join(3)
        assert len(ticks) >= 3
    finally:
        lp.close()


def _busy_frames():
    """A real frame of a thread spinning in a function named hot_spin, plus an idle waiter."""
    stop = threading.Event()
    idle = threading.Event()

    def hot_spin():
        while not stop.is_set():
            sum(range(1000))

    t1 = threading.Thread(target=hot_spin, name="hot", daemon=True)
    t2 = threading.Thread(target=idle.wait, name="idler", daemon=True)
    t1.start(); t2.start()
    return stop, idle


def test_cpu_profile_names_busy_thread_and_skips_idle():
    stop, idle = _busy_frames()
    try:
        rep = watch.cpu_profile(0.3, sample_s=0.01)
    finally:
        stop.set(); idle.set()
    assert rep["kind"] == "cpu_profile" and rep["samples"] > 5
    threads = {t["thread"]: t["busy_pct"] for t in rep["threads"]}
    parked = {t["thread"]: t["pct"] for t in rep["parked_threads"]}
    assert threads.get("hot", 0) + parked.get("hot", 0) > 50 and threads.get("hot", 0) > 0
    assert "idler" not in threads and "idler" not in parked
    assert any("hot_spin" in " ".join(s["stack"]) for s in rep["stacks"])
    assert rep["process_cpu_pct"] > 0


def test_profile_trigger_runs_once_and_removes_request(tmp_path):
    req = tmp_path / watch.PROFILE_REQUEST
    got = []
    ran = []
    tick = watch.profile_trigger(req, got.append, spawn=lambda f: (ran.append(1), f()))
    tick()  # no request: nothing
    assert not ran
    req.write_text("1")
    tick()
    assert ran == [1] and not req.exists()
    assert got and got[0]["kind"] == "cpu_profile" and 0.9 <= got[0]["seconds"] <= 2
    tick()
    assert ran == [1]


def test_profile_trigger_caps_and_defaults_seconds(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(watch, "cpu_profile", lambda s: seen.append(s) or {"kind": "cpu_profile"})
    req = tmp_path / watch.PROFILE_REQUEST
    tick = watch.profile_trigger(req, lambda r: None, spawn=lambda f: f())
    for text in ("", "junk", "9999"):
        req.write_text(text)
        tick()
    assert seen == [20.0, 20.0, watch.PROFILE_MAX_S]


def test_default_sink_writes_profile_report(tmp_path):
    path = tmp_path / "lw.jsonl"
    sink = watch.default_sink(path)
    sink({"kind": "cpu_profile", "seconds": 1.0, "process_cpu_pct": 50.0, "threads": [{"thread": "x", "busy_pct": 9.0}]})
    assert json.loads(path.read_text())["kind"] == "cpu_profile"
