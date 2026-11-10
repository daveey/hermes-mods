"""busy-tree-guard: refuse edits to a git tree a running background test still reads.

A ``pre_tool_call`` hook on patch/write_file (and mcp__ names), tree-changing terminal commands
and nested ``tool_call`` batches. It reads this task's running background processes from the
Hermes process registry; an edit inside the git tree of a running test-like process is refused
with the process, its start time and command, and the ways out (another worktree, wait, kill).
Decisions live in ``guard.py`` (hot-reloaded per call like the other guards). Kill switch:
``BUSY_TREE_GUARD=0``. Refusals: ``$HERMES_HOME/logs/busy-tree-guard.log``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Keep this boundary in the activation module, not in the code it reloads.
# Each guard currently has exactly one sibling decision module: guard.py.
from functools import wraps as _wraps
import importlib.machinery as _machinery
import importlib.util as _import_util
from pathlib import Path as _Path
from threading import RLock as _RLock


class _DecisionSourceLoader(_machinery.SourceFileLoader):
    def get_code(self, fullname):
        # Timestamp pyc files have only second precision: never reuse stale code
        # for a same-size edit within a second, and never write runtime bytecode.
        return self.source_to_code(self.get_data(self.path), self.path)


_decision_path = _Path(__file__).with_name("guard.py")
_decision_lock = _RLock()


def _decision_stamp():
    try:
        st = _decision_path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError as exc:
        return ("unavailable", exc.errno)


def _load_decision():
    # No relative-import cache or timestamp pyc, including on initial activation.
    name = __name__ + ".guard"
    loader = _DecisionSourceLoader(name, str(_decision_path))
    spec = _import_util.spec_from_file_location(name, _decision_path, loader=loader)
    assert spec is not None and spec.loader is not None
    fresh = _import_util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    return fresh


# Stamp BEFORE loading: an edit racing initial activation is retried next call.
_decision_seen = _decision_stamp()
guard = _load_decision()


def _refresh_decisions():
    global guard, _decision_seen
    with _decision_lock:
        stamp = _decision_stamp()
        if stamp == _decision_seen:
            return
        # Remember failures too: warn once per changed fingerprint, not per call.
        _decision_seen = stamp
        try:
            fresh = _load_decision()
            if _decision_stamp() != stamp:
                raise RuntimeError("decision source changed during reload")
        except Exception:
            logger.warning("busy-tree-guard: decision reload failed; keeping last good guard.py", exc_info=True)
            return
        guard = fresh
        logger.info("busy-tree-guard: reloaded guard.py")


def _with_decisions(fn):
    @_wraps(fn)
    def call(*args, **kwargs):
        # Keep enabled/classify/message on one module version across threads.
        with _decision_lock:
            _refresh_decisions()
            return fn(*args, **kwargs)
    return call


_registry_override: Any = None


def guard_registry_override(reg: Any) -> None:
    """Tests: read processes from ``reg`` instead of the core registry."""
    global _registry_override
    _registry_override = reg


def _cwd(args: Any) -> str:
    args = args if isinstance(args, dict) else {}
    return str(args.get("workdir") or os.environ.get("TERMINAL_CWD") or os.getcwd())


@_with_decisions
def _pre_tool_call(tool_name: str = "", args: Any = None, task_id: str = "", **_: Any) -> Optional[dict]:
    try:
        if not guard.enabled():
            return None
        guard.set_registry(_registry_override)
        hit = guard.check(tool_name, args, task_id or "", _cwd(args))
    except Exception:  # never let this plugin break a tool call
        logger.debug("busy-tree-guard check failed", exc_info=True)
        return None
    if not hit:
        return None
    target, tree, proc = hit
    logger.info("busy-tree-guard blocked %s: %s in %s (proc %s)", tool_name, target, tree, proc.get("session_id"))
    guard.record(tool_name, target, tree, proc)
    return {"action": "block", "message": guard.message(target, tree, proc)}


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
