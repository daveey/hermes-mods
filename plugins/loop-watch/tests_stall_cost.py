"""The watcher's own cost while the event loop is stalled is bounded (t_01a1d68301a50d6c).

On hermes-hub the stall sampler formatted every thread's full stack with traceback.extract_stack each
0.1 s; each sample took ~0.45 s of watcher wall time (linecache.checkcache stats = GIL hand-offs) and
the watcher took ~26% of GIL time while backends were already stalled. These tests drive the real stall
path (a loop thread blocked in Python, hundreds of deep threads) and fake-clock paths for the bounds.
"""
import asyncio
import importlib.util
import linecache
import threading
import time
import traceback
from pathlib import Path

import pytest

_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("loop_watch_watch_cost", _here / "watch.py")
watch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watch)


def _deep(n, fn):
    return fn() if n == 0 else _deep(n - 1, fn)


class _Loop:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.th = threading.Thread(target=self.loop.run_forever, daemon=True, name="loop")
        self.th.start()
        while not self.loop.is_running():
            time.sleep(0.01)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.th.join(5)
        self.loop.close()


def _python_stall_in_loop(seconds, sampled, release):
    """Keep the handler observable until sampled, even if the watcher wakes late."""
    end = time.monotonic() + seconds
    x = 0
    while not release.is_set() and (time.monotonic() < end or not sampled.is_set()):
        for i in range(500):
            x += i * i
    return x


@pytest.fixture
def many_threads():
    stop = threading.Event()
    ths = [threading.Thread(target=_deep, args=(40, stop.wait), daemon=True, name="idle-%d" % i) for i in range(200)]

    def spin():
        while not stop.is_set():
            sum(i * i for i in range(300))
    ths += [threading.Thread(target=_deep, args=(30, spin), daemon=True, name="busy-%d" % i) for i in range(2)]
    for t in ths:
        t.start()
    yield
    stop.set()
    for t in ths:
        t.join(5)


@pytest.mark.parametrize("wake_delay", [0.0, 2.2], ids=["normal", "late-watcher"])
def test_real_stall_with_many_threads_has_bounded_watcher_cost(many_threads, monkeypatch, wake_delay):
    # No source/stat I/O on the stall path: every such call is a GIL hand-off against the stalled loop.
    # Counted on the watcher (this) thread only; other threads may format tracebacks legitimately.
    me, io_calls = threading.get_ident(), []
    for mod, name in ((linecache, "checkcache"), (linecache, "getline"), (linecache, "updatecache"),
                      (traceback, "extract_stack")):
        def spy(*a, _orig=getattr(mod, name), _name=name, **k):
            if threading.get_ident() == me:
                io_calls.append(_name)
            return _orig(*a, **k)
        monkeypatch.setattr(mod, name, spy)
    lp = _Loop()
    sampled, release = threading.Event(), threading.Event()
    # Failure cleanup must also work if sampling is broken: never leave a spinning loop behind.
    watchdog = threading.Timer(20.0, release.set)
    watchdog.daemon = True
    watchdog.start()
    try:
        w = watch.LoopWatch(lp.loop, lp.th.ident, sink=lambda r: None, stall_s=0.3, sample_s=0.02)
        original_sample, original_wait = w._sample, w.wait

        def sample(*args):
            original_sample(*args)
            sampled.set()  # only release after the real stack capture, not merely a timeout

        first_wait = True

        def wait(ev, seconds):
            nonlocal first_wait
            result = original_wait(ev, seconds)
            if first_wait:
                first_wait = False
                # Model a descheduled watcher while the loop finishes its original 2s workload.
                time.sleep(wake_delay)
            return result

        w._sample, w.wait = sample, wait
        lp.loop.call_soon_threadsafe(_python_stall_in_loop, 2.0, sampled, release)
        c0 = time.thread_time()
        rep = w.check_once()
        cpu = time.thread_time() - c0
        assert io_calls == [], io_calls[:5]
        assert rep is not None and rep["sampling"] in ("full", "capped")
        assert any("_python_stall_in_loop" in " ".join(s["stack"]) for s in rep["loop_stacks"])
        assert any(b["thread"].startswith("busy-") for b in rep["busy_threads"])
        sampled_s = rep["stall_s"] - 0.3
        # Measured sample passes: within budget after the first pass, plus that first pass.
        first_pass_cap = 0.25
        assert rep["watcher_cost_s"] <= watch.SAMPLE_BUDGET * sampled_s + first_pass_cap, rep
        # Independent CPU measure of the watcher thread across the whole stall, report built included.
        assert cpu <= 0.10 * rep["stall_s"] + 0.05, (cpu, rep)
        # Exact exclusion of post-stall report work is covered by the fake-clock test below;
        # wall time here includes arbitrary runner descheduling and the injected late wake.
        assert not release.is_set(), "fixture watchdog expired before sampling completed"
    finally:
        release.set()
        watchdog.cancel()
        watchdog.join(5)
        lp.close()


class _FakeLoop:
    def __init__(self):
        self.cb = None

    def call_soon_threadsafe(self, fn):
        self.cb = fn


class _Frame:
    def __init__(self, name, back=None):
        self.f_code = type("C", (), {"co_filename": "/x/plugins/app.py", "co_name": name})()
        self.f_lineno = 7
        self.f_back = back


def _fake(stall_end, sample_cost, n_threads=10):
    now = [0.0]
    loop = _FakeLoop()
    passes = []
    loop_tid = 1
    frames = {loop_tid: _Frame("handler", _Frame("outer"))}
    for i in range(n_threads):
        frames[100 + i] = _Frame("crunch") if i % 2 else _Frame("wait")

    def clock():
        return now[0]

    def get_frames():
        passes.append(now[0])
        now[0] += sample_cost  # this pass's own cost
        return frames

    def wait(ev, t):
        now[0] += t
        if now[0] >= stall_end and loop.cb is not None:
            loop.cb()
        return ev.is_set()
    return now, loop, passes, clock, get_frames, wait, loop_tid


@pytest.mark.parametrize("sample_cost", [0.001, 0.05, 0.5])
def test_expensive_passes_back_off_to_the_budget(sample_cost):
    now, loop, passes, clock, frames, wait, tid = _fake(stall_end=60.0, sample_cost=sample_cost)
    w = watch.LoopWatch(loop, tid, sink=lambda r: None, stall_s=1.0, sample_s=0.1, clock=clock,
                        frames=frames, wait=wait, max_samples=10_000)
    rep = w.check_once()
    assert rep["stall_s"] >= 60.0
    sampled = rep["stall_s"] - 1.0
    # Bound: budget share of the sampled stall plus one pass (the first is taken before any back-off).
    assert rep["watcher_cost_s"] <= watch.SAMPLE_BUDGET * sampled + sample_cost + 1e-6, rep
    gaps = [b - a for a, b in zip(passes, passes[1:])]
    assert all(g >= max(0.1, sample_cost / watch.SAMPLE_BUDGET) - 1e-6 for g in gaps), gaps[:5]
    assert any(b["stack"][-1].endswith("crunch") for b in rep["busy_threads"])
    assert all(not b["stack"][-1].endswith("wait") for b in rep["busy_threads"])


def test_long_stall_stops_sampling_at_max_samples():
    now, loop, passes, clock, frames, wait, tid = _fake(stall_end=300.0, sample_cost=0.001)
    w = watch.LoopWatch(loop, tid, sink=lambda r: None, stall_s=1.0, clock=clock, frames=frames, wait=wait,
                        max_samples=40)
    rep = w.check_once()
    assert rep["samples"] == 40 and len(passes) == 40
    assert rep["sampling"] == "capped"
    assert rep["stall_s"] >= 300.0  # still timed to the end


def test_chronic_stalls_are_rate_limited_to_timing_only():
    now, loop, passes, clock, frames, wait, tid = _fake(stall_end=0.0, sample_cost=0.001)
    w = watch.LoopWatch(loop, tid, sink=lambda r: None, stall_s=1.0, clock=clock, frames=frames, wait=wait,
                        stalls_per_window=3, window_s=600.0)
    reps = []
    for _ in range(5):
        # Each probe stalls 5 s.
        end = now[0] + 5.0

        def wait_one(ev, t, end=end):
            now[0] += t
            if now[0] >= end:
                loop.cb()
            return ev.is_set()
        w.wait = wait_one
        reps.append(w.check_once())
    assert [r["sampling"] for r in reps] == ["full", "full", "full", "rate_limited", "rate_limited"]
    assert all(r["samples"] == 0 and r["watcher_cost_s"] == 0 for r in reps[3:])
    assert all(r["stall_s"] >= 5.0 for r in reps)
    n = len(passes)
    now[0] += 601.0  # window passes: sampling resumes
    end = now[0] + 5.0
    w.wait = lambda ev, t: (now.__setitem__(0, now[0] + t), now[0] >= end and loop.cb(), ev.is_set())[-1]
    assert w.check_once()["sampling"] == "full" and len(passes) > n


def test_thread_scan_is_capped_per_sample():
    now, loop, passes, clock, frames, wait, tid = _fake(stall_end=3.0, sample_cost=0.0, n_threads=1000)
    w = watch.LoopWatch(loop, tid, sink=lambda r: None, stall_s=1.0, clock=clock, frames=frames, wait=wait,
                        max_threads=50)
    seen = []
    orig = watch._raw_stack

    def counting(fr, depth=watch.STACK_DEPTH):
        seen.append(fr)
        return orig(fr, depth)
    watch._raw_stack = counting
    try:
        rep = w.check_once()
    finally:
        watch._raw_stack = orig
    # Per pass: the loop thread plus at most the busy half of the 50 scanned threads.
    assert rep["samples"] >= 1
    assert len(seen) <= rep["samples"] * (1 + 25)


# t_01a1d6cdfa497d94: pins for mutants of cb93c9ea that survived every loop-watch test.
def test_stall_s_excludes_post_stall_formatting(monkeypatch):
    now, loop, passes, clock, frames, wait, tid = _fake(stall_end=10.0, sample_cost=0.0)
    w = watch.LoopWatch(loop, tid, sink=lambda r: None, stall_s=1.0, sample_s=0.1, clock=clock, frames=frames,
                        wait=wait)
    orig = watch._fmt_raw

    def slow_fmt(raw):
        now[0] += 5.0  # report formatting, after the loop caught up
        return orig(raw)
    monkeypatch.setattr(watch, "_fmt_raw", slow_fmt)
    rep = w.check_once()
    assert rep["busy_threads"] and now[0] >= 15.0  # formatting ran and advanced the clock
    assert 10.0 <= rep["stall_s"] < 10.2, rep["stall_s"]


def test_watcher_cost_is_the_sum_of_pass_costs():
    now, loop, passes, clock, frames, wait, tid = _fake(stall_end=10.0, sample_cost=0.05)
    w = watch.LoopWatch(loop, tid, sink=lambda r: None, stall_s=1.0, sample_s=0.1, clock=clock, frames=frames,
                        wait=wait, max_samples=10_000)
    rep = w.check_once()
    assert rep["samples"] == len(passes) > 1
    assert rep["watcher_cost_s"] > 0
    assert rep["watcher_cost_s"] == pytest.approx(0.05 * rep["samples"], abs=1e-3), rep
