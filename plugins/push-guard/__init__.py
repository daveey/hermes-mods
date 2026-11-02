"""push-guard: refuse direct `git push` of code to a guarded branch; point at the repo's landing path.

``pre_tool_call`` checks terminal / process / execute_code (and nested ``tool_call``) for `git push` whose destination
is a guarded branch (``$HERMES_HOME/push-guards.json``, see guard.py). A merge-train guard blocks it unless every
changed file is in an allowed path (docs); a ``require_check`` guard blocks it unless the pushed sha's named CI
check-run is ``success``, and holds every `gh pr merge` to the same rule (admin credentials can bypass
branch protection without --admin). ``PUSH_GUARD=off`` in the command skips this local hook, not server protection.
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
            logger.warning("push-guard: decision reload failed; keeping last good guard.py", exc_info=True)
            return
        guard = fresh
        logger.info("push-guard: reloaded guard.py")


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
        why = classify(tool_name, args)
    except Exception:  # never let this plugin break a tool call
        logger.debug("push-guard classify failed", exc_info=True)
        return None
    if not why:
        return None
    logger.info("push-guard blocked %s: %s", tool_name, why.split("||")[0])
    return {"action": "block", "message": guard.message(why)}


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
