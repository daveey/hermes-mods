"""Slow-request tracer: names what an HTTP request was awaiting when it answered late.

A stalled event loop is one cause of Desktop "Timed out connecting to Hermes backend" errors; loop-watch's
LoopWatch catches that. The other cause is a request that is slow while the loop stays responsive: it is
parked on an ``await`` (an executor future, a lock, a child task, a sleep). Nothing blocks, so nothing
else reports it. This wraps the ASGI app; any request still waiting for its response start after
``sample_after_s`` gets its asyncio await chain (and every other task's not-idle chain) sampled from the
loop every ``sample_s``. When the response finally starts at or after ``slow_s`` (or never, by disconnect),
one report goes to the sink: method, path, total time, the sampled chains with counts.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
import time
from collections import Counter
from typing import Any, Callable, Dict, Optional

log = logging.getLogger("loop-watch")

SLOW_S = 2.0
SAMPLE_AFTER_S = 1.0
SAMPLE_S = 0.25
MAX_SAMPLES = 40
CHAIN_DEPTH = 18
TOP_N = 6
MAX_REPORTS_PER_HOUR = 120


def _short(path: str) -> str:
    for marker in ("/hermes-agent/", "/site-packages/", "/plugins/", "/lib/python"):
        i = path.rfind(marker)
        if i >= 0:
            return path[i + len(marker):]
    return path


def _coro_chain(coro, depth: int = CHAIN_DEPTH) -> list:
    """Outer→inner frames of a suspended coroutine chain (follows cr_await / ag_await / gi_yieldfrom)."""
    out = []
    seen = 0
    while coro is not None and seen < 64:
        seen += 1
        frame = getattr(coro, "cr_frame", None) or getattr(coro, "ag_frame", None) or getattr(coro, "gi_frame", None)
        if frame is not None:
            out.append("%s:%d %s" % (_short(frame.f_code.co_filename), frame.f_lineno, frame.f_code.co_name))
        nxt = getattr(coro, "cr_await", None)
        if nxt is None:
            nxt = getattr(coro, "ag_await", None)
        if nxt is None:
            nxt = getattr(coro, "gi_yieldfrom", None)
        if nxt is None and frame is None:
            break
        if nxt is not None and not (hasattr(nxt, "cr_frame") or hasattr(nxt, "ag_frame") or hasattr(nxt, "gi_frame")):
            out.append("<awaiting %s>" % type(nxt).__name__)
            break
        coro = nxt
    return out[-depth:]


def _task_chain(task) -> tuple:
    try:
        return tuple(_coro_chain(task.get_coro()))
    except Exception:  # noqa: BLE001
        return ()


class _Req:
    __slots__ = ("id", "method", "path", "t0", "task", "resp_at", "status", "samples", "others", "last_sample")

    def __init__(self, rid: int, method: str, path: str, t0: float, task) -> None:
        self.id, self.method, self.path, self.t0, self.task = rid, method, path, t0, task
        self.resp_at: Optional[float] = None
        self.status: Optional[int] = None
        self.samples: Counter = Counter()
        self.others: Counter = Counter()
        self.last_sample = 0.0


class ReqTrace:
    def __init__(self, *, sink: Callable[[dict], None], slow_s: float = SLOW_S, sample_after_s: float = SAMPLE_AFTER_S,
                 sample_s: float = SAMPLE_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.sink, self.slow_s, self.sample_after_s, self.sample_s, self.clock = sink, slow_s, sample_after_s, sample_s, clock
        self.inflight: Dict[int, _Req] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._hour = (0, 0)  # (hour bucket, reports in it)

    # ---- ASGI side (runs on the loop) ----
    def wrap(self, app):
        trace = self

        async def traced(scope, receive, send):
            if scope.get("type") != "http":
                return await app(scope, receive, send)
            req = _Req(next(trace._ids), scope.get("method", "?"), scope.get("path", "?"), trace.clock(),
                       asyncio.current_task())
            with trace._lock:
                trace.inflight[req.id] = req

            async def send_wrapper(message):
                if message.get("type") == "http.response.start" and req.resp_at is None:
                    req.resp_at = trace.clock()
                    req.status = message.get("status")
                return await send(message)

            try:
                return await app(scope, receive, send_wrapper)
            finally:
                with trace._lock:
                    trace.inflight.pop(req.id, None)
                trace._finish(req)

        traced.__loop_watch_traced__ = True
        return traced

    def _finish(self, req: _Req) -> None:
        end = req.resp_at if req.resp_at is not None else self.clock()
        total = end - req.t0
        if total < self.slow_s:
            return
        hour = int(time.time() // 3600)
        bucket, n = self._hour
        n = n + 1 if bucket == hour else 1
        self._hour = (hour, n)
        if n > MAX_REPORTS_PER_HOUR:
            return
        wall0 = time.time() - (self.clock() - req.t0)
        rep = {
            "kind": "slow_request",
            "started": time.strftime("%H:%M:%S", time.localtime(wall0)) + ".%03d" % (int(wall0 * 1000) % 1000),
            "method": req.method,
            "path": req.path[:300],
            "total_s": round(total, 3),
            "responded": req.resp_at is not None,
            "status": req.status,
            "samples": sum(req.samples.values()),
            "awaiting": [{"n": n_, "chain": list(c)} for c, n_ in req.samples.most_common(TOP_N)],
            "other_tasks": [{"n": n_, "chain": list(c)} for c, n_ in req.others.most_common(TOP_N)],
        }
        try:
            self.sink(rep)
        except Exception as exc:  # noqa: BLE001
            log.debug("loop-watch slow-request sink failed: %s", exc)

    def sample_on_loop(self) -> None:
        """Called ON the loop: sample await chains of every request waiting past ``sample_after_s``."""
        now = self.clock()
        with self._lock:
            due = [r for r in self.inflight.values()
                   if r.resp_at is None and now - r.t0 >= self.sample_after_s
                   and now - r.last_sample >= self.sample_s * 0.9 and sum(r.samples.values()) < MAX_SAMPLES]
        if not due:
            return
        others = None
        for r in due:
            r.last_sample = now
            if r.task is not None:
                r.samples[_task_chain(r.task)] += 1
            if others is None:
                others = Counter()
                req_tasks = {id(x.task) for x in due}
                try:
                    for t in asyncio.all_tasks():
                        if id(t) in req_tasks:
                            continue
                        ch = _task_chain(t)
                        if ch and not _idle_chain(ch):
                            others[ch] += 1
                except RuntimeError:
                    pass
            r.others.update(others)

    # ---- watcher-thread side ----
    def tick(self, loop) -> None:
        """From the watcher thread: schedule a sample on the loop if any request is overdue."""
        now = self.clock()
        with self._lock:
            overdue = any(r.resp_at is None and now - r.t0 >= self.sample_after_s for r in self.inflight.values())
        if overdue:
            try:
                loop.call_soon_threadsafe(self.sample_on_loop)
            except RuntimeError:
                pass


# A task chain whose innermost frame is one of these is a long-lived idle waiter (server accept loop,
# websocket reader, periodic sleeper); listing them would bury the one task the request waits on.
_IDLE_TAILS = ("sleep", "serve_forever", "wait_closed", "_accept_connection", "receive", "_run_until_stopped",
               "main_loop", "on_tick", "startup", "_serve")


def _idle_chain(chain: tuple) -> bool:
    inner = [c for c in chain if not c.startswith("<awaiting")]
    if not inner:
        return True
    return inner[-1].rsplit(" ", 1)[-1] in _IDLE_TAILS


def default_sink(path, logger=log) -> Callable[[dict], None]:
    import json

    def sink(rep: dict) -> None:
        top = rep["awaiting"][0]["chain"][-3:] if rep["awaiting"] else []
        logger.warning("loop-watch: slow request %s %s %.2fs (started %s, responded=%s, samples=%d) awaiting=%s",
                       rep["method"], rep["path"], rep["total_s"], rep["started"], rep["responded"], rep["samples"],
                       " > ".join(top) or "-")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rep) + "\n")
        except OSError:
            pass
    return sink


def install_on_app(app, trace: ReqTrace) -> bool:
    """Wrap a running Starlette/FastAPI app's middleware stack once. True when installed."""
    current = getattr(app, "middleware_stack", None)
    if current is not None and getattr(current, "__loop_watch_traced__", False):
        return False
    if current is None:
        build = getattr(app, "build_middleware_stack", None)
        if build is None:
            return False
        current = build()
    app.middleware_stack = trace.wrap(current)
    return True
