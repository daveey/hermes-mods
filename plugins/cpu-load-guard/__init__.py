"""cpu-load-guard: refuse host-saturating load generators on protected hosts.

A ``pre_tool_call`` hook inspects terminal (foreground and background), process writes,
execute_code, write_file/patch of script files, and local scripts a command runs (size- and depth-capped) on hosts listed in ``protected_hosts`` of ``~/.hermes/cpu-load-guard.yaml``
(default: the user's laptop; ``CPU_LOAD_GUARD_HOSTS=a,b`` overrides; ``CPU_LOAD_GUARD=off`` disables).

Limits: regex heuristics. Obfuscated burners, scripts fetched or generated at run time, scripts over
256 KB or nested more than two levels, and compiled programs are not inspected.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

try:
    from . import guard
except ImportError:  # loaded without a parent package (tests, hyphenated dir)
    import importlib.util
    from pathlib import Path

    _spec = importlib.util.spec_from_file_location("cpu_load_guard_guard", Path(__file__).with_name("guard.py"))
    guard = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(guard)

logger = logging.getLogger(__name__)

SHELL_TOOLS = {"terminal", "process_manage", "process"}
PY_TOOLS = {"execute_code"}
WRITE_TOOLS = {"write_file"}
PATCH_TOOLS = {"patch"}
_OWN_DIR = Path(__file__).resolve().parent
_V4A_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update) File:\s*(.+)$", re.M)


def _own_file(path: str) -> bool:
    # Editing this plugin (its tests quote the burners) must stay possible on a protected host.
    try:
        return _OWN_DIR in Path(os.path.expanduser(path)).resolve().parents
    except Exception:
        return False


def _check_patch(args: dict) -> Optional[str]:
    if args.get("mode") == "patch" or args.get("patch"):
        text = str(args.get("patch") or "")
        added = "\n".join(line[1:] for line in text.splitlines() if line.startswith("+"))
        paths = [p.strip() for p in _V4A_FILE_RE.findall(text)] or ["patch.sh"]
        for p in paths:
            if not _own_file(p):
                why = guard.check_write(p, added)
                if why:
                    return why
        return None
    path = str(args.get("path") or "")
    if _own_file(path):
        return None
    new = str(args.get("new_string") or "")
    content = new
    try:  # judge the file as it will be after the edit, not only the inserted lines
        cur = Path(os.path.expanduser(path)).read_text(errors="replace")
        old = str(args.get("old_string") or "")
        if old and old in cur:
            content = cur.replace(old, new) if args.get("replace_all") else cur.replace(old, new, 1)
    except Exception:
        pass
    return guard.check_write(path or "patch.sh", content)


def classify(tool_name: str, args: Any) -> Optional[str]:
    args = args if isinstance(args, dict) else {}
    name = tool_name[5:] if tool_name.startswith("mcp__") else tool_name
    if name in SHELL_TOOLS:
        cwd = args.get("workdir") or args.get("cwd") or None
        text = " ".join(str(args.get(k) or "") for k in ("command", "input", "data"))
        return guard.check_shell(text, str(cwd) if cwd else None)
    if name in PY_TOOLS:
        return guard.check_python(str(args.get("code") or ""))
    if name in WRITE_TOOLS:
        path = str(args.get("path") or "")
        return None if _own_file(path) else guard.check_write(path, str(args.get("content") or ""))
    if name in PATCH_TOOLS:
        return _check_patch(args)
    if name == "tool_call":
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
        logger.debug("cpu-load-guard classify failed", exc_info=True)
        return None
    if not why:
        return None
    logger.info("cpu-load-guard blocked %s: %s", tool_name, why)
    return {"action": "block", "message": guard.message(why)}


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
