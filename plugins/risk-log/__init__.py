"""risk-log: count high-risk Hermes tool use (log and reduce; it never blocks or changes a call).

A ``post_tool_call`` hook enqueues each call's name, args and ids plus its origin (interactive / cron / kanban /
gateway:<platform> / api_server); a background thread classifies it (classify.py) and appends metadata-only rows to
a 0600 SQLite file (store.py). The hook itself does no I/O and swallows every error.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any

try:
    from . import store
except ImportError:  # loaded without a parent package (tests, hyphenated dir)
    import importlib.util
    import sys
    from pathlib import Path

    _key = "risk_log_store"
    if _key in sys.modules:
        store = sys.modules[_key]
    else:
        _spec = importlib.util.spec_from_file_location(_key, Path(__file__).with_name("store.py"))
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        store = sys.modules.setdefault(_key, _mod)

logger = logging.getLogger(__name__)

# Cheap prefilter: tools that can ever produce a finding. Everything else returns before any work.
_ALWAYS = {"terminal", "process", "process_manage", "run_in_user_terminal", "execute_code", "read_file",
           "search_files", "send_message", "tool_call", "mcp__terminal", "mcp__process_manage", "mcp__execute_code",
           "mcp__read_file", "mcp__search_files", "mcp__tool_call"}
_NAME_HINTS = ("chrome", "devtools", "whatsapp", "slack", "discord", "signal", "imessage", "telegram", "sms",
               "autoresearch")
_UNATTENDED = {"api_server", "webhook", "msgraph_webhook"}
_LOCAL = {"", "cli", "tui", "desktop", "local", "codex", "tool", "gateway", "kanban"}
_HOST = socket.gethostname().split(".")[0]

WRITER = store.Writer()


_GETTER: list = []  # resolved once: a failed import per call would cost milliseconds


def _session_env(name: str) -> str:
    if not _GETTER:
        try:
            from gateway.session_context import get_session_env
            _GETTER.append(get_session_env)
        except Exception:  # noqa: BLE001 - outside the Hermes runtime
            _GETTER.append(None)
    getter = _GETTER[0]
    try:
        value = getter(name, "") if getter else ""
    except Exception:  # noqa: BLE001
        value = ""
    return str(value or os.environ.get(name, "") or "")


def origin() -> tuple[str, str]:
    """(origin, platform). Cron beats kanban beats platform, as in Hermes' approval context."""
    platform = (os.environ.get("HERMES_PLATFORM") or _session_env("HERMES_SESSION_PLATFORM")).strip().lower()
    source = _session_env("HERMES_SESSION_SOURCE").strip().lower()
    if _session_env("HERMES_CRON_SESSION").strip().lower() in ("1", "true", "yes", "on"):
        return "cron", platform or source
    if os.environ.get("HERMES_KANBAN_TASK"):
        return "kanban", platform or source
    if platform in _UNATTENDED:
        return platform, platform
    if platform and platform not in _LOCAL:
        return "gateway:" + platform, platform
    return "interactive", platform or source or "cli"


def _profile() -> str:
    explicit = _session_env("HERMES_SESSION_PROFILE")
    if explicit:
        return explicit
    home = os.environ.get("HERMES_HOME", "")
    parts = home.rstrip("/").split("/")
    return parts[-1] if len(parts) > 1 and parts[-2] == "profiles" else "default"


def relevant(tool_name: str) -> bool:
    if tool_name in _ALWAYS:
        return True
    lowered = tool_name.lower()
    return any(h in lowered for h in _NAME_HINTS)


def _post_tool_call(tool_name: str = "", args: Any = None, session_id: str = "", tool_call_id: str = "",
                    task_id: str = "", status: Any = None, duration_ms: Any = 0, **_: Any) -> None:
    try:
        name = str(tool_name or "")
        if not relevant(name):
            return None
        where, platform = origin()
        WRITER.submit({
            "at": time.time(), "host": _HOST, "tool": name, "args": dict(args) if isinstance(args, dict) else {},
            "call_id": tool_call_id or None, "session_id": session_id or _session_env("HERMES_SESSION_ID") or None,
            "task_id": os.environ.get("HERMES_KANBAN_TASK") or None, "origin": where, "platform": platform or None,
            "profile": _profile(), "status": str(status) if status else None,
            "duration_ms": int(duration_ms) if isinstance(duration_ms, (int, float)) else None,
        })
    except Exception:  # noqa: BLE001 - logging must never change a tool call
        logger.debug("risk-log enqueue failed", exc_info=True)
    return None


def register(ctx) -> None:
    _session_env("HERMES_SESSION_ID")  # resolve the session-context getter now, not on the first tool call
    ctx.register_hook("post_tool_call", _post_tool_call)
