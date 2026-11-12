"""evidence-clobber-guard: refuse shell writes that overwrite an existing evidence file, and shell
writes/deletes of another session's file in a shared scratch folder.

A ``pre_tool_call`` hook on terminal (and nested ``tool_call``) blocks ``cmd > FILE``,
``cmd &> FILE``, ``cmd | tee FILE`` (no ``-a``) when FILE already exists under a
``*/kanban/evidence/*`` directory, so a second test/mutation run can never wipe the first
run's log. Under ``$HERMES_HOME/cache/scratch/<dir>/`` (often a parent's cwd that subagents
inherit) it also blocks those writes and ``rm`` of files older than the task's first tool call.
Decisions live in ``guard.py`` (hot-reloaded per call like the other guards).
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
            logger.warning("evidence-clobber-guard: decision reload failed; keeping last good guard.py", exc_info=True)
            return
        guard = fresh
        logger.info("evidence-clobber-guard: reloaded guard.py")


def _with_decisions(fn):
    @_wraps(fn)
    def call(*args, **kwargs):
        # Keep enabled/classify/message on one module version across threads.
        with _decision_lock:
            _refresh_decisions()
            return fn(*args, **kwargs)
    return call


SHELL_TOOLS = {"terminal"}


def _cwd(args: dict, task_id: str = "") -> str:
    if args.get("workdir"):
        return str(args["workdir"])
    if task_id:  # a subagent's cwd record is seeded from its parent's (the shared-scratch case)
        try:
            from tools.terminal_tool import get_session_cwd
            cwd = get_session_cwd(task_id)
            if cwd:
                return str(cwd)
        except Exception:
            pass
    return str(os.environ.get("TERMINAL_CWD") or os.getcwd())


def classify(tool_name: str, args: Any, task_id: str = "", started: Optional[float] = None) -> Optional[str]:
    args = args if isinstance(args, dict) else {}
    if tool_name in SHELL_TOOLS:
        return guard.check_shell(str(args.get("command") or ""), _cwd(args, task_id), started=started)
    if tool_name in {"tool_call", "mcp__tool_call"}:
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                hit = classify(str(call.get("name") or ""), call.get("arguments"), task_id, started)
                if hit:
                    return hit
    return None


@_with_decisions
def _pre_tool_call(tool_name: str = "", args: Any = None, task_id: str = "", session_id: str = "",
                   **_: Any) -> Optional[dict]:
    try:
        if not guard.enabled():
            return None
        # Every tool call (not just shell) stamps the task's start: files older than it are someone else's.
        started = guard.task_started(str(task_id or session_id or ""))
        target = classify(tool_name, args, str(task_id or ""), started)
    except Exception:  # never let this plugin break a tool call
        logger.debug("evidence-clobber-guard classify failed", exc_info=True)
        return None
    if not target:
        return None
    logger.info("evidence-clobber-guard blocked %s: %s", tool_name, target)
    guard.record(tool_name, target, str((args or {}).get("command") or args))
    return {"action": "block", "message": guard.message(target)}


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
