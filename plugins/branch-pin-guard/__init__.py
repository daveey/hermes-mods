"""branch-pin-guard: keep pinned checkouts (sync mirrors, deploy checkouts) on their branch.

- ``pre_tool_call`` blocks terminal / execute_code / process calls whose `git checkout|switch` would move a pinned
  checkout off its branch, and points the agent at `git worktree add`.
- ``transform_tool_result`` is the safety net: after a call that ran checkout/switch, if any pinned checkout is off
  its branch anyway (cwd the hook couldn't see, a script), it appends how to put it back.
Pins: ``$HERMES_HOME/branch-pins.json`` ({"~/code/repo": "branch"}). ``claude_hook.py`` = the Claude Code version.
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
            logger.warning("branch-pin-guard: decision reload failed; keeping last good guard.py", exc_info=True)
            return
        guard = fresh
        logger.info("branch-pin-guard: reloaded guard.py")


def _with_decisions(fn):
    @_wraps(fn)
    def call(*args, **kwargs):
        # Keep enabled/classify/message on one module version across threads.
        with _decision_lock:
            _refresh_decisions()
            return fn(*args, **kwargs)
    return call


SHELL_TOOLS = {"terminal", "process_manage", "process"}
PY_TOOLS = {"execute_code"}


def _cwd(args: dict) -> Optional[str]:
    return args.get("workdir") or os.environ.get("TERMINAL_CWD") or None


def classify(tool_name: str, args: Any) -> Optional[str]:
    args = args if isinstance(args, dict) else {}
    if tool_name in SHELL_TOOLS:
        cmd = " ; ".join(str(args.get(k) or "") for k in ("command", "input", "data"))
        return guard.check_shell(cmd, _cwd(args))
    if tool_name in PY_TOOLS:
        return guard.check_python(str(args.get("code") or ""), _cwd(args))
    if tool_name in {"tool_call", "mcp__tool_call"}:
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                why = classify(str(call.get("name") or ""), call.get("arguments"))
                if why:
                    return why
    return None


@_with_decisions
def _pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict]:
    try:
        if not guard.enabled():
            return None
        why = classify(tool_name, args)
    except Exception:  # never let this plugin break a tool call
        logger.debug("branch-pin-guard classify failed", exc_info=True)
        return None
    if not why:
        return None
    logger.info("branch-pin-guard blocked %s: %s", tool_name, why)
    return {"action": "block", "message": guard.message(why)}


def _mentions_switch(args: Any) -> bool:
    text = " ".join(str(v) for v in (args or {}).values()) if isinstance(args, dict) else str(args or "")
    return "checkout" in text or "switch" in text


def _transform_tool_result(tool_name: str = "", args: Any = None, result: Any = None, **_: Any):
    try:
        if (not guard.enabled() or tool_name not in SHELL_TOOLS | PY_TOOLS or not isinstance(result, str)
                or "[branch-pin-guard]" in result or not _mentions_switch(args)):
            return None
        drift = guard.off_pin()
        return result + guard.drift_note(drift) if drift else None
    except Exception:
        logger.debug("branch-pin-guard drift check failed", exc_info=True)
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("transform_tool_result", _transform_tool_result)
