"""Hermes Inbox: Hermes posts proposals of work as sessions the user reads, runs or archives.

- tool ``inbox_post`` (chats, cron jobs, Kanban workers)
- ``pre_llm_call`` hook: a user turn in an open inbox message accepts it (moves it to Recents)
- CLI ``hermes inbox post|list|archive`` for scripts without an agent
- REST under /api/plugins/inbox (dashboard/plugin_api.py) for the Desktop sidebar section
See store.py for the data model.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)
_STORE = Path(__file__).resolve().parent / "store.py"


def store_module():
    key = f"_hermes_inbox_store_{int(_STORE.stat().st_mtime_ns)}"
    mod = sys.modules.get(key)
    if mod is None:
        spec = importlib.util.spec_from_file_location(key, _STORE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[key] = mod
    return mod


SCHEMA = {
    "name": "inbox_post",
    "description": (
        "Post a message to David's Hermes Inbox (left sidebar of Hermes Desktop): a proposal of work "
        "he can read, run with one click ('Do it'), reply to, or archive. Use it when you noticed "
        "something worth doing that needs his go-ahead and he is not in this conversation (cron jobs, "
        "background workers, audits). The message becomes its own session: `body` is what you found "
        "and why it matters (markdown, concrete facts), `accept_prompt` is the exact instruction that "
        "runs when he presses Do it, written so a fresh agent can execute it with no other context. "
        "Pass a stable `key` for recurring findings (e.g. 'vast-idle:2481930') so a re-post updates "
        "the open message instead of adding a duplicate. Do not post routine status or completed "
        "work; post only actionable proposals."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short headline, <= 80 chars, phrased as the proposal."},
            "body": {"type": "string", "description": "Markdown: what you observed, evidence, cost/risk."},
            "accept_prompt": {"type": "string", "description": "Self-contained instruction run on 'Do it'."},
            "key": {"type": "string", "description": "Dedupe key; re-posting with it updates the open message."},
            "cwd": {"type": "string", "description": "Working directory for the work (defaults to yours)."},
            "prompt_label": {"type": "string", "description": "Optional one-line summary of what Do it will do, "
                             "shown on the card instead of the full accept_prompt (e.g. 'stop the idle Vast box')."},
        },
        "required": ["title", "body", "accept_prompt"],
        "additionalProperties": False,
    },
}


def _poster_for(session_id: str | None) -> tuple[str | None, str | None]:
    """('cron · Vast idle audit', '/cwd') from the posting session's row."""
    if not session_id:
        return None, None
    try:
        from hermes_state import SessionDB
        db = SessionDB(read_only=True)
        try:
            row = db.get_session(session_id) or {}
        finally:
            db.close()
    except Exception:
        return None, None
    source = row.get("source") or ""
    title = (row.get("title") or "").strip()
    label = " · ".join(p for p in (source, title) if p) or None
    return label, row.get("cwd")


def handle_post(args: dict, **context) -> str:
    try:
        args = args or {}
        poster, cwd = _poster_for(context.get("session_id"))
        result = store_module().Inbox().post(
            args.get("title", ""), args.get("body", ""), args.get("accept_prompt", ""),
            key=args.get("key"), poster=poster, cwd=args.get("cwd") or cwd, prompt_label=args.get("prompt_label"),
        )
        result["posted"] = True
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"posted": False, "error": f"{type(exc).__name__}: {exc}"})


def _pre_llm_call(session_id=None, **_):
    try:
        if session_id and store_module().Inbox().on_user_turn(session_id):
            log.info("inbox: %s accepted by a user turn", session_id)
    except Exception:
        log.debug("inbox: accept hook failed", exc_info=True)
    return None


# ── CLI ─────────────────────────────────────────────────────────────────────
def _setup_cli(parser) -> None:
    sub = parser.add_subparsers(dest="inbox_cmd")
    p = sub.add_parser("post", help="post a proposal")
    p.add_argument("title")
    p.add_argument("--body", default="", help="markdown body ('-' reads stdin)")
    p.add_argument("--prompt", required=True, help="instruction 'Do it' runs")
    p.add_argument("--key")
    p.add_argument("--poster")
    p.add_argument("--cwd")
    p.add_argument("--prompt-label", help="one line shown on the card instead of the full prompt")
    sub.add_parser("list", help="list open messages")
    a = sub.add_parser("archive", help="archive a message")
    a.add_argument("session_id")


def _run_cli(args) -> int:
    inbox = store_module().Inbox()
    cmd = getattr(args, "inbox_cmd", None)
    if cmd == "post":
        body = sys.stdin.read() if args.body == "-" else args.body
        print(json.dumps(inbox.post(args.title, body, args.prompt, key=args.key, poster=args.poster,
                                    cwd=args.cwd, prompt_label=getattr(args, "prompt_label", None))))
        return 0
    if cmd == "list":
        for item in inbox.list():
            print(f"{'●' if item['unread'] else ' '} {item['session_id']}  {item['title']}  [{item.get('poster') or ''}]")
        return 0
    if cmd == "archive":
        ok = inbox.archive(args.session_id)
        print("archived" if ok else "not an inbox message")
        return 0 if ok else 1
    print("usage: hermes inbox {post,list,archive}")
    return 2


def register(ctx) -> None:
    ctx.register_tool(name="inbox_post", toolset="inbox", schema=SCHEMA, handler=handle_post, emoji="📥",
                      description="Post a proposal of work to the user's Hermes Inbox")
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    try:
        ctx.register_cli_command("inbox", "Hermes Inbox: post, list, archive proposals", _setup_cli,
                                 handler_fn=_run_cli)
    except Exception:
        log.debug("inbox: CLI registration unavailable", exc_info=True)
