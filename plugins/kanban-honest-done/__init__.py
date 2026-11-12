"""kanban-honest-done: a Kanban worker can't fake "done".

Inside a dispatcher-spawned worker (``HERMES_KANBAN_TASK`` set), a ``pre_tool_call`` hook refuses:

A. editing its OWN card's title/body (``hermes kanban edit <own id> --title/--body`` from terminal, process or
   execute_code) — narrowing your own scope is how a goal judge gets talked into accepting unfinished work;
B. completing its own card (``kanban_complete`` tool, or ``hermes kanban complete`` from a shell) with a handoff
   (summary and every metadata text value) that admits dropped / deferred / unrun / "not verified" work,
   unless it names a follow-up card
   (``t_xxxxxxxx``) that exists, is open (or already done), and was created by or is linked to this card.
D. completing its own card while the body/comments name an origin chat (``origin ... @session:X``) that is not
   linked to the card (chat link, notify subscription, or ``follow-through: SENT`` comment), so it never hears.
F. (any session) ``hermes kanban edit <id> --body`` that adds acceptance-waiver text or drops an acceptance sentence,
   unless the new body cites ``user message <id>`` that David typed in THIS chat (state.db, compression lineage).
   Authority quoted from another chat, a card or a notice is refused (acceptance.py; t_7e2ee45c 2026-11-10).
G. completing (or ``kanban_request_review``-ing) its own card with a handoff that turns a failed acceptance
   threshold into a waiver or a guessed pass: structured ``metadata.acceptance_results`` (a pass without or against
   its observed value, a fail without a preregistered ``IF FAIL <check>:`` branch, waived without a body waiver) or
   prose ("check 4 failed; waived", "treated as a pass"). thresholds.py (red-sweep acceptance-waiver-owner-decision).
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

try:
    from . import consent as consent_mod
except ImportError:  # same fallback as core
    consent_mod = core._load_sibling("consent")

logger = logging.getLogger(__name__)

SHELL_TOOLS = {"terminal", "process_manage", "process", "run_in_user_terminal"}
PY_TOOLS = {"execute_code"}
COMPLETE_TOOLS = {"kanban_complete"}
REVIEW_TOOLS = {"kanban_request_review"}


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
    if name in REVIEW_TOOLS:
        target = str(args.get("task_id") or own)
        if target != own:
            return None
        return core.check_review(own, str(args.get("summary") or ""), paths_fn(), metadata=args.get("metadata"))
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


def classify_acceptance(tool_name: str, args: Any, session_id: str, paths_fn=None, env=None) -> Optional[str]:
    """F: acceptance-changing body edits need a user message id from this chat. Any session."""
    paths_fn = paths_fn or core.board_paths
    args = args if isinstance(args, dict) else {}
    name = _strip(str(tool_name or ""))
    if name in SHELL_TOOLS:
        cmd = " ; ".join(str(args.get(k) or "") for k in ("command", "input", "data"))
        if "kanban" in cmd and "--body" in cmd:
            return core.check_shell_acceptance(cmd, session_id, paths_fn, env)
    if name in PY_TOOLS:
        code = str(args.get("code") or "")
        if "kanban" in code and "--body" in code:
            return core.check_python_acceptance(code, session_id, paths_fn, env)
    if name == "tool_call":
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                why = classify_acceptance(str(call.get("name") or ""), call.get("arguments"), session_id, paths_fn, env)
                if why:
                    return why
    return None


def _origin_chats(own: str, paths_fn=None) -> list[str]:
    """Chats a worker card came from: the creating session (``tasks.session_id``) and any ``origin @session:`` its
    body names. Their consent stops bind the worker too (H)."""
    out: list[str] = []
    for p in (paths_fn or core.board_paths)():
        try:
            conn = core.ro_connect(p)
        except Exception:
            continue
        try:
            row = conn.execute("SELECT session_id, body FROM tasks WHERE id=?", (own,)).fetchone()
        except Exception:
            row = None
        finally:
            conn.close()
        if row:
            out += [str(row[0])] if row[0] else []
            out += core.origin_sessions(row[1] or "")
            break
    return list(dict.fromkeys(out))


def classify_consent(tool_name: str, args: Any, session_id: str, own: Optional[str] = None) -> Optional[str]:
    """H: a write naming a card under this chat's (or, for a worker, its origin chat's) consent stop."""
    if not consent_mod.write_texts(tool_name, args):
        return None
    sessions = [session_id] + (_origin_chats(own) if own else [])
    return consent_mod.check_relay(tool_name, args, sessions)


def _pre_tool_call(tool_name: str = "", args: Any = None, session_id: str = "", **_: Any) -> Optional[dict]:
    own = None
    try:
        worker = _in_worker()
        own = core.own_task_id() if worker else None
        why = (classify_consent(tool_name, args, session_id or "", own)
               or classify_create(tool_name, args) or classify_acceptance(tool_name, args, session_id or ""))
        if not why and own:
            why = classify(tool_name, args, own)
    except Exception:  # never let this plugin break a tool call
        logger.debug("kanban-honest-done classify failed", exc_info=True)
        return None
    if not why:
        return None
    logger.info("kanban-honest-done refused %s for %s", tool_name, own)
    return {"action": "block", "message": why}


def _post_tool_call(tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "",
                    **_: Any) -> None:
    """H: remember an approval non-consent for the cards the blocked call named."""
    try:
        consent_mod.record(tool_name, args, result, session_id or "")
    except Exception:
        logger.debug("kanban-honest-done consent record failed", exc_info=True)


def _pre_llm_call(session_id: str = "", canonical_user_content: Any = None, **_: Any) -> None:
    """H: a real user message (not a notice turn) clears that chat's consent stops."""
    try:
        if isinstance(canonical_user_content, dict) and session_id:
            consent_mod.clear(session_id)
    except Exception:
        logger.debug("kanban-honest-done consent clear failed", exc_info=True)
    return None


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
    ctx.register_hook("post_tool_call", _post_tool_call)
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    _register_done_check()
