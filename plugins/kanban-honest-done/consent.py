"""Gate H: a consent stop on a card survives delegation.

Why (defect kanban-6cf8e9e3fd, 2026-11-12): a chat's comment on another gateway's card hit an approval prompt that
timed out ("The user has NOT consented ... do NOT attempt the same outcome via a different path. Silence is not
consent."). The chat then ordered a worker card to post the same comment; the delegated request carried only
action/target/text, not the stop, and the worker filed it. The stop text was guidance the next hop never saw.

Record (``post_tool_call``): when a tool result IS an approval non-consent (its ``error``/``message`` field, or the
whole result, starts with ``BLOCKED:`` and says the user has NOT consented / silence is not consent, or carries
``user_consent: false``), the card ids named in the blocked call's arguments are stored as a consent stop for that
session. Technical refusals (``stale_run``, ``foreign_card``, guard refusals, a notifier that failed to send) never
record: they are not a human veto. Quoted stop text in ordinary output (grep of approval.py) does not record either.

Enforce (``pre_tool_call``): while a stop is active, a WRITE that names a stopped card is refused: kanban_create,
kanban_comment, kanban_supervisor_request (operator ops), cross_task/cross_session sends, delegate_task, and
``hermes kanban``/``hermes-hub`` write commands in a shell or execute_code. A worker is also bound by the stops of the
chat its card came from (``tasks.session_id``, or the ``origin @session:`` its body names), on this host.

Clear (``pre_llm_call``): the next real user message in that session (the human is back in the loop). Stops expire
after 24 h. Every record / refusal / clear is a JSON line in ``$HERMES_HOME/logs/kanban-honest-done.jsonl``
(agent-owned telemetry; nothing goes to the Inbox).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Optional

TASK_ID_RE = re.compile(r"\bt_[0-9a-f]{8}(?:[0-9a-f]{8})?\b")
CONSENT_RE = re.compile(r"has NOT consented|Silence is not consent|User denied", re.I)
TTL_S = 24 * 3600
MAX_PER_SESSION = 20

WRITE_TOOLS = {"kanban_create", "kanban_comment", "kanban_supervisor_request", "kanban_edit", "kanban_unblock"}
SHELL_TOOLS = {"terminal", "process_manage", "process", "run_in_user_terminal"}
READ_ACTIONS = {"list", "read", "show", "get", "status", "display", "poll", "log", "wait"}
_SHELL_WRITE_RE = re.compile(
    r"\b(?:kanban|hermes-hub)\b[^\n;|&]{0,80}?\b(?:comment|create|create-task|edit|unblock|move|supervisor\w*|"
    r"operator\w*|notify|send)\b", re.I)


def _home(env=None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("HERMES_HOME") or Path.home() / ".hermes")


def store_path(env=None) -> Path:
    return _home(env) / "cache" / "kanban-honest-done" / "consent-stops.json"


def log_path(env=None) -> Path:
    return _home(env) / "logs" / "kanban-honest-done.jsonl"


def telemetry(event: str, env=None, **fields: Any) -> None:
    try:
        p = log_path(env)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), "event": event, **fields}, sort_keys=True) + "\n")
    except OSError:
        pass


def _load(env=None) -> dict:
    try:
        data = json.loads(store_path(env).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict, env=None) -> None:
    p = store_path(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".consent-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, sort_keys=True)
    os.replace(tmp, p)


def _live(stops: Iterable[dict], now: float) -> list[dict]:
    return [s for s in stops if isinstance(s, dict) and now - float(s.get("at") or 0) < TTL_S]


def active_stops(session_id: str, env=None, now: Optional[float] = None) -> list[dict]:
    if not session_id:
        return []
    return _live(_load(env).get(session_id) or [], time.time() if now is None else now)


# --- record -----------------------------------------------------------------------------------------------------

def _consent_text(s: Any) -> bool:
    return isinstance(s, str) and s.lstrip().startswith("BLOCKED:") and bool(CONSENT_RE.search(s))


def is_consent_denial(result: Any) -> Optional[str]:
    """The non-consent message when ``result`` is an approval refusal, else None. Only the result's own status
    fields count, never arbitrary output text that happens to quote one."""
    obj = result
    if isinstance(result, str):
        if _consent_text(result):
            return result
        try:
            obj = json.loads(result)
        except ValueError:
            return None
    if not isinstance(obj, dict):
        return None
    for key in ("error", "message", "result", "reason"):
        if _consent_text(obj.get(key)):
            return obj[key]
    if obj.get("user_consent") is False and obj.get("approved") is False:
        return str(obj.get("message") or "user did not consent")
    return None


def _targets(args: Any, exclude: Iterable[str] = ()) -> list[str]:
    try:
        text = json.dumps(args, default=str)
    except (TypeError, ValueError):
        text = str(args)
    skip = set(exclude)
    return [t for t in dict.fromkeys(TASK_ID_RE.findall(text)) if t not in skip]


def record(tool_name: str, args: Any, result: Any, session_id: str, env=None,
           now: Optional[float] = None) -> Optional[dict]:
    """Store a consent stop when ``result`` is an approval non-consent for a call that names cards."""
    env = os.environ if env is None else env
    msg = is_consent_denial(result)
    if not msg or not session_id:
        return None
    own = env.get("HERMES_KANBAN_TASK") or env.get("HERMES_KANBAN_WORKER_TASK") or ""
    targets = _targets(args, exclude=[own] if own else [])
    if not targets:  # nothing card-scoped to carry: the stop text in the result already governs this chat
        return None
    now = time.time() if now is None else now
    stop = {"targets": targets, "tool": str(tool_name or ""), "at": now,
            "outcome": "timeout" if re.search(r"timed out|Silence", msg, re.I) else "denied"}
    data = _load(env)
    data[session_id] = (_live(data.get(session_id) or [], now) + [stop])[-MAX_PER_SESSION:]
    for sid in list(data):  # prune expired sessions
        if not _live(data[sid] or [], now):
            data.pop(sid, None)
    _save(data, env)
    telemetry("consent_stop_recorded", env, session=session_id, targets=targets, tool=stop["tool"],
              outcome=stop["outcome"])
    return stop


def clear(session_id: str, env=None) -> bool:
    data = _load(env)
    if session_id and data.pop(session_id, None):
        _save(data, env)
        telemetry("consent_stop_cleared", env, session=session_id, why="user_reply")
        return True
    return False


# --- enforce ----------------------------------------------------------------------------------------------------

def _strip(name: str) -> str:
    return name[len("mcp__"):] if name.startswith("mcp__") else name


def write_texts(tool_name: str, args: Any) -> list[str]:
    """Text of a delegating / writing call (empty for reads)."""
    args = args if isinstance(args, dict) else {}
    name = _strip(str(tool_name or ""))
    if name == "tool_call":
        out: list[str] = []
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                out += write_texts(str(call.get("name") or ""), call.get("arguments"))
        return out
    if name in WRITE_TOOLS:
        return [json.dumps(args, default=str)]
    if name in ("cross_task", "cross_session"):
        return [] if str(args.get("action") or "").lower() in READ_ACTIONS else [json.dumps(args, default=str)]
    if name == "delegate_task":
        return [] if str(args.get("action") or "spawn").lower() in ("list", "stop") else [json.dumps(args, default=str)]
    if name in SHELL_TOOLS:
        cmd = " ; ".join(str(args.get(k) or "") for k in ("command", "input", "data"))
        return [cmd] if _SHELL_WRITE_RE.search(cmd) else []
    if name == "execute_code":
        code = str(args.get("code") or "")
        return [code] if _SHELL_WRITE_RE.search(code) or "kanban_" in code else []
    return []


def check_relay(tool_name: str, args: Any, sessions: Iterable[str], env=None,
                now: Optional[float] = None) -> Optional[str]:
    """Refusal for a write naming a card under an active consent stop of any of ``sessions``, or None."""
    sessions = [s for s in dict.fromkeys(sessions) if s]
    if not sessions:
        return None
    texts = write_texts(tool_name, args)
    if not texts:
        return None
    named = set(TASK_ID_RE.findall("\n".join(texts)))
    if not named:
        return None
    for sid in sessions:
        for stop in active_stops(sid, env, now):
            hit = [t for t in stop.get("targets") or [] if t in named]
            if hit:
                telemetry("consent_relay_refused", env, session=sid, targets=hit, tool=_strip(str(tool_name)))
                return relay_refusal(sid, hit, stop)
    return None


def relay_refusal(session_id: str, targets: list[str], stop: dict) -> str:
    when = time.strftime("%H:%M:%SZ", time.gmtime(float(stop.get("at") or 0)))
    how = "timed out (silence is not consent)" if stop.get("outcome") == "timeout" else "was refused"
    return (f"[kanban-honest-done] Refused: the user's approval for an action on {', '.join(targets)} {how} at {when} "
            f"in chat {session_id}. Delegating it (a card, a comment, an operator request, a subagent or another "
            "chat) is the same action by another route, so it stays stopped until the user answers in that chat. "
            "Ask them (end the turn with the question, or kanban_block needs_input with the exact ask); do not "
            "report the action as done or pending.")
