"""Hermes Inbox: Hermes posts proposals of work as sessions the user reads, runs or archives.

- tools ``inbox_post`` (chats, cron jobs, Kanban workers), ``inbox_outcome`` (read a card's
  decision back by key), ``inbox_types`` (list / show / define card types)
- ``pre_llm_call`` hook: a user turn in an open inbox message accepts it (moves it to Recents)
- CLI ``hermes inbox post|list|archive|act|outcome|types`` for scripts without an agent
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
        "work; post only actionable proposals. Never post what an agent can do without David: a "
        "failure, red CI or broken job goes to a Kanban card (assignee default), not the Inbox, and "
        "never post an Inbox card next to a Kanban card for the same work. A poster whose last 3 "
        "cards were archived with no action is refused (PosterMuted) until fixed and unmuted; posts "
        "telling an agent to fix/unblock a Kanban card are refused outright (AgentFixablePost).\n"
        "CARD TYPES: pass `type` (+ `fields`) to get a decision card with that type's own buttons "
        "instead of Do it. A type is a YAML spec (see inbox_types) declaring `fields` (required / max "
        "length / preview) and `actions`, each of kind `resolve` (records the decision, closes the card, "
        "no agent turn; with `input` the user edits a value first, e.g. Edit tweet text) or `prompt` "
        "(runs a templated instruction like '{{id}}: {{input}}' in the card's chat). Archive is always "
        "there and means 'dismissed, no decision'. Read the decision back later with "
        "inbox_outcome(key) and act on it; never treat an open or archived card as approval. Typed "
        "cards need no accept_prompt unless one of the type's prompt templates uses {{accept_prompt}}. "
        "Example: type='x-post', fields={'id':'Q3','text':'...','slot':'Tue 9am'}, key='x-post:Q3'. "
        "Re-posting the same key refreshes the OPEN card's fields; once David acted on a card with "
        "resolve actions (or archived it), a re-post with that key opens a new card."
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
            "type": {"type": "string", "description": "Card type name (default 'default' = Do it / Archive). "
                     "List available types with inbox_types(action='list')."},
            "fields": {"type": "object", "additionalProperties": {"type": "string"},
                       "description": "Values for the type's declared fields (strings). Required fields must "
                       "be present; values over a field's max are rejected."},
        },
        "required": ["title", "body"],
        "additionalProperties": False,
    },
}


OUTCOME_SCHEMA = {
    "name": "inbox_outcome",
    "description": (
        "Read back what David decided on the newest Inbox card with `key` (the key you passed to "
        "inbox_post). Returns {state, outcome, value, fields, at, session_id, type, task}: state is open "
        "(no decision yet), accepted (he ran a prompt action or replied), resolved (he pressed a "
        "resolve action) or archived (dismissed, no decision); outcome is the action id he pressed "
        "(e.g. approve / edit / skip, 'archived' for Archive); value is the text he typed for an input "
        "action (e.g. the edited tweet), and fields are the card's fields after any edit. Returns "
        "{found: false} when no card ever had the key. Only act on an explicit resolve outcome."
    ),
    "parameters": {"type": "object", "properties": {
        "key": {"type": "string", "description": "The card's dedupe key, e.g. 'x-post:Q3'."}},
        "required": ["key"], "additionalProperties": False},
}

TYPES_SCHEMA = {
    "name": "inbox_types",
    "description": (
        "List, show or define Inbox card types. A type declares a card's fields and its buttons:\n"
        "  name: x-post\n  label: X post\n  fields:\n    id: {required: true}\n"
        "    text: {required: true, preview: true, max: 280}\n  actions:\n"
        "    - {id: approve, label: Approve, icon: check, style: primary, kind: resolve, row: true}\n"
        "    - {id: edit, label: Edit, icon: edit, kind: resolve, input: {field: text, multiline: true}}\n"
        "    - {id: rewrite, label: Rewrite, kind: prompt, input: {placeholder: What to change},\n"
        "       prompt: 'Rewrite draft {{id}}: {{input}}'}\n"
        "Action kinds: resolve (records outcome=<id> and value=<input>, closes the card; input.field also "
        "writes the value into that field), task (resolve + queues a background Kanban card whose body is the "
        "rendered `prompt`; options assignee (default 'default'), board, title template, skills list; the "
        "card id is returned as `task` by inbox_outcome; if queueing fails the card stays open) and prompt (renders the template with {{field}}, {{input}}, "
        "{{key}}, {{title}}, {{accept_prompt}} in one plain substitution pass and runs it in the card's "
        "chat; the template shows as the button's tooltip). Options: input "
        "{field, multiline, max, placeholder, required}, confirm (true or a message), style "
        "(primary/secondary/danger), icon (codicon name), row (resolve actions without input only: also a "
        "sidebar hover button). 'archive' is reserved (always present). YAML is parsed as YAML 1.2 "
        "(only true/false are booleans); anchors/aliases are rejected. Sources, first wins: local types "
        "(<HERMES_HOME>/inbox/types/<name>.yaml), a skill's inbox-types/<name>.yaml, the built-in "
        "'default'. Prefer shipping a type in the owning skill's inbox-types/ dir; use define for ad-hoc "
        "ones (define cannot redefine 'default' or shadow a skill's type). Names match "
        "^[a-z0-9][a-z0-9_-]{0,63}$."
    ),
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["list", "get", "define"]},
        "name": {"type": "string", "description": "Type name (get / define)."},
        "spec": {"anyOf": [{"type": "object"}, {"type": "string"}],
                 "description": "define: the type as an object or YAML/JSON text."}},
        "required": ["action"], "additionalProperties": False},
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
            type=args.get("type"), fields=args.get("fields"),
        )
        result["posted"] = True
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"posted": False, "error": f"{type(exc).__name__}: {exc}"})


def handle_outcome(args: dict, **_) -> str:
    try:
        key = str((args or {}).get("key") or "").strip()
        if not key:
            return json.dumps({"error": "key is required"})
        out = store_module().Inbox().outcome(key)
        return json.dumps(dict(out, found=True) if out else {"found": False, "key": key})
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


def handle_types(args: dict, **_) -> str:
    try:
        args = args or {}
        reg = store_module().Inbox().types
        action = args.get("action")
        if action == "list":
            return json.dumps({"types": reg.list()})
        if action == "get":
            return json.dumps(reg.get(args.get("name")))
        if action == "define":
            return json.dumps(dict(reg.define(args.get("name"), args.get("spec")), defined=True))
        return json.dumps({"error": "action must be list, get or define"})
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


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
    p.add_argument("--prompt", default="", help="instruction 'Do it' runs (required for the default type)")
    p.add_argument("--key")
    p.add_argument("--poster")
    p.add_argument("--cwd")
    p.add_argument("--prompt-label", help="one line shown on the card instead of the full prompt")
    p.add_argument("--type", help="card type (see `hermes inbox types`)")
    p.add_argument("--field", action="append", default=[], metavar="K=V", help="a type field (repeatable)")
    sub.add_parser("list", help="list open messages")
    u = sub.add_parser("unmute", help="let a poster muted as noise post again (after fixing it)")
    u.add_argument("poster")
    a = sub.add_parser("archive", help="archive a message")
    a.add_argument("session_id")
    t = sub.add_parser("types", help="list card types, or show one")
    t.add_argument("name", nargs="?")
    o = sub.add_parser("outcome", help="what happened to the newest card with a key")
    o.add_argument("key")
    x = sub.add_parser("act", help="run one of a card's actions")
    x.add_argument("session_id")
    x.add_argument("action")
    x.add_argument("--input", help="value for an action with an input ('-' reads stdin)")


def _run_cli(args) -> int:
    inbox = store_module().Inbox()
    cmd = getattr(args, "inbox_cmd", None)
    if cmd == "post":
        body = sys.stdin.read() if args.body == "-" else args.body
        fields = {}
        for kv in getattr(args, "field", None) or []:
            k, sep, v = kv.partition("=")
            if not sep:
                print(f"--field needs K=V, got {kv!r}", file=sys.stderr)
                return 2
            fields[k.strip()] = v
        try:
            print(json.dumps(inbox.post(args.title, body, args.prompt, key=args.key, poster=args.poster,
                                        cwd=args.cwd, prompt_label=getattr(args, "prompt_label", None),
                                        type=getattr(args, "type", None), fields=fields or None)))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0
    if cmd == "types":
        try:
            if args.name:
                print(json.dumps(inbox.types.get(args.name), indent=2))
            else:
                for t in inbox.types.list():
                    acts = ", ".join(a["id"] for a in t.get("actions", [])) or t.get("error", "")
                    print(f"{t['name']:<20} {t.get('source', ''):<28} {acts}")
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0
    if cmd == "outcome":
        out = inbox.outcome(args.key)
        print(json.dumps(dict(out, found=True) if out else {"found": False, "key": args.key}))
        return 0 if out else 1
    if cmd == "act":
        value = sys.stdin.read() if args.input == "-" else args.input
        try:
            item = inbox.act(args.session_id, args.action, value)
        except Exception as exc:
            if not isinstance(exc, ValueError) and type(exc).__name__ != "TaskError":
                raise
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if not item:
            print("not an open inbox message")
            return 1
        print(json.dumps({k: item.get(k) for k in ("session_id", "state", "outcome", "outcome_value", "outcome_task", "prompt")
                          if item.get(k) is not None}))
        return 0
    if cmd == "list":
        for item in inbox.list():
            print(f"{'●' if item['unread'] else ' '} {item['session_id']}  {item['title']}  [{item.get('poster') or ''}]")
        return 0
    if cmd == "archive":
        ok = inbox.archive(args.session_id)
        print("archived" if ok else "not an inbox message")
        return 0 if ok else 1
    if cmd == "unmute":
        inbox.unmute(args.poster)
        print(f"unmuted {args.poster!r}")
        return 0
    print("usage: hermes inbox {post,list,archive,act,outcome,types,unmute}")
    return 2


def register(ctx) -> None:
    ctx.register_tool(name="inbox_post", toolset="inbox", schema=SCHEMA, handler=handle_post, emoji="📥",
                      description="Post a proposal of work to the user's Hermes Inbox")
    ctx.register_tool(name="inbox_outcome", toolset="inbox", schema=OUTCOME_SCHEMA, handler=handle_outcome,
                      emoji="📥", description="Read back the decision on an Inbox card by key")
    ctx.register_tool(name="inbox_types", toolset="inbox", schema=TYPES_SCHEMA, handler=handle_types,
                      emoji="📥", description="List, show or define Inbox card types")
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    try:
        ctx.register_cli_command("inbox", "Hermes Inbox: post, list, act on and archive proposals", _setup_cli,
                                 handler_fn=_run_cli)
    except Exception:
        log.debug("inbox: CLI registration unavailable", exc_info=True)
