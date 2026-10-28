"""kanban-honest-done: a Kanban worker can't fake "done".

Inside a dispatcher-spawned worker (``HERMES_KANBAN_TASK`` set), a ``pre_tool_call`` hook refuses:

A. editing its OWN card's title/body (``hermes kanban edit <own id> --title/--body`` from terminal, process or
   execute_code) — narrowing your own scope is how a goal judge gets talked into accepting unfinished work;
B. completing its own card (``kanban_complete`` tool, or ``hermes kanban complete`` from a shell) with a handoff
   (summary, every metadata text value, or title) that admits dropped / deferred / unrun / "not verified" work,
   unless it names a follow-up card
   (``t_xxxxxxxx``) that exists, is open (or already done), and was created by or is linked to this card.
D. completing its own card while the body/comments name an origin chat (``origin ... @session:X``) that is not
   linked to the card (chat link, notify subscription, or ``follow-through: SENT`` comment), so it never hears.
E. (any session, not only workers) ``kanban_create`` whose title/body names an origin chat on ANOTHER gateway
   (``origin ... @session:<profile>@<gw>/<id>``) without ``origin_session`` binding it: the chat would never see it.

The refusal says what to do instead.

C. In the Desktop backend it registers ``needs_attention`` with core's ``kanban_session_activity.register_done_check``
   (fork core): a session whose recent done card admits dropped work with no follow-up card paints amber, not green.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

try:
    from . import core
except ImportError:  # loaded without a parent package (pytest, hyphenated dir)
    import importlib.util
    import sys
    from pathlib import Path

    _key = "kanban_honest_done_core"
    core = sys.modules.get(_key)
    if core is None:
        _spec = importlib.util.spec_from_file_location(_key, Path(__file__).with_name("core.py"))
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        core = sys.modules.setdefault(_key, _mod)

logger = logging.getLogger(__name__)

SHELL_TOOLS = {"terminal", "process_manage", "process", "run_in_user_terminal"}
PY_TOOLS = {"execute_code"}
COMPLETE_TOOLS = {"kanban_complete"}


def _strip(name: str) -> str:
    for prefix in ("mcp__",):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name


def _in_worker(env=None) -> bool:
    env = os.environ if env is None else env
    if not env.get("HERMES_KANBAN_TASK"):
        return False
    if env is not os.environ:
        return True
    try:
        from agent.delegation_context import is_dispatcher_owned_worker_context  # type: ignore

        return bool(is_dispatcher_owned_worker_context())
    except Exception:
        return True


def classify(tool_name: str, args: Any, own: str, paths_fn=None) -> Optional[str]:
    """Refusal message for this call by worker ``own``, or None."""
    paths_fn = paths_fn or core.board_paths
    args = args if isinstance(args, dict) else {}
    name = _strip(str(tool_name or ""))
    if name in COMPLETE_TOOLS:
        target = str(args.get("task_id") or own)
        if target != own:
            return None
        summary = str(args.get("summary") or args.get("result") or "")
        return core.check_complete(own, summary, paths_fn(), metadata=args.get("metadata"))
    if name in SHELL_TOOLS:
        cmd = " ; ".join(str(args.get(k) or "") for k in ("command", "input", "data"))
        return core.check_shell(cmd, own, paths_fn)
    if name in PY_TOOLS:
        return core.check_python(str(args.get("code") or ""), own, paths_fn)
    if name == "tool_call":
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                why = classify(str(call.get("name") or ""), call.get("arguments"), own, paths_fn)
                if why:
                    return why
    return None


CREATE_TOOLS = {"kanban_create"}


def classify_create(tool_name: str, args: Any, names_fn=None, env=None) -> Optional[str]:
    """E: refusal for creating a card that names another gateway's origin chat without binding it. Any session."""
    args = args if isinstance(args, dict) else {}
    name = _strip(str(tool_name or ""))
    if name in CREATE_TOOLS:
        return core.check_create(args, env=env, names_fn=names_fn)
    if name == "tool_call":
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                why = classify_create(str(call.get("name") or ""), call.get("arguments"), names_fn, env)
                if why:
                    return why
    return None


def _pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict]:
    own = None
    try:
        why = classify_create(tool_name, args)
        if not why and _in_worker():
            own = core.own_task_id()
            why = classify(tool_name, args, own) if own else None
    except Exception:  # never let this plugin break a tool call
        logger.debug("kanban-honest-done classify failed", exc_info=True)
        return None
    if not why:
        return None
    logger.info("kanban-honest-done refused %s for %s", tool_name, own)
    return {"action": "block", "message": why}


def _register_done_check() -> bool:
    """C: tell core's sidebar activity which done cards need attention (fork core only; older cores lack it)."""
    try:
        from hermes_cli import kanban_session_activity as ksa  # type: ignore
    except Exception:
        return False
    fn = getattr(ksa, "register_done_check", None)
    if fn is None:
        return False
    fn("kanban-honest-done", core.needs_attention)
    return True


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    _register_done_check()
