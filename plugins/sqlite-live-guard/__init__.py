"""sqlite-live-guard: stop Hermes sessions from deleting, swapping or raw-writing live SQLite databases.

A ``pre_tool_call`` hook blocks terminal / process / execute_code calls (and the same calls nested in
``tool_call``) that would ``rm`` or ``mv`` a live ``*.db`` / ``-wal`` / ``-shm`` under the Hermes home,
overwrite one with ``cp``/``dd``/a redirect, or write to one with the ``sqlite3`` CLI. Reads stay allowed.
Recovered copies go in through ``swap_db.py`` (single-owner lock, holder check, originals kept).
``claude_hook.py`` applies the same rule to Claude Code's Bash tool.
"""

from __future__ import annotations

import logging
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
            logger.warning("sqlite-live-guard: decision reload failed; keeping last good guard.py", exc_info=True)
            return
        guard = fresh
        logger.info("sqlite-live-guard: reloaded guard.py")


def _with_decisions(fn):
    @_wraps(fn)
    def call(*args, **kwargs):
        # Keep enabled/classify/message on one module version across threads.
        with _decision_lock:
            _refresh_decisions()
            return fn(*args, **kwargs)
    return call


SHELL_TOOLS = {"terminal", "process_manage", "process", "mcp__terminal", "mcp__process_manage"}
PY_TOOLS = {"execute_code", "mcp__execute_code"}


def classify(tool_name: str, args: Any) -> Optional[str]:
    args = args if isinstance(args, dict) else {}
    if tool_name in SHELL_TOOLS:
        cmd = " ; ".join(str(args.get(k) or "") for k in ("command", "input", "data") if args.get(k))
        return guard.check_shell(cmd, str(args.get("workdir") or ""))
    if tool_name in PY_TOOLS:
        return guard.check_python(str(args.get("code") or ""))
    if tool_name in {"tool_call", "mcp__tool_call"}:
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                why = classify(str(call.get("name") or ""), call.get("arguments"))
                if why:
                    return why
    return None


def _command_text(tool_name: str, args: Any) -> str:
    args = args if isinstance(args, dict) else {}
    if tool_name in {"tool_call", "mcp__tool_call"}:
        return " ; ".join(_command_text(str(c.get("name") or ""), c.get("arguments"))
                          for c in (args.get("calls") or []) if isinstance(c, dict))
    return str(args.get("command") or args.get("code") or args.get("input") or args.get("data") or "")


@_with_decisions
def _pre_tool_call(tool_name: str = "", args: Any = None, session_id: str = "", **_: Any) -> Optional[dict]:
    try:
        if not guard.enabled():
            return None
        why = classify(tool_name, args)
    except Exception:  # never let this plugin break a tool call
        logger.debug("sqlite-live-guard classify failed", exc_info=True)
        return None
    if not why:
        return None
    logger.info("sqlite-live-guard blocked %s: %s", tool_name, why)
    try:
        # The hook's own task_id is the agent's tool-task id, not a Kanban card: the card comes from the env.
        guard.log_refusal(tool_name, why, _command_text(tool_name, args), str(session_id or ""))
    except Exception:
        logger.debug("sqlite-live-guard log failed", exc_info=True)
    return {"action": "block", "message": guard.message(why)}


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
