"""follow-through plugin: deferred "when card X finishes, do Y" deliveries actually happen, once, and are reported
honestly. Logic lives in core.py; this file only wires Hermes hooks."""
from __future__ import annotations

import importlib.util
import logging
import os
import threading
from pathlib import Path
from typing import Any

try:
    from . import core
except ImportError:  # pytest imports the hyphenated plugin dir without a parent package
    _spec = importlib.util.spec_from_file_location("follow_through_core", Path(__file__).with_name("core.py"))
    core = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(core)

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_sends = core.SendTracker()


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _throttle() -> core.Throttle:
    return core.Throttle(_home() / "cache" / "follow-through" / "reminders.json")


def pre_llm_call(session_id: str = "", user_message: Any = None, **_) -> dict | None:
    """Conditional context: unfinished follow-ups of this chat's child cards, and a do-it-now steer when the user
    asks about a missed action. Returns None (no tokens) when neither applies."""
    try:
        parts = []
        home = _home()
        items = core.open_followups(home, home / "state.db", session_id or "")
        if items:
            asked = core.is_missed_action_question(user_message if isinstance(user_message, str) else "")
            due = {t for t in _throttle().due(session_id or "", [i["task_id"] for i in items])} if not asked else \
                {i["task_id"] for i in items}
            items = [i for i in items if i["task_id"] in due]
            if items:
                parts.append(core.render_followups(items))
        if core.is_missed_action_question(user_message if isinstance(user_message, str) else ""):
            parts.append(core.MISSED_ACTION_NOTE)
        return {"context": "\n\n".join(parts)} if parts else None
    except Exception as exc:  # never break a turn
        logger.debug("follow-through pre_llm_call failed: %s", exc)
        return None


def transform_tool_result(tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "", **_):
    if not isinstance(result, str):
        return None
    try:
        if tool_name in ("kanban_create", "kanban_comment"):
            hint = core.obligation_hint(tool_name, args if isinstance(args, dict) else {}, result)
            if hint and "[follow-through]" not in result:
                return result + "\n" + hint
            return None
        with _lock:
            note = _sends.observe(session_id or "", tool_name or "", result)
        return result + note if note else None
    except Exception as exc:
        logger.debug("follow-through transform_tool_result failed: %s", exc)
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("transform_tool_result", transform_tool_result)
