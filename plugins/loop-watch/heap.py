"""Opt-in, one-shot tracemalloc heap census: live Python allocations grouped by source line.

Default off. ``HERMES_LOOP_WATCH_HEAP_CENSUS=<frames>`` (1..32) starts ``tracemalloc`` with that many
frames when loop-watch registers, and arms the same private one-shot trigger as the cache census
(census.Probe) with the command ``heap-census-v1``. The report holds only code locations (file:line),
byte sizes and block counts: never object values, keys or reprs. Tracing costs memory and CPU on every
allocation, so enable it for a diagnostic run only. If no request is consumed within
``HERMES_LOOP_WATCH_HEAP_CENSUS_TTL_S`` (default 6 h) the census stops the tracing it started and writes
``status: expired``, so a forgotten or leaked opt-in cannot trace for a process's whole life.
"""
from __future__ import annotations

import collections
import gc
import logging
import math
import os
import sys
import sysconfig
import threading
import time
import tracemalloc

COMMAND = b"heap-census-v1\n"
MAX_OUTPUT = 65536
TOP = 40
ENV = "HERMES_LOOP_WATCH_HEAP_CENSUS"
TTL_ENV = "HERMES_LOOP_WATCH_HEAP_CENSUS_TTL_S"
DEFAULT_TTL_S = 21600.0  # 6 h
log = logging.getLogger("loop-watch")


def frames(env=None):
    """Frame depth requested by the env var, or 0 (off) when unset/invalid/out of range."""
    env = os.environ if env is None else env
    try:
        value = int(env.get(ENV, "0"))
    except ValueError:
        return 0
    return value if 1 <= value <= 32 else 0


def ttl(env=None):
    """Seconds to wait for a request before tracing stops on its own; the default when unset/invalid/<= 0."""
    env = os.environ if env is None else env
    try:
        value = float(env.get(TTL_ENV, ""))
    except ValueError:
        return DEFAULT_TTL_S
    return value if math.isfinite(value) and value > 0 else DEFAULT_TTL_S


def _library_roots():
    roots = set()
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        path = sysconfig.get_paths().get(key)
        if path:
            roots.add(os.path.realpath(path) + os.sep)
            roots.add(path + os.sep)
    return tuple(sorted(roots, key=len, reverse=True))


def _short(path, home=os.path.expanduser("~")):
    return "~" + path[len(home):] if home and path.startswith(home + os.sep) else path


def _is_app(filename, libs):
    return not (filename.startswith("<") or "site-packages" in filename or filename.startswith(libs))


def _top(counter, counts, n=TOP):
    return [[key, size, counts[key]] for key, size in counter.most_common(n)]


def scan(*, clock=time.monotonic, top=TOP):
    """Aggregate the current tracemalloc snapshot. ``unknown`` when tracing is off."""
    started = clock()
    report = dict(schema=1, kind="heap_census", status="unknown", traced_bytes=None, peak_bytes=None,
                  tracemalloc_overhead_bytes=None, traces=0, frames=tracemalloc.get_traceback_limit(),
                  gc_counts=list(gc.get_count()), gc_collections=[s.get("collections") for s in gc.get_stats()],
                  by_line=[], by_app_line=[], by_app_entry=[], seconds=None)
    if not tracemalloc.is_tracing():
        return report
    snapshot = tracemalloc.take_snapshot().filter_traces((
        tracemalloc.Filter(False, tracemalloc.__file__), tracemalloc.Filter(False, __file__)))
    current, peak = tracemalloc.get_traced_memory()
    report.update(traced_bytes=current, peak_bytes=peak, tracemalloc_overhead_bytes=tracemalloc.get_tracemalloc_memory())
    libs = _library_roots()
    sizes = [collections.Counter() for _ in range(3)]
    counts = [collections.Counter() for _ in range(3)]
    for trace in snapshot.traces:
        report["traces"] += 1
        tb = trace.traceback  # oldest frame first
        innermost = tb[-1]
        keys = [f"{_short(innermost.filename)}:{innermost.lineno}", None, None]
        app = [fr for fr in tb if _is_app(fr.filename, libs)]
        if app:
            keys[1] = f"{_short(app[-1].filename)}:{app[-1].lineno}"
            keys[2] = f"{_short(app[0].filename)}:{app[0].lineno}"
        else:
            keys[1] = keys[2] = "(library only)"
        for i, key in enumerate(keys):
            sizes[i][key] += trace.size
            counts[i][key] += 1
    report["by_line"] = _top(sizes[0], counts[0], top)
    report["by_app_line"] = _top(sizes[1], counts[1], top)
    report["by_app_entry"] = _top(sizes[2], counts[2], top)
    report["status"] = "ok"
    report["seconds"] = round(clock() - started, 3)
    return report


def safe_scan():
    """Never let the census thread die without a result: any failure reports ``unknown``."""
    try:
        return scan()
    except Exception as exc:  # noqa: BLE001 (MemoryError/RuntimeError from the snapshot)
        return dict(schema=1, kind="heap_census", status="unknown", error=type(exc).__name__)


def start(home, *, env=None, probe_factory=None, clock=time.monotonic, sleep=time.sleep):
    depth = frames(env)
    if not depth:
        return None
    if getattr(sys, "_hermes_loop_watch_heap_census", False):
        return None
    setattr(sys, "_hermes_loop_watch_heap_census", True)
    limit = ttl(env)
    started_here = not tracemalloc.is_tracing()
    if started_here:
        tracemalloc.start(depth)
    # Loud on purpose: a leaked opt-in (e.g. a relayed .env key) must show up in agent.log, not only as slowness.
    log.warning("loop-watch: %s=%d heap census armed in pid %d (tracemalloc %s); tracing stops after the "
                "heap-census-v1 request or on its own after %ss (%s)", ENV, depth, os.getpid(),
                "started" if started_here else "was already on, left alone", f"{limit:g}", TTL_ENV)

    def release():
        # One-shot: tracing has no further use after the report, the TTL, or without a probe.
        if started_here and tracemalloc.is_tracing():
            tracemalloc.stop()
    if probe_factory is None:
        try:
            from . import census
        except ImportError:
            import importlib.util
            from pathlib import Path
            spec = importlib.util.spec_from_file_location("loop_watch_census", Path(__file__).with_name("census.py"))
            census = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(census)
        probe_factory = census.Probe
    try:
        probe = probe_factory(home, command=COMMAND, scanner=safe_scan, max_output=MAX_OUTPUT, subdir="loop-watch-heap")
    except Exception:  # noqa: BLE001
        release()
        log.warning("loop-watch: heap census could not arm its request directory; tracemalloc %s",
                    "stopped" if started_here else "left on (loop-watch did not start it)")
        return None

    deadline = clock() + limit

    def expire():
        probe.used = True  # the one opportunity is spent: a later request is never consumed
        release()
        try:
            probe.write("result.json", dict(schema=1, kind="heap_census", status="expired", ttl_s=limit,
                                            frames=depth, tracing_left_on=not started_here))
        except OSError:
            pass
        log.warning("loop-watch: heap census expired after %ss with no request; tracemalloc %s",
                    f"{limit:g}", "stopped" if started_here else "left on (loop-watch did not start it)")

    def run():
        try:
            while not probe.used:
                sleep(1)
                probe.tick()
                if not probe.used and clock() >= deadline:
                    expire()
        finally:
            probe.close()
            release()
    thread = threading.Thread(target=run, name="loop-watch-heap-census", daemon=True)
    thread.start()
    return thread
