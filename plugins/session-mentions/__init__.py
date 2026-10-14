"""cross_session — list / read / message sessions on any gateway the Desktop is connected to.

The agent never holds other gateways' credentials; the Desktop app does. So the tool writes a
request file, the Desktop plugin (``~/.hermes/desktop-plugins/session-mentions``) drains it over
``GET /api/plugins/session-mentions/outbox``, performs it on the target gateway's own connection,
and posts the result to ``POST /api/plugins/session-mentions/result``. The tool polls for it.
"""
from __future__ import annotations

import json
import time
import uuid

if __package__:
    from . import relay_files as rf
else:  # pytest imports this hyphen-named plugin dir without a parent package
    import importlib.util as _ilu
    from pathlib import Path as _Path

    _spec = _ilu.spec_from_file_location("_session_mentions_relay_files", _Path(__file__).with_name("relay_files.py"))
    rf = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
    _spec.loader.exec_module(rf)  # type: ignore[union-attr]

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
        "session. With `wait_for_reply` (seconds, max 600) it waits for that session's next reply "
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
            "message": {"type": "string", "description": "send: the text to deliver."},
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
        db = SessionDB()
        info["title"] = db.get_session_title(session_id) or ""
    except Exception:
        pass
    try:
        from hermes_cli.profiles import get_active_profile_name  # type: ignore
        info["profile"] = get_active_profile_name() or "default"
    except Exception:
        info["profile"] = "default"
    return info


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg})


def handle(args: dict, session_id: str | None = None, **_kw) -> str:
    action = str(args.get("action") or "").strip()
    if action not in ("list", "read", "send"):
        return _err("action must be list, read, or send")
    if action in ("read", "send") and not str(args.get("target") or "").strip():
        return _err("target is required for read/send")
    if action == "send" and not str(args.get("message") or "").strip():
        return _err("message is required for send")

    wait = 0
    if action == "send":
        try:
            wait = max(0, min(MAX_REPLY_WAIT, int(args.get("wait_for_reply") or 0)))
        except (TypeError, ValueError):
            wait = 0

    base = rf.root()
    rid = uuid.uuid4().hex
    req = {
        "id": rid,
        "action": action,
        "args": {k: args.get(k) for k in ("target", "query", "gateway", "message", "limit") if args.get(k) not in (None, "")},
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
            return _err("The Hermes Desktop app did not pick up the request — it must be running with the "
                        "session-mentions plugin enabled to reach other gateways.")
        time.sleep(0.5)
    return _err("Timed out waiting for the Desktop to finish the request (it may still complete).")


_QUALIFIED_REF = None


def _pre_llm_call(user_message=None, **_kw):
    """Point the model at cross_session when the user links a session on ANOTHER gateway —
    without it the model burns turns on session_search, which only sees this gateway."""
    global _QUALIFIED_REF
    import re
    if _QUALIFIED_REF is None:
        _QUALIFIED_REF = re.compile(r"@session:[`\"']?(?:@session:)?[\w.-]*@[\w.-]+/[\w.-]+")
    text = user_message if isinstance(user_message, str) else json.dumps(user_message or "")
    refs = sorted(set(_QUALIFIED_REF.findall(text or "")))
    if not refs:
        return None
    return {"context": (
        "Session link(s) " + ", ".join(refs[:5]) + " point to sessions on other Hermes gateways "
        "(`@session:<profile>@<gateway>/<id>`). session_search cannot see them — use the "
        "cross_session tool (action=read, target=<link>) to fetch the transcript, and "
        "cross_session action=send to message them.")}


def register(ctx) -> None:
    ctx.register_tool(name="cross_session", toolset=TOOLSET, schema=SCHEMA, handler=handle, emoji="🔗",
                      description="List/read/message sessions on any Desktop-connected gateway")
    ctx.register_hook("pre_llm_call", _pre_llm_call)
