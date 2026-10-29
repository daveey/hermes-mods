"""mac-heavy-guard: keep heavy builds, whole test suites and whole-repo installs off the laptop.

A ``pre_tool_call`` hook blocks terminal / process calls whose command is heavy work (see
guard.py) and points the agent at the hub / worker-1, a narrower command, or the
``MAC_HEAVY_OK=1`` opt-out. Acts only on macOS. ``claude_hook.py`` applies the same rule to
Claude Code's Bash tool.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

try:
    from . import guard
except ImportError:  # loaded without a parent package (tests, hyphenated dir)
    import importlib.util
    from pathlib import Path

    _spec = importlib.util.spec_from_file_location("mac_heavy_guard_guard", Path(__file__).with_name("guard.py"))
    guard = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(guard)

logger = logging.getLogger(__name__)

SHELL_TOOLS = {"terminal", "process_manage", "process"}


def classify(tool_name: str, args: Any) -> Optional[str]:
    args = args if isinstance(args, dict) else {}
    if tool_name in SHELL_TOOLS:
        return guard.check_shell(str(args.get("command") or ""), cwd=args.get("workdir") or None)
    if tool_name in {"tool_call", "mcp__tool_call"}:
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                why = classify(str(call.get("name") or ""), call.get("arguments"))
                if why:
                    return why
    return None


def _pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict]:
    try:
        if not guard.enabled():
            return None
        why = classify(tool_name, args)
    except Exception:  # never let this plugin break a tool call
        logger.debug("mac-heavy-guard classify failed", exc_info=True)
        return None
    if not why:
        return None
    logger.info("mac-heavy-guard blocked %s: %s", tool_name, why)
    return {"action": "block", "message": guard.message(why)}


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
