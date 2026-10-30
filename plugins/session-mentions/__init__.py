"""cross_session — list / read / message sessions on any gateway the Desktop is connected to.

The agent never holds other gateways' credentials; the Desktop app does. So the tool writes a
request file, the Desktop plugin (``~/.hermes/desktop-plugins/session-mentions``) drains it over
``GET /api/plugins/session-mentions/outbox``, performs it on the target gateway's own connection,
and posts the result to ``POST /api/plugins/session-mentions/result``. The tool polls for it.
"""
from __future__ import annotations

import json
import os
import time
import uuid

if __package__:
    from . import relay_files as rf
else:  # pytest imports this hyphen-named plugin dir without a parent package
    import importlib.util as _ilu
    from pathlib import Path as _Path

    def _load_sibling(name):
        spec = _ilu.spec_from_file_location(f"_session_mentions_{name}", _Path(__file__).with_name(f"{name}.py"))
        mod = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod

    rf = _load_sibling("relay_files")

TOOLSET = "session_mentions"
PICKUP_TIMEOUT = 30.0  # Desktop drains every ~3s; no claim by then = Desktop not running
MAX_REPLY_WAIT = 600

SCHEMA = {
    "name": "cross_session",
    "description": (
        "List, read, or send a message to Hermes sessions on ANY gateway connected to the user's "
        "Hermes Desktop app — this Mac and remote/Nous-hosted gateways alike. Use it to resolve "
        "session links of the form `@session:<profile>@<gateway>/<id>` (a session on another "
        "gateway — session_search cannot read those) and to message another session. Also works for "
        "plain `@session:<profile>/<id>` links (this gateway).\n"
        "- action=list: find sessions (optional `query` matches titles; `gateway` filters).\n"
        "- action=read: transcript of `target` (last `limit` messages, default 40).\n"
        "- action=send: deliver `message` into `target` as a new user turn, attributed to this "
        "session. A Kanban worker (HERMES_KANBAN_TASK set) cannot send into a human chat "
        "(desktop/cli source); that send is refused and names the card to place instead. "
        "With `wait_for_reply` (seconds, max 600) it waits for that session's next reply "
        "and returns it; otherwise it returns once delivered.\n"
        "Requires the Hermes Desktop app to be running (it relays the request)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "read", "send"]},
            "target": {"type": "string", "description": "Session link (`@session:...`) or `<gateway>:<session_id>` for read/send."},
            "query": {"type": "string", "description": "list: title search terms."},
            "gateway": {"type": "string", "description": "list: only this gateway (name or id)."},
            "message": {"type": "string", "description": "send: the text to deliver. Relaying a standing instruction of the user (e.g. \"don't wait\")? Put it on its own line as `standing: <exact words>`; quoted user directives get one automatically."},
            "limit": {"type": "integer", "description": "list: max rows (default 20); read: max messages (default 40)."},
            "wait_for_reply": {"type": "integer", "description": "send: seconds to wait for the target's reply (0 = don't wait)."},
        },
        "required": ["action"],
    },
}


def _sender_info(session_id: str | None) -> dict:
    info = {"session_id": session_id or "", "title": ""}
    if not session_id:
        return info
    try:
        from hermes_state import SessionDB
        db = SessionDB(read_only=True)  # a read: writable opens run schema init + a system-wide fd scan
        try:
            info["title"] = db.get_session_title(session_id) or ""
        finally:
            db.close()
    except Exception:
        pass
    try:
        from hermes_cli.profiles import get_active_profile_name  # type: ignore
        info["profile"] = get_active_profile_name() or "default"
    except Exception:
        info["profile"] = "default"
    task = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    if task:
        info["kanban_task"] = task
    return info


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg})


# Models reach this tool through deferred `tool_call` without loading its schema, and guess the
# argument names — `session` for `target` in every logged failure. Accept the common guesses.
TARGET_ALIASES = ("session", "session_id", "sessionId", "session_ref", "session_link", "link", "ref", "id", "to")
MESSAGE_ALIASES = ("text", "content", "body", "prompt")


def _first(args: dict, keys) -> object:
    for k in keys:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return None


def normalize_args(args: dict | None) -> dict:
    args = dict(args or {})
    if not str(args.get("target") or "").strip():
        alias = _first(args, TARGET_ALIASES)
        if alias is not None:
            args["target"] = alias
    if not str(args.get("message") or "").strip():
        alias = _first(args, MESSAGE_ALIASES)
        if alias is not None:
            args["message"] = alias
    return args


import re as _re

# Same directive class as core agent/standing_instructions.py (kept local: hosts on older cores lack it).
_DONT_WAIT = _re.compile(r"\b(?:do\s*not|don['’]?t|dont|never)\s+(?:stop\s+to\s+)?(?:block|wait|pause|ask|stop)\b"
                         r"|\bno\s+(?:check[\s-]?ins?|waiting|pausing)\b", _re.I)
_USER_ATTRIBUTED = _re.compile(r"\b(?:David|the user|user (?:said|says|wrote)|you said|he said|she said)\b", _re.I)
_QUOTE = _re.compile(r"'([^'\n]{4,400})'|\"([^\"\n]{4,400})\"|“([^”\n]{4,400})”|‘([^’\n]{4,400})’")
_STANDING_LINE = _re.compile(r"^[\s>*\-•]*standing:\s*(\S.*?)\s*$", _re.I | _re.M)


def tag_standing(message: str) -> str:
    """Append `standing: <exact words>` for each quoted user directive not to block/wait (self-heal
    agent-aa8d66631c). Core compaction and Kanban worker context keep `standing:` lines word for word, so a
    relayed "dont block, dont wait" cannot be summarized away in the receiving session."""
    if not _DONT_WAIT.search(message) or not _USER_ATTRIBUTED.search(message):
        return message
    have = {" ".join(m.group(1).split()).casefold() for m in _STANDING_LINE.finditer(message)}
    add = []
    for q in _QUOTE.finditer(message):
        words = " ".join(next(g for g in q.groups() if g is not None).split())
        if _DONT_WAIT.search(words) and words.casefold() not in have:
            have.add(words.casefold())
            add.append(f"standing: {words}")
    return message + ("\n\n" + "\n".join(add) if add else "")


def handle(args: dict, session_id: str | None = None, **_kw) -> str:
    args = normalize_args(args)
    action = str(args.get("action") or "").strip()
    if action not in ("list", "read", "send"):
        return _err("action must be list, read, or send")
    if action in ("read", "send") and not str(args.get("target") or "").strip():
        got = sorted(k for k in args if k != "action")
        return _err("target is required for read/send — pass the session link as `target` "
                    "(e.g. target=\"@session:default/20260101_000000_abc123\"); got keys: "
                    + (", ".join(got) or "none"))
    if action == "send" and not str(args.get("message") or "").strip():
        return _err("message is required for send — pass the text as `message`")

    if action == "send":
        refusal = rf.worker_send_refusal(args.get("target"))
        if refusal:
            return _err(refusal)
        args["message"] = tag_standing(str(args["message"]))

    wait = 0
    if action == "send":
        try:
            wait = max(0, min(MAX_REPLY_WAIT, int(args.get("wait_for_reply") or 0)))
        except (TypeError, ValueError):
            wait = 0
    return _relay(action, args, session_id, wait)


TASK_SCHEMA = {
    "name": "cross_task",
    "description": (
        "List, read, or comment on Kanban tasks on ANY gateway connected to the user's Hermes Desktop "
        "app (this Mac and remote/Nous-hosted gateways). Use it to resolve task links of the form "
        "`@task:<board>@<gateway>/<task_id>` (a task on another gateway — kanban_show cannot see "
        "those). Plain `@task:<board>/<task_id>` links live on this gateway: kanban_show(task_id, "
        "board) reads them too.\n"
        "- action=list: find tasks (optional `query` matches titles/ids, `status` e.g. "
        "\"running,blocked\", `gateway` filters).\n"
        "- action=read: the task `target` (task link, `<gateway>:<task_id>` or a bare `t_…` id, "
        "which is searched on every gateway): body, status, latest summary, runs, links, "
        "last `limit` comments/events (default 10).\n"
        "- action=comment: add `message` as a comment on `target`, attributed to this session.\n"
        "Reading or commenting does not tie the task to this session; `hermes-hub adopt-task` does that when "
        "this session really owns the work.\n"
        "Requires the Hermes Desktop app to be running (it relays the request)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "read", "comment"]},
            "target": {"type": "string", "description": "Task link (`@task:...`), `<gateway>:<task_id>` or a bare task id, for read/comment."},
            "query": {"type": "string", "description": "list: title / id search terms."},
            "status": {"type": "string", "description": "list: comma-separated statuses to keep."},
            "gateway": {"type": "string", "description": "list: only this gateway (name or id)."},
            "message": {"type": "string", "description": "comment: the comment text."},
            "limit": {"type": "integer", "description": "list: max rows (default 20); read: max comments/events (default 10)."},
        },
        "required": ["action"],
    },
}

TASK_TARGET_ALIASES = ("task", "task_id", "taskId", "task_ref", "task_link") + TARGET_ALIASES


def handle_task(args: dict, session_id: str | None = None, **_kw) -> str:
    args = dict(args or {})
    if not str(args.get("target") or "").strip():
        alias = _first(args, TASK_TARGET_ALIASES)
        if alias is not None:
            args["target"] = alias
    args = normalize_args(args)
    action = str(args.get("action") or "").strip()
    if action not in ("list", "read", "comment"):
        return _err("action must be list, read, or comment")
    if action in ("read", "comment") and not str(args.get("target") or "").strip():
        got = sorted(k for k in args if k != "action")
        return _err("target is required for read/comment — pass the task link as `target` "
                    "(e.g. target=\"@task:default@nous-1/t_1a2b3c4d\"); got keys: " + (", ".join(got) or "none"))
    if action == "comment" and not str(args.get("message") or "").strip():
        return _err("message is required for comment — pass the text as `message`")
    out = _relay(f"task_{action}", args, session_id, 0)
    return out

def _devices_note(base) -> str:
    try:
        seen = sorted({c.get("device") for c in rf.live_couriers(base) if c.get("device")})
    except Exception:
        seen = []
    return ("Desktops draining this gateway now: " + ", ".join(seen)) if seen else "No Desktop is draining this gateway right now."


def _relay(action: str, args: dict, session_id: str | None, wait: int) -> str:
    base = rf.root()
    rid = uuid.uuid4().hex
    # Which gateway/Mac the request is for, so only a Desktop that can reach it claims it.
    route = rf.route_of_target(args.get("target")) if args.get("target") else rf.norm_name(args.get("gateway"))
    if route in rf.LOCAL_ALIASES and not rf.home_courier_alive(base):
        return _err(f"`@{route}` is ambiguous from this gateway: every connected Desktop calls its own Mac that. "
                    "Name the Mac instead (`@session:<profile>@<device>/<id>`). " + _devices_note(base))
    req = {
        "id": rid,
        "action": action,
        "route": route,
        "args": {k: args.get(k) for k in ("target", "query", "gateway", "message", "limit", "status") if args.get(k) not in (None, "")},
        "wait_for_reply": wait,
        "sender": _sender_info(session_id),
        "created_at": time.time(),
        "ttl": 600,
    }
    rf.atomic_write(base / "outbox" / f"{rid}.json", req)

    outbox = base / "outbox" / f"{rid}.json"
    result_path = base / "results" / f"{rid}.json"
    deadline_pickup = time.time() + PICKUP_TIMEOUT
    deadline = time.time() + PICKUP_TIMEOUT + 120 + wait  # a forwarded send adds one hop

    while time.time() < deadline:
        if result_path.exists():
            try:
                data = json.loads(result_path.read_text())
            except Exception:
                time.sleep(0.3)
                continue
            try:
                result_path.unlink()
            except OSError:
                pass
            return json.dumps(data)
        if outbox.exists() and time.time() > deadline_pickup:
            try:
                outbox.unlink()
            except OSError:
                pass
            if route:
                return _err(f"No connected Hermes Desktop that can reach `{route}` picked up the request "
                            f"(only a Desktop that knows that gateway/Mac claims it). " + _devices_note(base))
            return _err("The Hermes Desktop app did not pick up the request — it must be running with the "
                        "session-mentions plugin enabled to reach other gateways.")
        time.sleep(0.5)
    return _err("Timed out waiting for the Desktop to finish the request (it may still complete).")


_QUALIFIED_REF = None
_TASK_REF = None


def _pre_llm_call(user_message=None, **_kw):
    """Point the model at cross_session / cross_task when the user links a session or Kanban task
    on ANOTHER gateway — without it the model burns turns on session_search / kanban_show, which
    only see this gateway."""
    global _QUALIFIED_REF, _TASK_REF
    import re
    if _QUALIFIED_REF is None:
        _QUALIFIED_REF = re.compile(r"@session:[`\"']?(?:@session:)?[\w.-]*@[\w.-]+/[\w.-]+")
        _TASK_REF = re.compile(r"@task:[`\"']?([\w.-]*)(?:@([\w.-]+))?/(t_[0-9a-z]+)")
    text = user_message if isinstance(user_message, str) else json.dumps(user_message or "")
    notes = []
    refs = sorted(set(_QUALIFIED_REF.findall(text or "")))
    if refs:
        notes.append(
            "Session link(s) " + ", ".join(refs[:5]) + " point to sessions on other Hermes gateways "
            "(`@session:<profile>@<gateway>/<id>`). session_search cannot see them — use the "
            "cross_session tool (action=read, target=<link>) to fetch the transcript, and "
            "cross_session action=send to message them.")
    remote, local = [], []
    for m in _TASK_REF.finditer(text or ""):
        (remote if m.group(2) else local).append(m)
    if remote:
        links = sorted({m.group(0).replace("`", "").replace('"', "").replace("'", "") for m in remote})
        notes.append(
            "Task link(s) " + ", ".join(links[:5]) + " point to Kanban tasks on other Hermes gateways "
            "(`@task:<board>@<gateway>/<task_id>`). kanban_show cannot see them — use the cross_task "
            "tool (action=read, target=<link>) to read one, and cross_task action=comment to comment.")
    if local:
        pairs = sorted({f"kanban_show(task_id={m.group(3)}, board={m.group(1) or 'default'})" for m in local})
        notes.append("Task link(s) `@task:<board>/<task_id>` are Kanban tasks on this gateway: read them with "
                     + "; ".join(pairs[:5]) + ".")
    return {"context": "\n".join(notes)} if notes else None


def register(ctx) -> None:
    ctx.register_tool(name="cross_session", toolset=TOOLSET, schema=SCHEMA, handler=handle, emoji="🔗",
                      description="List/read/message sessions on any Desktop-connected gateway")
    ctx.register_tool(name="cross_task", toolset=TOOLSET, schema=TASK_SCHEMA, handler=handle_task, emoji="🗂️",
                      description="List/read/comment on Kanban tasks on any Desktop-connected gateway")
    ctx.register_hook("pre_llm_call", _pre_llm_call)
