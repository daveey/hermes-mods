"""cross_session — list / read / message sessions on any gateway the Desktop is connected to.

The agent never holds other gateways' credentials; the Desktop app does. So the tool writes a
request file, the Desktop plugin (``~/.hermes/desktop-plugins/session-mentions``) drains it over
``GET /api/plugins/session-mentions/outbox``, performs it on the target gateway's own connection,
and posts the result to ``POST /api/plugins/session-mentions/result``. The tool polls for it.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid

if __package__:
    from . import relay_files as rf
    from . import board_tasks as bt
    from . import relay_human as rh
else:  # pytest imports this hyphen-named plugin dir without a parent package
    import importlib.util as _ilu
    from pathlib import Path as _Path

    def _load_sibling(name):
        spec = _ilu.spec_from_file_location(f"_session_mentions_{name}", _Path(__file__).with_name(f"{name}.py"))
        mod = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod

    rf = _load_sibling("relay_files")
    bt = _load_sibling("board_tasks")
    rh = _load_sibling("relay_human")

TOOLSET = "session_mentions"
PICKUP_TIMEOUT = 30.0  # Desktop drains on the outbox push, else every ~3s; no claim by then = Desktop not running
MAX_REPLY_WAIT = 600
# How long the Desktop courier keeps re-submitting a cross_session send that core refuses because another backend
# holds the chat's turn (`retry_busy`; desktop-plugins/session-mentions/plugin.js `OWNER_RETRY_WINDOW_S` is the
# same number, held equal by tests/test_send_retry_ttl.py). The send's wait below outlasts it by the grace, and
# both stay under core's 420 s cut of a tool call (agent/tool_executor.py), or the tool would report a timeout
# on a message that still lands. When the wait ends the request is marked cancelled and the courier stops.
SEND_BUSY_RETRY = 300
SEND_RETRY_GRACE = 60  # the last re-submit's own 45 s timeout + posting the result
# A send's whole wait (reaching the chat + waiting for its reply) stays under core's 420 s cut. The request
# carries it as `total_wait_seconds`; the courier, once the submit is accepted, waits for the reply
# min(wait_for_reply, what is left of it minus SEND_REPLY_MARGIN) and reports a shortened wait as
# `reply_wait_seconds` (plugin.js `replyBudgetOf`). A send accepted at once keeps up to 390 s.
SEND_TOTAL_WAIT_MAX = 400
SEND_REPLY_MARGIN = 10  # plugin.js REPLY_MARGIN_S: the last poll, the transcript read, posting the result

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
        "With `wait_for_reply` (seconds; the whole call, reaching the chat included, is held to 400 s, so "
        "the wait is at most 390 and shorter after a busy-chat retry: the result then says so in "
        "`reply_wait_note`) it waits for that session's next reply "
        "and returns it; otherwise it returns once delivered. For relaying a Kanban comment or decision, "
        "set dedupe_key=\"kanban:<task_id>:<comment_id>\". A duplicate returns the first delivery receipt, "
        "not a new turn (duplicate_pending while in flight). Without a key, sends are unchanged.\n"
        "While another Hermes window holds the target's turn the send is retried for up to 5 minutes; "
        "`status: refused` with `retry_safe: true` after that means nothing was delivered.\n"
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
            "dedupe_key": {"type": "string", "maxLength": 200, "description": "send: gateway-wide idempotency key; use kanban:<task_id>:<comment_id> for relayed decisions (7-day retention)."},
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

    if action == "send" and "dedupe_key" in args:
        key = args["dedupe_key"]
        if not isinstance(key, str) or not key or len(key) > 200:
            return _err("dedupe_key must be a nonempty string of at most 200 characters")
    if action == "send":
        refusal = rf.worker_send_refusal(args.get("target"))
        if refusal:
            return _err(refusal)
        args["message"] = tag_standing(str(args["message"]))

    wait = asked = 0
    if action == "send":
        try:
            asked = max(0, min(MAX_REPLY_WAIT, int(args.get("wait_for_reply") or 0)))
        except (TypeError, ValueError):
            asked = 0
        wait = min(asked, SEND_TOTAL_WAIT_MAX - SEND_REPLY_MARGIN)
    out = _relay(action, args, session_id, wait)
    if asked:
        try:
            data = json.loads(out)
        except ValueError:
            return out
        # The courier reports the reply wait it really used when reaching the chat took part of the budget.
        used = data.get("reply_wait_seconds") if isinstance(data, dict) else None
        used = wait if not isinstance(used, (int, float)) or isinstance(used, bool) else min(wait, max(0, int(used)))
        if isinstance(data, dict) and used < asked:
            data["reply_wait_seconds"] = used
            data["reply_wait_note"] = (
                f"wait_for_reply was shortened from {asked} s to {used} s: the whole call, reaching the chat "
                f"included, is held to {SEND_TOTAL_WAIT_MAX} s (a tool call is cut at 420 s). "
                "No reply here does not mean none will come: read the target (action=read) later.")
            out = json.dumps(data)
    return out


TASK_SCHEMA = {
    "name": "cross_task",
    "description": (
        "List, read, or comment on Kanban tasks. Headless hosts configured with "
        "session_mentions.task_transport=board_replica read/list only their selected authorized "
        "Board replica (possibly stale, never live authority); comments are refused. Bare IDs are "
        "scoped to that board, not searched globally. Explicit gateway must match the replica owner id. "
        "Otherwise tasks are reached on ANY gateway connected to the user's Hermes Desktop "
        "app (this Mac and remote/Nous-hosted gateways). Use it to resolve task links of the form "
        "`@task:<board>@<gateway>/<task_id>` (a task on another gateway — kanban_show cannot see "
        "those). Plain `@task:<board>/<task_id>` links live on this gateway: kanban_show(task_id, "
        "board) reads them too.\n"
        "- action=list: find tasks (optional `query` matches titles/ids, `status` e.g. "
        "\"running,blocked\", `gateway` filters).\n"
        "- action=read: the task `target` (task link, `<gateway>:<task_id>` or a bare `t_…` id, "
        "which is searched on every gateway): body, status, latest summary, runs, links, "
        "last `limit` comments/events (default 10). `task.source: replica` + `task.owner` means the "
        "card lives on another gateway (e.g. a Board worker node) and this is the Board replica copy; "
        "a ready card carries `task.queue` (its lane and how many cards are ahead), so a long wait "
        "reads as queued, not stuck.\n"
        "- action=comment: add `message` as a comment on `target`, attributed to this session. A card "
        "reachable only as a replica copy is refused with `rejection: foreign_card` naming its owner. "
        "When the message passes on the user's reply to a card that asked them, set "
        "`relays_user_message: true`: the card then counts it as a human answer (once per user message).\n"
        "Reading or commenting does not tie the task to this session; `hermes-hub adopt-task` does that when "
        "this session really owns the work.\n"
        "Gateways no connected Desktop reaches (Board worker nodes `worker-N`) go through the hermes-hub courier: "
        "comments are queued to the owning gateway (`transport: hub_courier`), reads come from the replica.\n"
        "Otherwise requires the Hermes Desktop app to be running (it relays the request)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "read", "comment"]},
            "target": {"type": "string", "description": "Task link (`@task:...`), `<gateway>:<task_id>` or a bare task id, for read/comment."},
            "board": {"type": "string", "description": "Headless replica mode: selected board (worker board grants still apply)."},
            "query": {"type": "string", "description": "list: title / id search terms."},
            "status": {"type": "string", "description": "list: comma-separated statuses to keep."},
            "gateway": {"type": "string", "description": "list: only this gateway (name or id)."},
            "message": {"type": "string", "description": "comment: the comment text."},
            "relays_user_message": {"type": "boolean", "description": "comment: true when `message` passes on the user's reply from this chat to a card that asked them."},
            "limit": {"type": "integer", "description": "list: max rows (default 20); read: max comments/events (default 10)."},
        },
        "required": ["action"],
    },
}

TASK_TARGET_ALIASES = ("task", "task_id", "taskId", "task_ref", "task_link") + TARGET_ALIASES


def handle_task(args: dict, session_id: str | None = None, *, timeout_seconds: float | None = None, **_kw) -> str:
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
    if bt.enabled():
        return json.dumps(bt.handle(args))
    args.pop("relayed_human", None)  # only the origin's own proof below may set it, never the model
    hub = _hub_route(action, args, session_id)
    if hub is not None:
        return hub
    if action == "comment" and rh.requested(args):
        # kanban-d1b921f32e: this gateway holds the chat's transcript, so it proves the relay here.
        relayed, refusal = rh.stamp(str(args["target"]), session_id, base=rf.root(),
                                    state_db=rf._hermes_home() / "state.db")
        if refusal:
            return _err(refusal)
        args["relayed_human"] = relayed
    out = _relay(f"task_{action}", args, session_id, 0, **({"timeout_seconds": timeout_seconds} if timeout_seconds is not None else {}))
    if args.get("relayed_human"):
        # The ledger records a relay only once the card's gateway stored it: a failed delivery leaves the
        # user message relayable. That core's own refusal (replay, pre-ask) is a refusal, not a transport error.
        try:
            data = json.loads(out)
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("ok") is True:
            rh.record(str(args["target"]), args["relayed_human"], base=rf.root(), write=rf.atomic_write)
        elif (refused := rh.refusal_of(data)) is not None:
            return json.dumps({"ok": False, "rejection": "relayed_human_refused",
                               "error": f"relays_user_message refused by the card's gateway: {refused}. "
                                        "Post it without the flag if it is not the user's reply."})
    return out

# --- gateways no Desktop courier reaches (Board worker nodes `worker-N`): the hermes-hub courier (agent-e4fd46c7a7)

log = logging.getLogger(__name__)
HUB_CLI_TIMEOUT = 60.0
HUB_HINT = ("Reach it with the hermes-hub courier: `hermes-hub request <gateway> tasks <profile>/<board>/<task_id> "
            "comment --arg text=<message>` (reads: `hermes-hub tasks --gw <gateway> --json`).")


def _route_claimable(route: str, base) -> bool | None:
    """True when a live Desktop courier may claim ``route`` (its device/aliases or reach list has it, or a legacy
    courier with no reach list), False when live couriers exist and none can, None when no courier is live
    (a Desktop may be starting: keep the old pickup wait)."""
    couriers = rf.live_couriers(base)
    if not couriers:
        return None
    for c in couriers:
        reach = rf.names(c.get("reach") or ())
        if not reach or route in reach or route in rf.names(c.get("aliases") or ()) or route == rf.norm_name(c.get("device")):
            return True
    return False


def _hub_state_db():
    from pathlib import Path
    return Path(os.environ.get("HERMES_HUB_STATE") or Path.home() / ".local/state/hermes-hub") / "hub.db"


def _hub_gateways() -> set:
    """Gateway ids the hermes-hub replica knows (what `hermes-hub request` resolves); empty without a replica."""
    path = _hub_state_db()
    if not path.is_file():
        return set()
    try:
        con = rf._ro_connect(path)
        try:
            return {rf.norm_name(r[0]) for r in con.execute("SELECT DISTINCT gw FROM replica") if r[0]}
        finally:
            con.close()
    except Exception:
        return set()


def _hub_cli() -> str | None:
    import shutil
    from pathlib import Path
    found = shutil.which("hermes-hub")
    if found:
        return found
    fallback = Path.home() / ".local/bin/hermes-hub"
    return str(fallback) if fallback.is_file() else None


def _hub_run(argv: list, session_id: str | None = None):
    import subprocess
    env = dict(os.environ)
    if session_id:
        # hermes-hub stamps an agent's comment (writer_kind=agent) from its own environment: the gateway process
        # running this tool may lack the markers, so the call names them.
        env["HERMES_AGENT"] = "1"
        env["HERMES_SESSION_ID"] = str(session_id)
    else:
        env.setdefault("HERMES_AGENT", "1")
    return subprocess.run([_hub_cli() or "hermes-hub"] + argv, capture_output=True, text=True, timeout=HUB_CLI_TIMEOUT, env=env)


def _hub_tasks(gw: str) -> list:
    proc = _hub_run(["tasks", "--gw", gw, "--all", "--json"])
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip()[:500] or f"exit {proc.returncode}")
    rows = json.loads(proc.stdout or "[]")
    return rows if isinstance(rows, list) else []


def _task_ref(target) -> tuple[str, str]:
    """(board, task id) from `@task:<board>@<gw>/<id>` or `<gw>:<id>`; board '' when the ref has none."""
    text = str(target or "").strip().strip("`\"'")
    while text.startswith("@task:"):
        text = text[len("@task:"):].strip("`\"'")
    if "/" in text:
        head, tid = text.split("/", 1)
        return head.split("@", 1)[0], tid.strip()
    if ":" in text:
        return "", text.split(":", 1)[1].strip()
    return "", text


def _hub_route(action: str, args: dict, session_id: str | None) -> str | None:
    """Serve a request no live Desktop courier can claim. None = leave it to the Desktop relay."""
    route = rf.route_of_target(args.get("target")) if args.get("target") else rf.norm_name(args.get("gateway"))
    if not route or route in rf.LOCAL_ALIASES or route == rf.gateway_name():
        return None
    base = rf.root()
    if _route_claimable(route, base) is not False:
        return None
    if route not in _hub_gateways() or not _hub_cli():
        if action in ("read", "comment"):
            canonical = bt.canonical_reference(args)
            if canonical is not None:
                return json.dumps(canonical)
        reach = sorted({n for c in rf.live_couriers(base) for n in rf.names(c.get("reach") or ())})
        log.warning("cross_task: unroutable route=%s", route)
        return _err(f"No Desktop courier reaches `{route}` (live reach lists: {', '.join(reach) or 'none'}) and the "
                    f"hermes-hub replica on this gateway does not know it. " + HUB_HINT)
    try:
        if action == "comment":
            if rh.requested(args):
                return _err("relays_user_message is not carried by the hermes-hub courier that reaches "
                            f"`{route}`; post the comment without the flag, or relay it from a chat on that gateway.")
            board, tid = _task_ref(args.get("target"))
            profile = "default"
            try:
                row = next((r for r in _hub_tasks(route) if r.get("id") == tid), None)
            except Exception:
                row = None
            if row:
                profile = str(row.get("owner_profile") or profile)
                board = str(row.get("board") or board)
            key = f"{profile}/{board or 'default'}/{tid}"
            proc = _hub_run(["request", route, "tasks", key, "comment", "--arg", f"text={args['message']}"], session_id)
            rid = (proc.stdout or "").strip().splitlines()[-1:] or [""]
            if proc.returncode not in (0, 3):
                log.warning("cross_task: unroutable route=%s (hermes-hub request exit %s)", route, proc.returncode)
                return _err(f"hermes-hub request to `{route}` failed: {(proc.stderr or proc.stdout).strip()[:500]}")
            out = {"ok": True, "transport": "hub_courier", "gateway": route, "key": key, "request_id": rid[0]}
            if proc.returncode == 3:
                out["queued"] = "hub unreachable; queued locally and sent on the next hermes-hub sync"
            out["note"] = f"Delivered by the owning gateway's courier; `hermes-hub requests` shows {rid[0]}'s outcome."
            return json.dumps(out)
        rows = _hub_tasks(route)
        meta = {"ok": True, "source": "replica", "transport": "hub_replica", "gateway": route,
                "freshness": "hermes-hub replica copy; it may lag the owning gateway."}
        if action == "read":
            _board, tid = _task_ref(args.get("target"))
            row = next((r for r in rows if r.get("id") == tid), None)
            if row is None:
                return _err(f"Task {tid} is not in the hermes-hub replica for `{route}`.")
            return json.dumps({**meta, "task": {**row, "source": "replica", "owner": route}})
        query = str(args.get("query") or "").casefold()
        statuses = set(rf.names(args.get("status") or ""))
        try:
            limit = max(1, min(200, int(args.get("limit") or 20)))
        except (TypeError, ValueError):
            limit = 20
        keep = [r for r in rows if (not statuses or rf.norm_name(r.get("status")) in statuses)
                and query in f"{r.get('id', '')} {r.get('title', '')}".casefold()]
        return json.dumps({**meta, "tasks": keep[:limit]})
    except Exception as exc:
        log.warning("cross_task: unroutable route=%s (%s)", route, exc)
        return _err(f"hermes-hub courier for `{route}` failed: {exc}. " + HUB_HINT)


def _devices_note(base) -> str:
    try:
        seen = sorted({c.get("device") for c in rf.live_couriers(base) if c.get("device")})
    except Exception:
        seen = []
    return ("Desktops draining this gateway now: " + ", ".join(seen)) if seen else "No Desktop is draining this gateway right now."


def _pickup_diagnosis(base) -> str:
    """Why nobody claimed a route-less request: no Desktop draining this gateway (quit/asleep: tell the owner,
    retry later) versus live couriers that did not claim (busy or an older plugin: retry once, then report)."""
    try:
        live = rf.live_couriers(base)
        if live:
            return (f"{len(live)} Desktop courier(s) are live ({', '.join(sorted(str(c.get('device')) for c in live))}) "
                    "but none claimed it within the wait; retry once, and if it repeats the Desktop is busy or runs an "
                    "older session-mentions plugin.")
        ages = []
        for path in (base / "couriers").glob("*.json") if (base / "couriers").is_dir() else ():
            try:
                ages.append((time.time() - path.stat().st_mtime, path.stem))
            except OSError:
                continue
        if ages:
            age, dev = min(ages)
            return (f"No Desktop is draining this gateway now (last courier `{dev}` was seen {int(age)}s ago: it is "
                    "quitting, asleep or offline). Retry when it is back; a hub-resident session can use the "
                    "hermes-hub courier for task reads/comments.")
        return "No Desktop has ever drained this gateway's outbox here (no courier record): open Hermes Desktop on a Mac that reaches it."
    except Exception:
        return ""


def _relay(action: str, args: dict, session_id: str | None, wait: int, *, timeout_seconds: float | None = None) -> str:
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
        "args": {k: args.get(k) for k in ("target", "query", "gateway", "message", "limit", "status", "relayed_human") if args.get(k) not in (None, "")},
        "wait_for_reply": wait,
        "sender": _sender_info(session_id),
        "created_at": time.time(),
        "ttl": 600,
    }
    if action == "send":  # opt in: other writers of send requests (Slack follower, close-out) want the first refusal
        req.update(retry_busy=True, retry_busy_seconds=SEND_BUSY_RETRY, total_wait_seconds=SEND_TOTAL_WAIT_MAX)
    if action == "send" and args.get("dedupe_key"):
        duplicate = rf.queue_send(base, req, args["dedupe_key"])
        if duplicate:
            return json.dumps(duplicate)
    else:
        rf.atomic_write(base / "outbox" / f"{rid}.json", req)
    if hasattr(rf, "notify_outbox"):
        rf.notify_outbox(base)  # wake the Desktop courier now (no-op outside `hermes serve`)

    outbox = base / "outbox" / f"{rid}.json"
    result_path = base / "results" / f"{rid}.json"
    deadline_pickup = time.time() + PICKUP_TIMEOUT
    deadline = time.time() + PICKUP_TIMEOUT + 120 + wait  # a forwarded send adds one hop
    if action == "send":  # the courier may retry a refused send for the whole TTL before it answers
        deadline = time.time() + min(SEND_BUSY_RETRY + SEND_RETRY_GRACE + wait, SEND_TOTAL_WAIT_MAX + SEND_REPLY_MARGIN)
    if timeout_seconds is not None:
        deadline = min(deadline, time.time() + max(0, timeout_seconds))

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
        if time.time() > deadline_pickup and rf.take_back(outbox):  # else a courier claimed it: await its answer
            if route:
                log.warning("cross_task: unroutable route=%s (no Desktop pickup)", route)
                return _err(f"No connected Hermes Desktop that can reach `{route}` picked up the request "
                            f"(only a Desktop that knows that gateway/Mac claims it). " + _devices_note(base)
                            + (" " + HUB_HINT if action.startswith("task_") else ""))
            return _err("The Hermes Desktop app did not pick up the request — it must be running with the "
                        "session-mentions plugin enabled to reach other gateways. " + _pickup_diagnosis(base))
        if action == "send" and _interrupted():
            # The turn was stopped or this call abandoned: nobody reads the answer, so stop the courier's retry.
            if rf.take_back(outbox):
                return _err("Interrupted before the Desktop picked the send up: nothing was sent.")
            if hasattr(rf, "mark_cancelled"):
                rf.mark_cancelled(rid, base)
            return _err("Interrupted while the Desktop was delivering the send: its retry was cancelled, "
                        "but a submit already under way may still land. Read the target before resending.")
        if _yield_requested():
            # Steering is not Stop: release this tool's wait, but leave courier
            # custody intact so an in-flight send is not cancelled or repeated.
            return json.dumps({"ok": True, "status": "pending", "request_id": rid,
                               "message": "Yielded to a new chat message; the Desktop request remains live. "
                                          "Read the target before resending."})
        time.sleep(0.5)
    if timeout_seconds is not None:
        rf.take_back(outbox)  # withdraw unclaimed bounded read requests
    if action == "send" and hasattr(rf, "mark_cancelled"):
        # Nobody waits any more: tell the courier before its next re-submit, then take an answer that just landed.
        rf.mark_cancelled(rid, base)
        try:
            data = json.loads(result_path.read_text())
            result_path.unlink(missing_ok=True)
            return json.dumps(data)
        except (OSError, ValueError):
            pass
    return _err("Timed out waiting for the Desktop to finish the request (it may still complete).")


def _yield_requested() -> bool:
    try:
        import threading
        from tools.interrupt import consume_yield
        return bool(consume_yield(threading.get_ident()))
    except Exception:
        return False


def _interrupted() -> bool:
    """Core's per-thread interrupt bit (tools.interrupt): set on this tool's worker thread when the turn is
    stopped or the call is abandoned. False where core is not importable (tests, older core)."""
    try:
        from tools.interrupt import is_interrupted  # type: ignore

        return bool(is_interrupted())
    except Exception:
        return False


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


def _relay_guard(tool_name="", args=None, session_id="", **_kw):
    """pre_tool_call: refuse core's own ``kanban_comment relays_user_message`` when core's verify() would name a
    user row hermes-mods wrote (a ``[coach]`` nudge or brief) or a ``[selfheal-probe]`` prompt as the user's reply
    (core does not know those openings). Fail-open: any error lets core decide."""
    if tool_name != "kanban_comment" or not isinstance(args, dict) or not rh.requested(args):
        return None
    try:
        reason = rh.core_relay_refusal(rf._hermes_home() / "state.db", session_id)
    except Exception:
        return None
    return {"action": "block", "message": reason} if reason else None


def stop_watcher() -> None:
    """Plugin unload / disable: stop the dashboard's outbox watcher thread (dashboard/plugin_api.py), which lives
    in a separately imported module; the two share its state through sys.modules."""
    import threading
    import types
    sh = sys.modules.get("_session_mentions_outbox_watch")
    if sh is None:  # unloaded before any route ran: record it, so a later /ping does not start the watcher
        sh = types.ModuleType("_session_mentions_outbox_watch")
        sh.thread, sh.stop = None, threading.Event()
        sys.modules["_session_mentions_outbox_watch"] = sh
    sh.disabled = True
    sh.stop.set()


def register(ctx) -> None:
    sh = sys.modules.get("_session_mentions_outbox_watch")
    if sh is not None:  # registered again after an unload: the next /ping or /outbox may start the watcher
        sh.disabled = False
    on_unload = getattr(ctx, "on_unload", None)
    if callable(on_unload):
        on_unload(stop_watcher)
    ctx.register_tool(name="cross_session", toolset=TOOLSET, schema=SCHEMA, handler=handle, emoji="🔗",
                      description="List/read/message sessions on any Desktop-connected gateway")
    ctx.register_tool(name="cross_task", toolset=TOOLSET, schema=TASK_SCHEMA, handler=handle_task, emoji="🗂️",
                      description="List/read/comment on Kanban tasks on any Desktop-connected gateway")
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("pre_tool_call", _relay_guard)
