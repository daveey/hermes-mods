"""stash-worktree-guard: ``refs/stash`` is shared by every worktree of a repo.

- ``pre_tool_call`` blocks ``git stash pop`` / ``apply`` when the repo has more than one worktree
  and the stash's branch is not the current branch (terminal, process, execute_code, nested tool_call).
- ``transform_terminal_output`` appends a warning after any ``git stash`` in such a repo, including
  ``git stash list``, so the agent sees the shared-ref rule on the command that just ran.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

try:
    from . import guard
except ImportError:  # loaded without a parent package (tests, hyphenated dir)
    import importlib.util
    from pathlib import Path

    _spec = importlib.util.spec_from_file_location(
        "stash_worktree_guard_guard", Path(__file__).with_name("guard.py"))
    assert _spec is not None and _spec.loader is not None
    guard = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(guard)

logger = logging.getLogger(__name__)

SHELL_TOOLS = {"terminal", "process_manage", "process"}
PY_TOOLS = {"execute_code"}


def _shell_text(args: dict) -> str:
    return " ; ".join(str(args.get(k) or "") for k in ("command", "input", "data"))


def _base_cwd(args: Any, task_id: str = "") -> Optional[str]:
    if isinstance(args, dict) and args.get("workdir"):
        return str(args["workdir"])
    try:
        from tools.terminal_tool import get_session_cwd
        recorded = get_session_cwd(task_id or None)
        if recorded:
            return recorded
    except Exception:
        logger.debug("stash-worktree-guard could not read the terminal session cwd", exc_info=True)
    return os.environ.get("TERMINAL_CWD") or None


def classify(tool_name: str, args: Any, task_id: str = "") -> Optional[str]:
    args = args if isinstance(args, dict) else {}
    cwd = _base_cwd(args, task_id)
    if tool_name in SHELL_TOOLS:
        return guard.refuse_reason(_shell_text(args), cwd)
    if tool_name in PY_TOOLS:
        return guard.check_python(str(args.get("code") or ""), cwd)
    if tool_name in {"tool_call", "mcp__tool_call"}:
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                why = classify(str(call.get("name") or ""), call.get("arguments"), task_id)
                if why:
                    return why
    return None


def _pre_tool_call(tool_name: str = "", args: Any = None, task_id: str = "", **_: Any) -> Optional[dict]:
    try:
        why = classify(tool_name, args, task_id)
    except Exception:
        logger.debug("stash-worktree-guard classify failed", exc_info=True)
        return None
    if not why:
        return None
    logger.info("stash-worktree-guard blocked %s", tool_name)
    return {"action": "block", "message": why}


def transform_terminal_output(command: str = "", output: str = "", task_id: str = "", **_: Any) -> Optional[str]:
    try:
        if not isinstance(output, str) or guard.MARK in output:
            return None
        note = guard.warning_for(command, _base_cwd(None, task_id))
        if not note:
            return None
        return output.rstrip() + "\n\n" + note
    except Exception:
        logger.debug("stash-worktree-guard warning failed", exc_info=True)
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("transform_terminal_output", transform_terminal_output)
