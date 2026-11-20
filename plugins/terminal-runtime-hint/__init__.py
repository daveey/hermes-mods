"""Add an actionable interpreter hint to terminal Python dependency failures."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

MARK = "terminal-runtime-hint:"
_MISSING_RUAMEL = re.compile(r"ModuleNotFoundError:\s*No module named ['\"]ruamel(?:\.[^'\"]+)?['\"]")
_PYTHON_TOKEN = re.compile(r"(?:^|[\s;&|()])(?:[./\\w-]*python(?:3(?:\.\d+)?)?)(?=\s|$)")


def _python_command(command: Any) -> bool:
    return isinstance(command, str) and bool(_PYTHON_TOKEN.search(command))


def _hint() -> str:
    return (
        f"{MARK} this Python command used an interpreter without Hermes's managed dependencies. "
        "Re-run the same command with the Python executable replaced by "
        '`$HOME/.hermes/hermes-agent/.venv/bin/python` (or use `hermes --run-module` for a module). '
        "Do not install ruamel into the system Python."
    )


def transform_tool_result(tool_name: str = "", args: Any = None, result: Any = None, **_) -> Optional[str]:
    """Annotate only a real failed Python terminal invocation, once."""
    if tool_name.rsplit("__", 1)[-1] != "terminal" or not isinstance(result, str) or MARK in result:
        return None
    try:
        data = json.loads(result)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("exit_code") == 0:
        return None
    output = data.get("output")
    command = args.get("command") if isinstance(args, dict) else None
    if not isinstance(output, str) or not _MISSING_RUAMEL.search(output) or not _python_command(command):
        return None
    data["hint"] = _hint()
    return json.dumps(data, ensure_ascii=False)



def register(ctx) -> None:
    ctx.register_hook("transform_tool_result", transform_tool_result)

