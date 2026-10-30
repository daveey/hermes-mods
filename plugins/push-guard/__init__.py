"""push-guard: refuse direct `git push` of code to a guarded branch; point at the repo's landing path.

``pre_tool_call`` checks terminal / process / execute_code (and nested ``tool_call``) for `git push` whose destination
is a guarded branch (``$HERMES_HOME/push-guards.json``, see guard.py). A merge-train guard blocks it unless every
changed file is in an allowed path (docs); a ``require_check`` guard blocks it unless the pushed sha's named CI
check-run is ``success``, and holds `gh pr merge --admin` to the same rule. ``PUSH_GUARD=off`` in the command skips it.
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

    _spec = importlib.util.spec_from_file_location("push_guard_guard", Path(__file__).with_name("guard.py"))
    guard = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(guard)

logger = logging.getLogger(__name__)

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
