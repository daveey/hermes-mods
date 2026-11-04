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
import atexit
import itertools
import uuid
import logging
import os
import re
import sys
import threading
import time
from collections import Counter
from functools import lru_cache
from typing import Any, Callable, Dict, Optional

log = logging.getLogger("loop-watch")

SLOW_S = 2.0
SAMPLE_AFTER_S = 1.0
SAMPLE_S = 0.25
MAX_SAMPLES = 40
CHAIN_DEPTH = 18
TOP_N = 6
MAX_REPORTS_PER_HOUR = 120
# Per path (query stripped) inside the hourly cap. With only the global cap, /api/sessions and
# /api/profiles/projects/tree spent all 120 by 00:32:59 (hub, 2026-11-03), and the fleet request behind a
# Desktop timeout at 00:33:03 left no record.
MAX_REPORTS_PER_PATH_PER_HOUR = 20
# Additive request_stats deltas, independently of slow-report caps. Production
# installs a dedicated 5s flusher; UTC hours group requests by completion time.
STATS_MAX_PATHS = 300  # per hour bucket per delta; excess folds into "(other)"
STATS_MAX_DURATIONS = 5000  # first samples per path per delta, NOT mergeable percentiles
STATS_MAX_QUERIES = 64  # distinct query strings counted per path per delta
STATS_EMIT_PATHS = 40  # legacy constant; v2 emits ALL bounded path counters
_ID_SEGMENT = re.compile(r"^(?=.*\d)[A-Za-z0-9_.:-]{8,}$")


@lru_cache(maxsize=4096)
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


def stats_path(path: str) -> str:
    """Path with id-like segments (session ids, card ids, hashes) folded to ``:id``, so per-path stats
    count one route, not one row per session."""
    path = path.split("?", 1)[0]
    return "/".join(":id" if _ID_SEGMENT.match(seg) else seg for seg in path.split("/"))


def _pct(sorted_vals: list, q: float) -> float:
    if not sorted_vals:
        return 0.0
    return sorted_vals[min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1))))]


class _Req:
    __slots__ = ("id", "method", "path", "t0", "task", "resp_at", "status", "samples", "others", "workers",
                 "last_sample", "query", "inflight_same")

    def __init__(self, rid: int, method: str, path: str, t0: float, task, query: str = "",
                 inflight_same: int = 0) -> None:
        self.id, self.method, self.path, self.t0, self.task = rid, method, path, t0, task
        self.query, self.inflight_same = query, inflight_same
        self.resp_at: Optional[float] = None
        self.status: Optional[int] = None
        self.samples: Counter = Counter()
        self.others: Counter = Counter()
        self.workers: Counter = Counter()
        self.last_sample = 0.0


class ReqTrace:
    def __init__(self, *, sink: Callable[[dict], None], slow_s: float = SLOW_S, sample_after_s: float = SAMPLE_AFTER_S,
                 sample_s: float = SAMPLE_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.sink, self.slow_s, self.sample_after_s, self.sample_s, self.clock = sink, slow_s, sample_after_s, sample_s, clock
        self.inflight: Dict[int, _Req] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._hour = (0, 0)  # (hour bucket, reports in it)
        self._per_path: Counter = Counter()  # reports per path in the current hour bucket
        self._stats_hour = 0
        self._stats: Dict[int, dict] = {}
        self._stats_lock = threading.RLock()
        self._stats_flush_lock = threading.RLock()
        self._stats_pending = None
        self._stats_queue = []
        self._stats_wake = threading.Event()
        self._stats_late_thread = None
        self._stats_id = uuid.uuid4().hex
        self._stats_seq = itertools.count(1)
        self._stats_since = time.time()
        self._stats_mono = time.monotonic()
        self._stats_interval = 5.0
        self._stats_dropped = 0
        self._stats_stop = threading.Event()
        self._stats_thread = None
        self._stats_closed = False

    # ---- ASGI side (runs on the loop) ----
    def wrap(self, app):
        trace = self

        async def traced(scope, receive, send):
            if scope.get("type") != "http":
                try:
                    return await app(scope, receive, send)
                finally:
                    if scope.get('type') == 'lifespan' and trace._stats_thread is not None:
                        trace.close_stats()
            path = scope.get("path", "?")
            try:
                query = (scope.get("query_string") or b"").decode("latin-1")[:300]
            except Exception:  # noqa: BLE001
                query = ""
            with trace._lock:
                same = sum(1 for r in trace.inflight.values() if r.path == path and r.resp_at is None)
                req = _Req(next(trace._ids), scope.get("method", "?"), path, trace.clock(),
                           asyncio.current_task(), query, same)
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

    def _record_stats(self, req: _Req, total: float, hour: int) -> None:
        with self._stats_lock:
            if self._stats_hour != hour:
                if self._stats_hour:
                    self._stats_wake.set()  # no disk I/O on the request thread
                self._stats_hour = hour
            # Bound memory even across a prolonged sink outage. A loss is explicit
            # in the next coverage receipt, never silently called complete coverage.
            if hour not in self._stats and len(self._stats) >= 2:
                lost = self._stats.pop(min(self._stats))
                self._stats_dropped += sum(st['n'] for st in lost.values())
            stats = self._stats.setdefault(hour, {})
            key = stats_path(req.path)
            if key not in stats and len(stats) >= STATS_MAX_PATHS:
                key = "(other)"
            st = stats.get(key)
            if st is None:
                st = stats[key] = {"n": 0, "slow": 0, "durs": [], "max_s": 0.0,
                                  "max_inflight": 0, "slow_queries": Counter(), "queries": Counter()}
            st["n"] += 1
            st["max_s"] = max(st["max_s"], total)
            if len(st["durs"]) < STATS_MAX_DURATIONS:
                st["durs"].append(total)
            st["max_inflight"] = max(st["max_inflight"], req.inflight_same + 1)
            if req.query in st["queries"] or len(st["queries"]) < STATS_MAX_QUERIES:
                st["queries"][req.query] += 1
            if total >= self.slow_s:
                st["slow"] += 1
                q = req.query
                if q in st["slow_queries"] or len(st["slow_queries"]) < STATS_MAX_QUERIES:
                    st["slow_queries"][q] += 1
            self._stats_closed = False
            if self._stats_stop.is_set() and self._stats_late_thread is None:
                # Late completions never perform storage I/O on the request loop.
                atexit.register(self.close_stats)
                def drain():
                    while True:
                        self.close_stats()
                        with self._stats_flush_lock, self._stats_lock:
                            if not self._stats or self._stats_pending is not None:
                                self._stats_late_thread = None
                                return
                self._stats_late_thread = threading.Thread(target=drain, name='loop-watch-stats-drain', daemon=True)
                self._stats_late_thread.start()

    def _stats_record(self, kind: str, **fields) -> dict:
        return dict(kind=kind, schema=2, record_id=f'{self._stats_id}:{next(self._stats_seq)}',
                    producer_id=self._stats_id, pid=os.getpid(), **fields)

    def _send_stats_queue(self) -> None:
        # Only the flush lock is held across sink I/O, never the recorder lock.
        while self._stats_pending is not None or self._stats_queue:
            if self._stats_pending is None:
                self._stats_pending = self._stats_queue.pop(0)
            self.sink(self._stats_pending)
            self._stats_pending = None

    def flush_stats(self, *, reason: str = "manual", coverage: bool = False) -> None:
        """Atomically detach a bounded batch; acknowledge it outside the data lock."""
        with self._stats_flush_lock:
            try:
                self._send_stats_queue()
                with self._stats_lock:
                    batches, self._stats = self._stats, {}
                    wall, mono = time.time(), time.monotonic()
                    dropped = self._stats_dropped
                    if coverage:
                        self._stats_dropped = 0
                for hour, stats in sorted(batches.items()):
                    paths = {}
                    for key, st in stats.items():
                        durs = sorted(st['durs'])
                        paths[key] = dict(n=st['n'], slow=st['slow'],
                                          p50_s=round(_pct(durs, .5), 3),
                                          p95_s=round(_pct(durs, .95), 3),
                                          max_s=round(st['max_s'], 3), duration_samples=len(durs),
                                          max_inflight=st['max_inflight'],
                                          queries=[[q, n] for q, n in st['queries'].most_common(8)],
                                          slow_queries=[[q, n] for q, n in st['slow_queries'].most_common(8)])
                    self._stats_queue.append(self._stats_record(
                        'request_stats', hour=time.strftime('%Y-%m-%dT%H', time.gmtime(hour * 3600)),
                        hour_epoch=hour * 3600, slow_s=self.slow_s, paths=paths,
                        semantics='delta', reason=reason))
                if coverage:
                    self._stats_queue.append(self._stats_record(
                        'request_stats_coverage', since=self._stats_since, through=wall,
                        elapsed_s=mono - self._stats_mono, interval_s=self._stats_interval,
                        dropped_requests=dropped, reason=reason))
                    self._stats_since, self._stats_mono = wall, mono
                self._send_stats_queue()
            except Exception as exc:  # noqa: BLE001
                log.warning('loop-watch request-stats sink failed: %s', exc)

    def start_stats(self, interval_s: float = 5.0) -> None:
        """Dedicated flusher: loop stalls and LoopWatch.check_once cannot delay it."""
        with self._stats_lock:
            if self._stats_thread is not None or self._stats_closed:
                return
            self._stats_interval = max(.01, float(interval_s))
            atexit.register(self.close_stats)

            def run():
                with self._stats_flush_lock:
                    if self._stats_stop.is_set():
                        return
                    self.flush_stats(reason='start', coverage=True)
                while not self._stats_stop.is_set():
                    self._stats_wake.wait(self._stats_interval)
                    self._stats_wake.clear()
                    with self._stats_flush_lock:
                        if self._stats_stop.is_set():
                            return
                        self.flush_stats(reason='periodic', coverage=True)
            self._stats_thread = threading.Thread(target=run, name='loop-watch-stats', daemon=True)
            self._stats_thread.start()

    def close_stats(self) -> None:
        self._stats_stop.set()
        self._stats_wake.set()
        with self._stats_flush_lock:
            with self._stats_lock:
                if self._stats_closed:
                    return
            self.flush_stats(reason='shutdown', coverage=True)
            with self._stats_lock:
                # Retry at atexit on sink failure; late requests have their own drain.
                self._stats_closed = self._stats_pending is None and not self._stats_queue and not self._stats
                if self._stats_closed:
                    atexit.unregister(self.close_stats)

    def _finish(self, req: _Req) -> None:
        end = req.resp_at if req.resp_at is not None else self.clock()
        total = end - req.t0
        hour = int(time.time() // 3600)
        try:
            self._record_stats(req, total, hour)
        except Exception as exc:  # noqa: BLE001
            log.debug("loop-watch request stats failed: %s", exc)
        if total < self.slow_s:
            return
        if self._hour[0] != hour:
            self._hour, self._per_path = (hour, 0), Counter()
        key = req.path.split("?", 1)[0]
        if self._per_path[key] >= MAX_REPORTS_PER_PATH_PER_HOUR or self._hour[1] >= MAX_REPORTS_PER_HOUR:
            return
        self._per_path[key] += 1
        self._hour = (hour, self._hour[1] + 1)
        wall0 = time.time() - (self.clock() - req.t0)
        rep = {
            "kind": "slow_request",
            "started": time.strftime("%H:%M:%S", time.localtime(wall0)) + ".%03d" % (int(wall0 * 1000) % 1000),
            "method": req.method,
            "path": req.path[:300],
            "query": req.query,
            "inflight_same_path": req.inflight_same,
            "total_s": round(total, 3),
            "responded": req.resp_at is not None,
            "status": req.status,
            "samples": sum(req.samples.values()),
            "awaiting": [{"n": n_, "chain": list(c)} for c, n_ in req.samples.most_common(TOP_N)],
            "other_tasks": [{"n": n_, "chain": list(c)} for c, n_ in req.others.most_common(TOP_N)],
            "worker_threads": [{"n": n_, "thread": t, "stack": list(st)}
                               for (t, st), n_ in req.workers.most_common(TOP_N)],
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
        workers = _worker_stacks()
        for r in due:
            r.workers.update(workers)
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
                        if ch and not _idle_chain(ch) and not _middleware_hop(ch):
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


# Executor threads that run request work: anyio's (sync FastAPI handlers and dependencies), asyncio's
# default executor (run_in_executor(None) / to_thread), and plugin pools (hub-sync). A request parked on
# ``run_sync_in_worker_thread`` is waiting on one of these; their stacks say what the handler is doing.
_WORKER_PREFIXES = ("AnyIO worker", "asyncio_", "ThreadPoolExecutor", "hub-sync")
# Innermost frames of an executor thread waiting for work (not running any).
_IDLE_WORKER_TAILS = {"_worker", "run", "get", "wait", "acquire", "_wait_for_tstate_lock"}


def _worker_stacks(depth: int = 10) -> Counter:
    out: Counter = Counter()
    try:
        names = {t.ident: t.name for t in threading.enumerate()}
        frames = sys._current_frames()
    except Exception:  # noqa: BLE001
        return out
    me = threading.get_ident()
    for tid, frame in frames.items():
        name = names.get(tid) or ""
        if tid == me or not name.startswith(_WORKER_PREFIXES):
            continue
        # Only metadata is reported. Traceback extraction checks source caches even
        # with lookup_lines=False, doing filesystem I/O on the event loop.
        stack = []
        while frame is not None and (depth <= 0 or len(stack) < depth):
            code = frame.f_code
            stack.append("%s:%d %s" % (_short(code.co_filename), frame.f_lineno, code.co_name))
            frame = frame.f_back
        stack.reverse()  # reports retain outer-to-inner order
        st = tuple(stack if depth > 0 else stack[-depth:])
        if not st:
            continue
        # Idle = parked in the pool's own queue wait: every frame is threading/queue/pool plumbing.
        if all(any(m in x for m in ("threading.py", "queue.py", "thread.py", "to_thread", "_asyncio.py"))
               for x in st) and st[-1].rsplit(" ", 1)[-1] in _IDLE_WORKER_TAILS:
            continue
        pool = name.rsplit("_", 1)[0] if name.startswith("asyncio_") else name.split(" ")[0]
        out[(pool, st)] += 1
    return out


def _middleware_hop(chain: tuple) -> bool:
    """A BaseHTTPMiddleware task parked in ``call_next`` only forwards to the next layer: pure noise."""
    return any(c.endswith(" call_next") for c in chain[-5:])


def _idle_chain(chain: tuple) -> bool:
    inner = [c for c in chain if not c.startswith("<awaiting")]
    if not inner:
        return True
    return inner[-1].rsplit(" ", 1)[-1] in _IDLE_TAILS


def default_sink(path, logger=log) -> Callable[[dict], None]:
    import json

    def sink(rep: dict) -> None:
        if rep.get("kind") != "slow_request":
            # Unlike diagnostic reports, counters require an acknowledged write.
            # Propagate errors so flush_stats retains the same delta for retry.
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rep) + "\n")
                f.flush()
                os.fsync(f.fileno())
            return
        chain = rep["awaiting"][0]["chain"] if rep["awaiting"] else []
        top = chain[-3:]
        # Behind a BaseHTTPMiddleware the request task only waits on call_next's stream (anyio Event.wait), which
        # names no handler: name the busiest worker thread instead, where the handler's time actually went.
        busy = ""
        if chain and _middleware_hop(tuple(chain[:-3])):
            workers = rep.get("worker_threads") or []
            if workers:
                w = workers[0]
                busy = " busy=%s(%d): %s" % (w["thread"], w["n"], " > ".join(w["stack"][-3:]))
        logger.warning("loop-watch: slow request %s %s %.2fs (started %s, responded=%s, samples=%d) awaiting=%s%s",
                       rep["method"], rep["path"] + ("?" + rep["query"] if rep.get("query") else ""), rep["total_s"], rep["started"], rep["responded"], rep["samples"],
                       " > ".join(top) or "-", busy)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rep) + "\n")
        except OSError:
            pass
    return sink


def _server_trace(app):
    """Follow conventional ASGI wrappers, including a closure's named app cell.

    Do not inspect arbitrary closure state or globally flush unrelated servers.
    Bounds and cycle detection keep foreign wrappers from trapping shutdown.
    """
    seen = set()
    for _ in range(16):
        if app is None or id(app) in seen:
            return None
        seen.add(id(app))
        owner = getattr(app, '__loop_watch_stats_trace__', None)
        if owner is not None:
            return owner
        inner = (getattr(app, 'app', None) or getattr(app, '__wrapped__', None)
                 or getattr(getattr(app, 'web', None), 'app', None))
        code = getattr(app, '__code__', None)
        cells = getattr(app, '__closure__', None)
        if inner is None and code is not None and cells:
            for name, cell in zip(code.co_freevars, cells):
                if name == 'app':
                    try:
                        inner = cell.cell_contents
                    except ValueError:
                        pass  # empty closure cell
                    break
        app = inner
    return None


def _install_server_shutdown(app, trace: ReqTrace) -> None:
    """Uvicorn re-raises SIGTERM after lifespan, before Python atexit can run.

    The watcher can attach after lifespan has already entered the OLD middleware
    stack, so wrapping only ASGI lifespan misses shutdown. Flush at the server's
    drained-shutdown boundary instead; preserve its arguments, result and errors.
    """
    try:
        import uvicorn
    except ImportError:
        return  # other ASGI hosts retain lifespan + atexit + periodic coverage
    app.__loop_watch_stats_trace__ = trace
    server = uvicorn.Server
    original = server.shutdown
    if getattr(original, '__loop_watch_stats_shutdown__', False):
        return

    async def shutdown(self, *args, **kwargs):
        try:
            return await original(self, *args, **kwargs)
        finally:
            owner = _server_trace(self.config.app)
            if owner is not None:
                owner.close_stats()
    shutdown.__loop_watch_stats_shutdown__ = True
    server.shutdown = shutdown


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
    _install_server_shutdown(app, trace)
    app.middleware_stack = trace.wrap(current)
    return True
