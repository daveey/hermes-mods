"""loop-watch: log what held a Hermes backend's event loop whenever it stalls.

In a ``hermes serve`` process (Desktop backends, SSH-isolated backends) this starts a daemon thread
that probes the main asyncio loop every 0.25 s. A probe that waits >= 1 s is a stall: the thread
samples every thread's Python stack until the loop catches up, then writes one line to agent.log
(``loop-watch: event loop stalled …``) and the full report to ``<HERMES_HOME>/logs/loop-watch.jsonl``.
A stalled loop answers no HTTP request at all, which the Desktop logs as "Timed out connecting to
Hermes backend"; this names the code that held it.

Config: ``HERMES_LOOP_WATCH=off`` disables; ``HERMES_LOOP_WATCH=all`` also watches non-serve
processes; ``HERMES_LOOP_WATCH_STALL_S`` (default 1.0) sets the stall threshold.

It also wraps the serve app with a slow-request tracer (reqtrace.py): a request that waits >= 1 s for
its response start has its asyncio await chain sampled; if it answers after >= 2 s
(``HERMES_LOOP_WATCH_SLOW_S``; 0 disables) it gets one ``loop-watch: slow request …`` line and a
``kind: slow_request`` report in the same jsonl. That covers the case a stall can't: the loop is
responsive but the request is parked on an await (executor queue, lock, child task).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from . import watch
except ImportError:  # loaded without a parent package (tests, hyphenated dir)
    import importlib.util

    _spec = importlib.util.spec_from_file_location("loop_watch_watch", Path(__file__).with_name("watch.py"))
    watch = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(watch)


def is_serve_process(argv=None) -> bool:
    argv = sys.argv if argv is None else argv
    return len(argv) > 1 and argv[1] == "serve"


def wanted(env=None, argv=None) -> bool:
    env = os.environ if env is None else env
    mode = str(env.get("HERMES_LOOP_WATCH", "")).strip().lower()
    if mode in ("off", "0", "false", "no"):
        return False
    return mode == "all" or is_serve_process(argv)


def _stall_s(env=None) -> float:
    env = os.environ if env is None else env
    try:
        return max(0.2, float(env.get("HERMES_LOOP_WATCH_STALL_S", watch.STALL_S)))
    except ValueError:
        return watch.STALL_S


def _slow_s(env=None) -> float:
    env = os.environ if env is None else env
    try:
        return float(env.get("HERMES_LOOP_WATCH_SLOW_S", 2.0))
    except ValueError:
        return 2.0


def register(ctx) -> None:
    if not wanted():
        return
    try:
        from hermes_constants import get_hermes_home
        home = Path(get_hermes_home())
    except Exception:  # noqa: BLE001
        home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    watch.start(home, stall_s=_stall_s(), slow_s=_slow_s())
