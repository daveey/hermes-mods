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

try:
    from . import guard
except ImportError:  # loaded without a parent package (tests, hyphenated dir)
    import importlib.util
    from pathlib import Path

    _spec = importlib.util.spec_from_file_location("live_checkout_guard_guard", Path(__file__).with_name("guard.py"))
    guard = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(guard)

logger = logging.getLogger(__name__)

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


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
