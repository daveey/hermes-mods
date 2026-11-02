"""mac-heavy-guard: keep heavy builds, whole test suites and whole-repo installs off the laptop.

A ``pre_tool_call`` hook blocks terminal / process calls whose command is heavy work (see
guard.py) and points the agent at the hub / worker-1, a narrower command, or the
``MAC_HEAVY_OK=1`` opt-out. A multi-file Python test run in a Hermes core worktree is allowed, and
``transform_tool_result`` appends a note to its result pointing at ``hermes-core-wt test``.
Acts only on macOS. ``claude_hook.py`` applies the same rule to Claude Code's Bash tool.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
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
            logger.warning("mac-heavy-guard: decision reload failed; keeping last good guard.py", exc_info=True)
            return
        guard = fresh
        logger.info("mac-heavy-guard: reloaded guard.py")


def _with_decisions(fn):
    @_wraps(fn)
    def call(*args, **kwargs):
        # Keep enabled/classify/message on one module version across threads.
        with _decision_lock:
            _refresh_decisions()
            return fn(*args, **kwargs)
    return call


SHELL_TOOLS = {"terminal", "process_manage", "process"}


def _terminal_cwd(task_id: str) -> Optional[str]:
    """The persistent shell's current directory for this task (a previous `cd` call), if Hermes tracks one."""
    try:
        from tools import terminal_tool as tt
        env = tt._active_environments.get(tt._resolve_container_task_id(task_id)) or tt._active_environments.get(task_id)
        cwd = getattr(env, "cwd", None)
        return cwd if isinstance(cwd, str) and cwd else None
    except Exception:
        return None


def _cwd_for(args: dict, task_id: str) -> Optional[str]:
    return args.get("workdir") or (_terminal_cwd(task_id) if task_id else None)


def classify(tool_name: str, args: Any, task_id: str = "") -> Optional[str]:
    args = args if isinstance(args, dict) else {}
    if tool_name in SHELL_TOOLS:
        return guard.check_shell(str(args.get("command") or ""), cwd=_cwd_for(args, task_id))
    if tool_name in {"tool_call", "mcp__tool_call"}:
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                why = classify(str(call.get("name") or ""), call.get("arguments"), task_id)
                if why:
                    return why
    return None


def _block_message(tool_name: str, args: Any, task_id: str) -> Optional[str]:
    args = args if isinstance(args, dict) else {}
    if tool_name in SHELL_TOOLS:
        return guard.evaluate(str(args.get("command") or ""), cwd=_cwd_for(args, task_id))[0]
    if tool_name in {"tool_call", "mcp__tool_call"}:
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                msg = _block_message(str(call.get("name") or ""), call.get("arguments"), task_id)
                if msg:
                    return msg
    return None


# Notes computed before the call (the shell's cwd then is the one the command starts in) and appended to its
# result by transform_tool_result. A batched tool_call's inner terminal calls go through the same hooks.
_pending_notes: "OrderedDict[tuple, str]" = OrderedDict()
_PENDING_MAX = 64


def _note_key(tool_name: str, args: Any, task_id: str, tool_call_id: str) -> tuple:
    command = str((args if isinstance(args, dict) else {}).get("command") or "")
    return (tool_call_id,) if tool_call_id else (task_id, tool_name, command)


@_with_decisions
def _pre_tool_call(tool_name: str = "", args: Any = None, task_id: str = "", tool_call_id: str = "",
                   **_: Any) -> Optional[dict]:
    try:
        if not guard.enabled():
            return None
        msg = _block_message(tool_name, args, task_id or "")
        if not msg and tool_name in SHELL_TOOLS and isinstance(args, dict):
            note = guard.note(str(args.get("command") or ""), cwd=_cwd_for(args, task_id or ""))
            if note:
                _pending_notes[_note_key(tool_name, args, task_id or "", tool_call_id or "")] = note
                while len(_pending_notes) > _PENDING_MAX:
                    _pending_notes.popitem(last=False)
    except Exception:  # never let this plugin break a tool call
        logger.debug("mac-heavy-guard classify failed", exc_info=True)
        return None
    if not msg:
        return None
    logger.info("mac-heavy-guard blocked %s", tool_name)
    return {"action": "block", "message": msg}


def _transform_tool_result(tool_name: str = "", args: Any = None, result: Any = None, task_id: str = "",
                           tool_call_id: str = "", **_: Any) -> Optional[str]:
    """Append the pending core-worktree note to a multi-file test run's result (None = unchanged)."""
    if tool_name not in SHELL_TOOLS or not _pending_notes:
        return None
    note = _pending_notes.pop(_note_key(tool_name, args, task_id or "", tool_call_id or ""), None)
    if not note or not isinstance(result, str):
        return None
    return f"{result}\n\n[{note}]"


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("transform_tool_result", _transform_tool_result)
