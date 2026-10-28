import asyncio
import importlib.util
import json
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
        assert any(b["thread"] == "hog" and "_busy_python" in b["stack"][-1] for b in rep["busy_threads"])
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
