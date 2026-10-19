"""kanban-worker-nice: run Kanban workers (and everything they spawn) at a lower CPU priority.

When the plugin loads inside a dispatcher-spawned Kanban worker (``HERMES_KANBAN_TASK`` set), it
raises the worker process's own niceness. Niceness is inherited across fork/exec, so every child
the worker starts afterwards (terminal shells, xcodebuild + SWBBuildService + swift-frontend,
test runners, cargo...) runs niced too. Desktop chats, the gateway and the user's own terminals
are untouched.

Config: env ``HERMES_KANBAN_WORKER_NICE`` (0-20, default 10). 0 disables.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_NICE = 10
ENV_VAR = "HERMES_KANBAN_WORKER_NICE"


def target_nice(env=None) -> int:
    env = os.environ if env is None else env
    raw = env.get(ENV_VAR)
    if raw is None or str(raw).strip() == "":
        return DEFAULT_NICE
    try:
        val = int(str(raw).strip())
    except ValueError:
        return DEFAULT_NICE
    return max(0, min(20, val))


def apply(env=None, getprio=None, setprio=None) -> int | None:
    """Raise this process's niceness to the target if it's a Kanban worker.

    Returns the niceness set, or None when nothing was changed. Never lowers niceness (that
    needs root and would undo an operator's own ``renice``).
    """
    env = os.environ if env is None else env
    if not env.get("HERMES_KANBAN_TASK"):
        return None
    want = target_nice(env)
    if want <= 0 or not hasattr(os, "setpriority"):
        return None
    getprio = getprio or (lambda: os.getpriority(os.PRIO_PROCESS, 0))
    setprio = setprio or (lambda n: os.setpriority(os.PRIO_PROCESS, 0, n))
    try:
        cur = getprio()
        if cur >= want:
            return None
        setprio(want)
        return want
    except OSError as exc:  # pragma: no cover - permission/platform oddities
        logger.debug("kanban-worker-nice: setpriority failed: %s", exc)
        return None


def register(ctx) -> None:
    applied = apply()
    if applied is not None:
        logger.info("kanban-worker-nice: worker %s niced to %d", os.environ.get("HERMES_KANBAN_TASK"), applied)
