"""Opt-in, one-shot tracemalloc heap census: live Python allocations grouped by source line.

Default off. ``HERMES_LOOP_WATCH_HEAP_CENSUS=<frames>`` (1..32) starts ``tracemalloc`` with that many
frames when loop-watch registers, and arms the same private one-shot trigger as the cache census
(census.Probe) with the command ``heap-census-v1``. The report holds only code locations (file:line),
byte sizes and block counts: never object values, keys or reprs. Tracing costs memory and CPU for the
life of the process, so enable it for a diagnostic run only.
"""
from __future__ import annotations

import collections
import gc
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


def frames(env=None):
    """Frame depth requested by the env var, or 0 (off) when unset/invalid/out of range."""
    env = os.environ if env is None else env
    try:
        value = int(env.get(ENV, "0"))
    except ValueError:
        return 0
    return value if 1 <= value <= 32 else 0


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


def start(home, *, env=None, probe_factory=None):
    depth = frames(env)
    if not depth:
        return None
    if getattr(sys, "_hermes_loop_watch_heap_census", False):
        return None
    setattr(sys, "_hermes_loop_watch_heap_census", True)
    started_here = not tracemalloc.is_tracing()
    if started_here:
        tracemalloc.start(depth)

    def release():
        # One-shot: tracing has no further use after the report (or without a probe).
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
        return None

    def run():
        try:
            while not probe.used:
                time.sleep(1)
                probe.tick()
        finally:
            probe.close()
            release()
    thread = threading.Thread(target=run, name="loop-watch-heap-census", daemon=True)
    thread.start()
    return thread
