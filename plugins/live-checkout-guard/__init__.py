"""live-checkout-guard: refuse in-place edits of the live (synced) hermes-mods checkout.

``pre_tool_call`` blocks patch/write_file on paths that resolve (through symlinks) into the protected root,
terminal / execute_code commands that would commit, stash, rebase, ... or write files there, and shows how to do
the work in a detached git worktree. See README.md. ``claude_hook.py`` = the Claude Code version.
"""

from __future__ import annotations

import logging
import os
import sys
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
            logger.warning("live-checkout-guard: decision reload failed; keeping last good guard.py", exc_info=True)
            return
        guard = fresh
        logger.info("live-checkout-guard: reloaded guard.py")


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
FILE_TOOLS = {"write_file", "patch"}


def _session_cwd(task_id: str, shell: bool) -> Optional[str]:
    """The cwd the core tool will actually use: ``tools.terminal_tool.get_session_cwd`` (written after every
    terminal command, so it follows ``cd`` across calls). The terminal keys it by the approval session key, else
    task_id; file tools by task_id. Read from the already-loaded core module only: if the terminal tool was never
    imported in this process no record can exist, and the hook must not pull in the core."""
    mod = sys.modules.get("tools.terminal_tool")
    getter = getattr(mod, "get_session_cwd", None)
    if not callable(getter):
        return None
    keys = []
    if shell:
        ctx = sys.modules.get("tools.approval_context") or sys.modules.get("tools.approval")
        try:
            ctx_key = ctx.get_current_session_key(default="") if ctx else ""
        except Exception:
            ctx_key = ""
        if ctx_key:
            keys.append(ctx_key)
    keys.append(task_id or "")
    for k in keys:
        try:
            cwd = getter(k or None)  # None/"" reads the "default" record, like the terminal does
        except Exception:
            cwd = None
        if isinstance(cwd, str) and cwd.strip():
            return cwd
    return None


def _cwd(args: dict, task_id: str = "", shell: bool = True) -> Optional[str]:
    """workdir > the session's recorded terminal cwd > $TERMINAL_CWD > process cwd (guard default)."""
    return args.get("workdir") or _session_cwd(task_id, shell) or os.environ.get("TERMINAL_CWD") or None


def classify(tool_name: str, args: Any, task_id: str = "") -> Optional[str]:
    args = args if isinstance(args, dict) else {}
    tool = tool_name[5:] if tool_name.startswith("mcp__") else tool_name
    if tool in FILE_TOOLS:
        return guard.check_file_tool(tool, args, _cwd(args, task_id, shell=False))
    if tool in SHELL_TOOLS:
        cmd = " ; ".join(str(args.get(k) or "") for k in ("command", "input", "data"))
        if tool == "terminal" and (why := guard.check_terminal_env(str(args.get("command") or ""))):
            return why  # only the terminal tool carries exports into later calls
        return guard.check_shell(cmd, _cwd(args, task_id))
    if tool in PY_TOOLS:
        return guard.check_python(str(args.get("code") or ""), _cwd(args, task_id))
    if tool == "tool_call":
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                why = classify(str(call.get("name") or ""), call.get("arguments"), task_id)
                if why:
                    return why
    return None


@_with_decisions
def _pre_tool_call(tool_name: str = "", args: Any = None, task_id: str = "", **_: Any) -> Optional[dict]:
    try:
        if not guard.enabled():
            return None
        why = classify(tool_name, args, str(task_id or ""))
    except Exception:  # never let this plugin break a tool call
        logger.debug("live-checkout-guard classify failed", exc_info=True)
        return None
    if not why:
        return None
    logger.info("live-checkout-guard blocked %s: %s", tool_name, why)
    return {"action": "block", "message": guard.message(why)}


def _post_tool_call(tool_name: str = "", args: Any = None, result: Any = None,
                    task_id: str = "", session_id: str = "", status: str = "", **_: Any) -> None:
    """Observe successful skill writes only; never change the tool's result."""
    if tool_name.removeprefix("mcp__") != "skill_manage" or status not in {"", "ok", "success"}:
        return
    try:
        if not guard.enabled():
            return
        path = _Path(__file__).with_name("skill_publish.py")
        loader = _DecisionSourceLoader(__name__ + ".skill_publish", str(path))
        spec = _import_util.spec_from_file_location(loader.name, path, loader=loader)
        assert spec is not None and spec.loader is not None
        module = _import_util.module_from_spec(spec)
        loader.exec_module(module)
        root = _Path(guard.root())
        paths = module.written_paths(args if isinstance(args, dict) else {}, result, root)
        if not paths:
            return
        outcome = module.publish(root, paths, session_id=session_id, task_id=task_id)
        logger.info("live-checkout-guard: skill publication %s", outcome)
    except Exception as exc:
        # One record per call, including CI skips; never retry from the observer.
        logger.warning("live-checkout-guard: skill publication preserved edits: %s", exc)


# 45s shared publication deadline + 5s disposable-worktree cleanup + two
# 2s process-group reap allowances, with headroom before core abandons the hook.
_post_tool_call.hermes_hook_timeout = 60


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
