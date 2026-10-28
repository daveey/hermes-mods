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

try:
    from . import guard
except ImportError:  # loaded without a parent package (tests, hyphenated dir)
    import importlib.util
    from pathlib import Path

    _spec = importlib.util.spec_from_file_location("sqlite_live_guard_guard", Path(__file__).with_name("guard.py"))
    guard = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(guard)

logger = logging.getLogger(__name__)

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
