"""Event-loop stall watcher: names what held the loop when an HTTP backend stops answering.

A daemon thread posts a no-op onto the watched asyncio loop every ``period_s``. If the loop has
not run it within ``stall_s``, the thread samples every thread's Python stack every ``sample_s``
until the loop catches up. Then it writes ONE report: stall length, the loop thread's stacks
(what the loop was doing instead of serving), and the other threads seen running Python during
the stall (a thread holding the GIL in a long C call starves the loop without appearing on its
stack). The watcher's own oversleep (``watch_lag_s``) tells GIL starvation from a busy loop: a
loop blocked in its own code lets the watcher wake on time; a GIL hog delays the watcher too.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import sys
import threading
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("loop-watch")

STALL_S = 1.0
PERIOD_S = 0.25
SAMPLE_S = 0.1
STACK_DEPTH = 14
TOP_N = 5
# Frames of a thread that is idle (waiting on a lock, a socket, a queue, sleep). Skipped when
# listing "other threads running Python" so the report shows only threads that did work.
_IDLE_FUNCS = {"wait", "_wait_for_tstate_lock", "select", "poll", "sleep", "get", "acquire", "accept",
               "recv", "recv_into", "read", "readline", "_worker", "run_forever", "_run_once", "join",
               "wait_for", "control", "epoll", "kqueue", "result", "_recv_bytes", "readinto"}


def _fmt_stack(frame, depth: int = STACK_DEPTH) -> tuple:
    out = []
    for fs in traceback.extract_stack(frame)[-depth:]:
        out.append("%s:%d %s" % (_short(fs.filename), fs.lineno, fs.name))
    return tuple(out)


def _short(path: str) -> str:
    for marker in ("/hermes-agent/", "/site-packages/", "/plugins/", "/lib/python"):
        i = path.rfind(marker)
        if i >= 0:
            return path[i + len(marker):]
    return path


def _idle(stack: tuple) -> bool:
    if not stack:
        return True
    top = stack[-1].rsplit(" ", 1)[-1]
    return top in _IDLE_FUNCS


class LoopWatch:
    def __init__(self, loop: asyncio.AbstractEventLoop, loop_thread_id: int, *,
                 sink: Callable[[dict], None], stall_s: float = STALL_S, period_s: float = PERIOD_S,
                 sample_s: float = SAMPLE_S, clock: Callable[[], float] = time.monotonic,
                 frames: Callable[[], dict] = sys._current_frames) -> None:
        self.loop, self.loop_tid, self.sink = loop, loop_thread_id, sink
        self.stall_s, self.period_s, self.sample_s = stall_s, period_s, sample_s
        self.clock, self.frames = clock, frames
        self.stop = threading.Event()
        self.reports = 0
        self.on_tick: Optional[Callable[[], None]] = None

    def _names(self) -> dict:
        return {t.ident: t.name for t in threading.enumerate()}

    def check_once(self) -> Optional[dict]:
        """Post one probe; on a stall, sample until the loop runs it and return the report."""
        ran = threading.Event()
        posted_wall, posted = time.time(), self.clock()
        try:
            self.loop.call_soon_threadsafe(ran.set)
        except RuntimeError:  # loop closed
            self.stop.set()
            return None
        if ran.wait(self.stall_s):
            return None
        me = threading.get_ident()
        loop_stacks: Counter = Counter()
        busy: Counter = Counter()
        names = self._names()
        samples, watch_lag = 0, 0.0
        while True:
            t0 = self.clock()
            done = ran.wait(self.sample_s)
            watch_lag = max(watch_lag, self.clock() - t0 - self.sample_s)
            frames = self.frames()
            samples += 1
            f = frames.get(self.loop_tid)
            if f is not None:
                loop_stacks[_fmt_stack(f)] += 1
            for tid, fr in frames.items():
                if tid in (me, self.loop_tid):
                    continue
                st = _fmt_stack(fr, 6)
                if not _idle(st):
                    busy[(names.get(tid) or str(tid), st)] += 1
            if done or self.stop.is_set():
                break
        stall = self.clock() - posted
        return {
            "posted": time.strftime("%H:%M:%S", time.localtime(posted_wall)) + ".%03d" % (int(posted_wall * 1000) % 1000),
            "stall_s": round(stall, 3),
            "samples": samples,
            "watch_lag_s": round(max(0.0, watch_lag), 3),
            "pid": os.getpid(),
            "loop_stacks": [{"n": n, "stack": list(st)} for st, n in loop_stacks.most_common(TOP_N)],
            "busy_threads": [{"n": n, "thread": name, "stack": list(st)} for (name, st), n in busy.most_common(TOP_N)],
        }

    def run(self) -> None:
        while not self.stop.is_set():
            if self.on_tick is not None:
                try:
                    self.on_tick()
                except Exception as exc:  # noqa: BLE001
                    log.debug("loop-watch tick failed: %s", exc)
            try:
                rep = self.check_once()
            except Exception as exc:  # noqa: BLE001 - a watcher must never take the process down
                log.debug("loop-watch check failed: %s", exc)
                rep = None
            if rep is not None:
                self.reports += 1
                try:
                    self.sink(rep)
                except Exception as exc:  # noqa: BLE001
                    log.debug("loop-watch sink failed: %s", exc)
            self.stop.wait(self.period_s)


def find_loop(target_thread: Optional[int] = None):
    """The running asyncio loop on ``target_thread`` (default: the main thread), or None."""
    target_thread = target_thread or threading.main_thread().ident
    for obj in gc.get_objects():
        try:
            if isinstance(obj, asyncio.AbstractEventLoop) and obj.is_running() \
                    and getattr(obj, "_thread_id", None) == target_thread:
                return obj
        except Exception:  # noqa: BLE001 - foreign objects with odd isinstance hooks
            continue
    return None


def default_sink(path: Path) -> Callable[[dict], None]:
    def sink(rep: dict) -> None:
        top = rep["loop_stacks"][0]["stack"][-1] if rep["loop_stacks"] else "?"
        hog = rep["busy_threads"][0] if rep["busy_threads"] else None
        log.warning("loop-watch: event loop stalled %.2fs (posted %s, watch_lag %.2fs) loop top=%s busiest=%s",
                    rep["stall_s"], rep["posted"], rep["watch_lag_s"], top,
                    ("%s %s" % (hog["thread"], hog["stack"][-1])) if hog else "-")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rep) + "\n")
        except OSError:
            pass
    return sink


_STARTED_ATTR = "_hermes_loop_watch_started"


def _reqtrace_mod():
    try:
        from . import reqtrace  # type: ignore[attr-defined]
        return reqtrace
    except ImportError:
        import importlib.util
        mod = sys.modules.get("loop_watch_reqtrace")
        if mod is None:
            spec = importlib.util.spec_from_file_location("loop_watch_reqtrace", Path(__file__).with_name("reqtrace.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sys.modules["loop_watch_reqtrace"] = mod
        return mod


def _install_reqtrace(home: Path, slow_s: Optional[float]):
    """Wrap the serve process's FastAPI app with the slow-request tracer. None when not a web server."""
    if slow_s is None or slow_s <= 0:
        return None
    ws = sys.modules.get("hermes_cli.web_server")
    app = getattr(ws, "app", None) if ws is not None else None
    if app is None:
        return None
    try:
        rt = _reqtrace_mod()
        trace = rt.ReqTrace(sink=rt.default_sink(home / "logs" / "loop-watch.jsonl"), slow_s=slow_s)
        if rt.install_on_app(app, trace):
            log.info("loop-watch: tracing requests slower than %.1fs", slow_s)
            return trace
    except Exception as exc:  # noqa: BLE001
        log.info("loop-watch: request tracer not installed: %s", exc)
    return None


def start(home: Path, *, stall_s: float = STALL_S, wait_for_loop_s: float = 600.0,
          slow_s: Optional[float] = 2.0) -> Optional[threading.Thread]:
    """Start the watcher once per process. Waits (in its own thread) for the main-thread loop."""
    if getattr(sys, _STARTED_ATTR, False):
        return None
    setattr(sys, _STARTED_ATTR, True)
    sink = default_sink(home / "logs" / "loop-watch.jsonl")

    def body() -> None:
        deadline = time.monotonic() + wait_for_loop_s
        loop = None
        while loop is None and time.monotonic() < deadline:
            loop = find_loop()
            if loop is None:
                time.sleep(2.0)
        if loop is None:
            log.info("loop-watch: no running main-thread event loop; not watching")
            return
        log.info("loop-watch: watching event loop of pid %d (stall >= %.1fs)", os.getpid(), stall_s)
        lw = LoopWatch(loop, getattr(loop, "_thread_id"), sink=sink, stall_s=stall_s)
        trace = _install_reqtrace(home, slow_s)
        if trace is not None:
            lw.on_tick = lambda: trace.tick(loop)
        lw.run()

    th = threading.Thread(target=body, name="loop-watch", daemon=True)
    th.start()
    return th
