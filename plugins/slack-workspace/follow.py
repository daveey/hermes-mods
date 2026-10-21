"""Follower plumbing for engagements (see engage.py):

* ``Runner`` (bot gateway side): wakes the follower agent — one long-lived session per
  engagement in the follower profile — by running ``hermes -p <profile> chat -Q`` with the new
  messages; a tick thread handles kickoff, expiry, the 7-quiet-days auto-end and the retry queue
  for requests to another machine's session (delivered through the session-mentions courier).
* ``Peer``: the follower profile's view of the bot (store + bot token read from the bot profile's
  home, located by ``<HERMES_HOME>/slack-workspace/peer.json``), used by the ``slack_follow`` tools.
* ``engagement_op``: create/list/get/pause/resume/end/update/queue, shared by the tools and
  ``engage_cli.py`` (remote callers).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

if __package__:
    from . import core, engage
else:  # loaded by path (tests, engage_cli.py)
    import importlib.util as _ilu

    def _sib(name, file):
        mod = sys.modules.get(name)
        if mod is None:
            spec = _ilu.spec_from_file_location(name, Path(__file__).with_name(file))
            mod = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
            sys.modules[name] = mod
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod
    core = _sib("slack_workspace_core", "core.py")
    engage = _sib("slack_workspace_engage", "engage.py")

logger = logging.getLogger(__name__)

DEBOUNCE_S = 2.0
TURN_TIMEOUT_S = 900
QUIET_WARN_S = 6 * 86400
# Results coming back as notes are HELD and posted as ONE summary per finished batch: when a sender marks the
# note final (final=true: "this was the last card of the batch"), or when no new result arrived for
# RESULT_QUIET_S (policy.json follower.result_quiet_s). Never one post per result.
RESULT_QUIET_S = 2 * 3600
QUIET_END_S = engage.QUIET_END_DAYS * 86400
MBP_RETRY_S = 300
PICKUP_WAIT_S = 25
# follower.mbp_delivery: "courier" (default) = the Mac's Desktop courier delivers through the session's
# owning socket; a request it couldn't deliver (Desktop not running, Mac asleep) or that waited longer
# than PULL_GRACE_S goes to the Mac's pull cron (slack_follow_pull.py). "courier_only" = no pull
# fallback. "pull" = the pull cron only. Why courier first: see DELIVERY.md in this plugin.
PULL_GRACE_S = 180
MBP_DEVICE = "david-mbp"  # follower.mbp_device: courier name for owner refs that say `@this-device`
LOCAL_ALIASES = ("this-device", "local")


def mbp_mode(policy: dict) -> str:
    mode = ((policy or {}).get("follower") or {}).get("mbp_delivery") or "courier"
    return mode if mode in ("courier", "courier_only", "pull") else "courier"


def target_route(target: str) -> str:
    """The gateway part of `@session:<profile>@<gw>/<id>` ('' when none)."""
    text = (target or "").strip().strip("`\"'")
    while text.startswith("@session:"):
        text = text[len("@session:"):]
    head = text.split("/", 1)[0] if "/" in text else ""
    return head.rsplit("@", 1)[1].strip().lower() if "@" in head else ""


def courier_target(target: str, device: str) -> str:
    """Owner refs made on a Mac say `@this-device`, which is ambiguous from this gateway (every
    Desktop calls its own Mac that): name the Mac."""
    if target_route(target) not in LOCAL_ALIASES:
        return target
    head, sid = target.rsplit("/", 1)
    return f"{head.rsplit('@', 1)[0]}@{device}/{sid}"


# =================================================================================================
# Prompts
# =================================================================================================

def _fmt(rows: list[dict], name: Callable[[str], str], bot_user_id: str) -> list[str]:
    out = []
    for r in rows:
        if r.get("user") == bot_user_id:
            who = "YOU (the bot)"
        elif r.get("bot"):
            who = f"{name(r['user']) if r.get('user') else r['bot']} (bot)"
        else:
            who = name(r.get("user") or "")
        where = f" thread={r['thread_ts']}" if r.get("thread_ts") and r["thread_ts"] != r["ts"] else ""
        text = (r.get("text") or "").strip()
        if len(text) > 3000:
            text = text[:3000] + "…"
        out.append(f"- ts={r['ts']}{where} {who}: {text}")
    return out


def scope_label(e: dict) -> str:
    return f"#{e.get('name') or e['channel']} ({e['channel']})" + (f", thread {e['thread']}" if e["thread"] else "")


META_RE = re.compile(
    r"(?:who(?:'s| is| was)? (?:this|that|it) for|what(?:'s| is| was) (?:this|that)\b|why (?:is|was|are|were|did|do)\b[^.?]{0,40}"
    r"\b(?:post|posted|posting|here)\b|why (?:are )?you (?:post|posting|posted)|who (?:posted|asked)|"
    r"your (?:last|latest|previous) (?:post|message|reply)|last thing you (?:posted|said)|"
    r"why (?:did )?(?:alpha|you) (?:post|say|send))", re.IGNORECASE)


def asks_about_our_posts(rows: list[dict], bot_user_id: str) -> bool:
    return any(r.get("user") != bot_user_id and META_RE.search(r.get("text") or "") for r in rows)


GROUNDING_RULE = ("If anyone asks about YOUR OWN posts (who is this for, what was that, why did you post, what was "
                  "your last post): first read the real thread (slack_read, i.e. conversations.replies) and answer by "
                  "quoting the post by its ts and content. Never answer from memory or assumption; the posts are "
                  "listed under 'Your recent posts' when this prompt has them.")

RESULTS_RULE = ("Results: ONE short batch summary (at most 6 lines, link the PRs), never one post per run; "
                "detail lives in the PR/README.")


def batch_prompt(e: dict, batch: dict, context_rows: list[dict], name: Callable[[str], str],
                 bot_user_id: str, escalations: Optional[list[dict]] = None,
                 own_posts: Optional[list[dict]] = None) -> str:
    lines = [f"[Slack engagement {e['id']} · {scope_label(e)}]",
             "New messages since you last looked (oldest first; ts = reply_to id):"]
    lines += _fmt(batch["trigger"], name, bot_user_id)
    other = [r for r in batch["all"] if r not in batch["trigger"]]
    if other:
        lines += ["", "Also new, no reply expected (your own posts, bots not addressing you):"]
        lines += _fmt(other, name, bot_user_id)
    if context_rows:
        lines += ["", "Earlier in scope (already handled, context only):"]
        lines += _fmt(context_rows, name, bot_user_id)
    if own_posts and asks_about_our_posts(batch["trigger"], bot_user_id):
        lines += ["", "Your recent posts here (someone is asking about them; quote the matching ts/text, and re-read "
                      "the thread with slack_read before answering):"]
        lines += _fmt(own_posts, name, bot_user_id)
        lines += [GROUNDING_RULE]
    esc = [x for x in (escalations or []) if not e["thread"] or x.get("thread") == e["thread"]]
    if esc:
        lines += ["", "Inbox cards you already posted here (reuse the SAME topic to update one):"]
        lines += [f"- topic '{x['topic']}' (thread {x['thread'] or '-'}): {x['title']}  [{x['key']}]"
                  + (f"  — owner pressed {x['decision']}" if x.get("decision") else "") for x in esc[-10:]]
    lines += ["", "Follow the participation rules (skill slack-workspace-bot, 'Engaged channels'). " + GROUNDING_RULE + " "
              "Reply with slack_post(channel, thread_ts, reply_to=<ts>, text); one reply per message. "
              "Be quick: answer simple things right away with ONE slack_post, no preamble. The gateway already "
              "added :eyes: to messages addressed to us and swaps it for :white_check_mark: when you post; "
              "do not react for that. Use slack_react only for other reactions. Anything needing the owner → slack_escalate (nothing posted in the "
              "channel). Work needing the Mac → slack_defer_to_mbp. If nothing needs you, post nothing. "
              "End with a one-line note of what you did (your chat is the activity log)."]
    return "\n".join(lines)


def kickoff_prompt(e: dict, self_ref: str = "") -> str:
    parts = [f"[Slack engagement {e['id']} · {scope_label(e)} · kickoff]",
             "A Hermes session engaged the owner's Slack bot here and handed this channel to you. "
             "You are the follower: from now on every new message in scope reaches you, and the bot's "
             "ambient mode stays out. Load skill slack-workspace-bot and follow 'Engaged channels'.",
             "", "## Brief", e.get("brief") or "(none)"]
    if e.get("context"):
        parts += ["", "## Handoff from the originating session", e["context"]]
    meta = []
    if e.get("owner"):
        meta.append(f"- Originating session (use for Mac-only work via slack_defer_to_mbp): {e['owner']}")
    if e.get("kanban_ids"):
        meta.append(f"- Linked Kanban cards: {', '.join(e['kanban_ids'])}")
    if e.get("budget"):
        meta.append(f"- Budget: {e['budget']}")
    if e.get("expires"):
        meta.append(f"- Ends: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(e['expires']))}")
    if self_ref:
        meta.append(f"- Your own session (for others to message you): {self_ref}")
    if meta:
        parts += ["", "## Links"] + meta
    parts += ["", "## Standing rules", "- " + RESULTS_RULE, "- " + GROUNDING_RULE]
    parts += ["", "## Now",
              f"1. Read the recent history (slack_read channel={e['channel']}"
              + (f" thread_ts={e['thread']}" if e["thread"] else "") + ").",
              "2. Post ONE short intro line with slack_post (no reply_to"
              + (f", thread_ts={e['thread']}" if e["thread"] else "")
              + ") saying you are following along for the owner and what you can help with. "
              "No 'Posting for <owner>' prefix.",
              "3. If something in the history is clearly waiting on us, handle it per the rules."]
    return "\n".join(parts)


# =================================================================================================
# Engagement ops (tools + CLI)
# =================================================================================================

DEFAULT_FOLLOWER = "slack-follow"


def default_follower(engs) -> str:
    """Follower profile from the bot's policy.json (``{"follower": {"profile": ...}}``)."""
    try:
        return (engs.store.policy().get("follower") or {}).get("profile") or DEFAULT_FOLLOWER
    except Exception:
        return DEFAULT_FOLLOWER


def parse_until(v: Any, now: Optional[float] = None) -> Optional[float]:
    now = time.time() if now is None else now
    if v in (None, "", 0):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([hdw])", s)
    if m:
        return now + float(m.group(1)) * {"h": 3600, "d": 86400, "w": 604800}[m.group(2)]
    try:
        return float(s)
    except ValueError:
        pass
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _public(e: Optional[dict]) -> Optional[dict]:
    if not e:
        return None
    out = {k: e.get(k) for k in ("id", "channel", "thread", "name", "state", "owner", "kanban_ids",
                                 "budget", "follower_profile", "follower_session", "last_handled_ts",
                                 "expires", "last_activity")}
    out["test"] = bool(e.get("test"))
    out["brief"] = (e.get("brief") or "")[:400]
    return out


def _with_test(engs, e: Optional[dict]) -> Optional[dict]:
    if e:
        e = dict(e, test=engs.is_test(e["id"]))
    return e


def engagement_op(engs: "engage.Engagements", op: str, args: dict) -> dict:
    op = (op or "list").strip()
    eid = (args.get("id") or args.get("engagement") or "").strip()
    pub = lambda e: _public(_with_test(engs, e))  # noqa: E731
    if op == "test_inbox":
        # Global toggle: do test engagements' escalations reach the owner's Inbox? (default off)
        if args.get("enabled") is not None:
            engs.store.update_policy(test_inbox=bool(args["enabled"]))
        return {"ok": True, "test_inbox": engs.test_inbox()}
    if op == "mbp_pull":  # the other machine fetches requests for its sessions (works across sleep)
        mode = mbp_mode(engs.store.policy())
        if mode == "courier_only":
            return {"ok": True, "requests": [], "mode": mode}
        got = engs.mbp_claim(courier_grace=PULL_GRACE_S if mode == "courier" else None)
        return {"ok": True, "mode": mode,
                "requests": [{k: r[k] for k in ("id", "engagement", "target", "message", "channel", "thread")}
                             for r in got]}
    if op == "mbp_done":
        if not engs.mbp_get(args.get("id") or ""):
            return {"ok": False, "error": "no such request"}
        engs.mbp_attempt(args["id"], ok=bool(args.get("ok")), error=str(args.get("error") or ""),
                         retry_in=float(args.get("retry_in") if args.get("retry_in") is not None else MBP_RETRY_S))
        return {"ok": True}
    if op == "create":
        e = engs.create(args["channel"], args.get("brief") or "", thread=args.get("thread") or "",
                        name=args.get("name") or "", context=args.get("context") or "",
                        owner=args.get("owner") or "", kanban_ids=args.get("kanban_ids") or [],
                        budget=args.get("budget") or "", until=parse_until(args.get("until")),
                        private=bool(args.get("private")),
                        follower_profile=args.get("follower_profile") or default_follower(engs),
                        last_handled_ts=args.get("last_handled_ts"))
        if args.get("test"):
            engs.set_test(e["id"], True)
        return {"ok": True, "engagement": pub(e)}
    if op == "list":
        states = args.get("states") or ("active", "paused")
        return {"ok": True, "engagements": [pub(e) for e in engs.list(states=states)]}
    if not eid:
        return {"ok": False, "error": f"{op} needs id"}
    if not engs.get(eid):
        return {"ok": False, "error": f"no engagement {eid}"}
    if op == "message":
        # A result/answer for the follower from another session (e.g. the Mac session it deferred to).
        # Results are held and posted as one batch summary (see notes_due); final=true releases the batch now.
        if not (args.get("text") or "").strip():
            return {"ok": False, "error": "message needs text"}
        final = bool(args.get("final"))
        return {"ok": True, "queued": engs.add_note(eid, args["text"], args.get("sender") or "", final=final),
                "note": ("the follower gets the batch within ~1 minute and posts ONE summary" if final else
                         "held with the other results of this batch; the follower posts ONE summary when a note "
                         "with final=true arrives or no result comes for 2 h")}
    if op == "get":
        e = engs.get(eid)
        return {"ok": True, "engagement": pub(e) | {"brief": e.get("brief"), "context": e.get("context")},
                "mbp_queue": engs.mbp_list(eid)}
    if op in ("pause", "end"):
        return {"ok": True, "engagement": pub(engs.set_state(eid, "paused" if op == "pause" else "ended"))}
    if op == "resume":
        # Skip what was said while paused rather than answering a backlog.
        return {"ok": True, "engagement": pub(engs.update(eid, state="active", last_handled_ts=f"{time.time():.6f}",
                                                              last_activity=time.time()))}
    if op == "update":
        fields = {k: args[k] for k in ("brief", "context", "kanban_ids", "budget", "name", "owner") if k in args}
        if "until" in args:
            fields["expires"] = parse_until(args["until"])
        if args.get("test") is not None:
            engs.set_test(eid, bool(args["test"]))
        return {"ok": True, "engagement": pub(engs.update(eid, **fields))}
    return {"ok": False, "error": f"unknown op {op}"}


# =================================================================================================
# Runner (bot gateway side)
# =================================================================================================

# Model routing: the follower profile defaults to a fast model (Sonnet). Turns that must look things up or
# reason over evidence (repo / kanban / numbers, long or multi-message batches) run on RESEARCH_MODEL instead, so
# speed on quick replies never costs grounding on research ones. policy.json: follower.research_model
# ("" turns routing off), follower.research_min_chars, follower.research_min_messages.
RESEARCH_MODEL = "claude-opus-5-5"
RESEARCH_MIN_CHARS = 400
RESEARCH_MIN_MESSAGES = 3
_RESEARCH_RE = re.compile(
    r"(?:\bt_[0-9a-f]{6,}\b|\bkanban\b|\bgithub\b|\brepo\b|\bcommits?\b|\bpull request\b|\bPR\s*#?\d+|\bbranch(?:es)?\b"
    r"|\w+/\w+\.\w{1,4}\b|\w+\.(?:py|md|ts|rs|yaml|json)\b|`[^`]+`|\bhow many\b|\bhow does\b|\bhow did\b"
    r"|\bexplain\b|\binvestigat|\bsummari[sz]e|\bcompare\b|\bbenchmark|\bresults?\b|\bnumbers?\b|\bmeasure"
    r"|\bexperiment|\bscor(?:e|ing)\b|\bdata\b|\bwhy did\b|\bwhat(?:'s| is| are) the\b|\blook (?:into|at|up)\b)",
    re.IGNORECASE)


def research_model(policy: dict, batch: dict) -> str:
    """Model for a batch turn: the research model when the triggering messages look like lookups, else '' (the
    follower profile's default). Pure; the policy's follower.research_model='' disables routing."""
    fcfg = (policy or {}).get("follower") or {}
    model = fcfg.get("research_model", RESEARCH_MODEL)
    if not model:
        return ""
    trig = batch.get("trigger") or []
    min_chars = int(fcfg.get("research_min_chars") or RESEARCH_MIN_CHARS)
    min_msgs = int(fcfg.get("research_min_messages") or RESEARCH_MIN_MESSAGES)
    texts = [(r.get("text") or "") for r in trig]
    if len(trig) >= min_msgs or sum(len(t) for t in texts) >= min_chars:
        return model
    return model if any(_RESEARCH_RE.search(t) for t in texts) else ""


def hermes_bin() -> list[str]:
    exe = shutil.which("hermes") or str(Path(sys.executable).with_name("hermes"))
    return [exe]


def parse_session_id(output: str) -> str:
    m = re.findall(r"session_id:\s*([\w.-]+)", output or "")
    return m[-1] if m else ""


class Runner:
    def __init__(self, store, engs, *, token: Callable[[], str], bot_user_id: Callable[[], str],
                 name: Callable[[str], str], root_home: Path, spawn: Optional[Callable] = None,
                 inbox_post: Optional[Callable] = None, self_gateway: str = "",
                 inbox_outcome: Optional[Callable] = None, policy: Optional[Callable[[], dict]] = None):
        self.store, self.engs = store, engs
        self.policy = policy or (lambda: {})
        self.token, self.bot_user_id, self.name = token, bot_user_id, name
        self.root_home = Path(root_home)
        self.spawn = spawn or self._spawn
        self.inbox_post = inbox_post
        self.inbox_outcome = inbox_outcome  # (key, profile) -> inbox outcome dict | None
        self.self_gateway = self_gateway
        self._busy: set[str] = set()
        self._again: set[str] = set()
        self._courier_backoff: dict[str, float] = {}
        self._lock = threading.Lock()
        self._ticker: Optional[threading.Thread] = None

    # ---- follower turns -------------------------------------------------------------------------
    def session_title(self, e: dict) -> str:
        return f"Slack {('#' + e['name']) if e.get('name') else e['channel']} · {e['id']}"

    def _spawn(self, e: dict, prompt: str, model: str = "") -> tuple[int, str]:
        qdir = self.root_home / "cache" / "slack-follow"
        qdir.mkdir(parents=True, exist_ok=True)
        qf = qdir / f"{e['id']}-{uuid.uuid4().hex[:6]}.txt"
        qf.write_text(prompt)
        # The follower is a named profile of THIS gateway: resolve `-p` against the gateway root, not
        # the bot profile's home (or the process HOME, which may be a per-profile home dir).
        env = {k: v for k, v in os.environ.items() if not k.startswith("HERMES_SESSION_")}
        env["HERMES_HOME"] = str(self.root_home)
        cmd = hermes_bin() + ["-p", e.get("follower_profile") or DEFAULT_FOLLOWER, "chat", "-Q",
                              "--query-file", str(qf), "-c", self.session_title(e), "--create-if-missing",
                              "--yolo", "--pass-session-id"] + (["-m", model] if model else [])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=TURN_TIMEOUT_S, env=env,
                               cwd=str(self.root_home))
            return r.returncode, (r.stdout or "") + (r.stderr or "")[-2000:]
        except subprocess.TimeoutExpired as exc:
            return 124, f"timeout: {exc}"
        finally:
            try:
                qf.unlink()
            except OSError:
                pass

    def self_ref(self, e: dict) -> str:
        sid = e.get("follower_session")
        return f"@session:{e.get('follower_profile') or DEFAULT_FOLLOWER}@{self.self_gateway}/{sid}" if sid else ""

    def run_once(self, eid: str) -> bool:
        """One follower turn for *eid* if it has work. Returns True when a turn ran."""
        e = self.engs.get(eid)
        if not e or e["state"] != "active":
            return False
        bot = self.bot_user_id()
        if e.get("kickoff"):
            self._join_if_public(e)
            rc, out = self.spawn(e, kickoff_prompt(e))
            sid = parse_session_id(out)
            self.engs.update(eid, kickoff=0 if rc == 0 else 1, **({"follower_session": sid} if sid else {}))
            if rc != 0:  # stop here; the next tick retries (no tight retry loop)
                logger.warning("slack-follow %s kickoff failed rc=%s: %s", eid, rc, out[-500:])
                return False
            return True
        notes = self.engs.notes(eid)
        if notes and self.notes_due(notes):
            lines = [f"[Slack engagement {eid} · {scope_label(e)} · {len(notes)} result note(s) for you]"]
            for n in notes:
                lines += [f"From {n.get('sender') or 'another session'}:", n["text"], ""]
            lines += ["This is one finished batch. Post ONE short summary in the waiting thread with a single "
                      "slack_post: at most 6 lines, headline first, link the PRs, no per-run posts, no 'Posting for' "
                      "prefix; detail stays in the PR/README. If a note is not a result (a question, an answer for "
                      "someone), handle it per the participation rules. " + GROUNDING_RULE,
                      "End with a one-line activity note."]
            rc, out = self.spawn(e, "\n".join(lines))
            if rc != 0:
                logger.warning("slack-follow %s note turn failed rc=%s: %s", eid, rc, out[-500:])
                return False
            self.engs.take_notes(eid, len(notes))
            return True
        batch = self.engs.unhandled(eid, bot_user_id=bot)
        if not batch["newest"]:
            return False
        if not batch["trigger"]:
            self.engs.mark_handled(eid, batch["newest"])
            return False
        first = min(float(r["ts"]) for r in batch["all"])
        ctx = [r for r in self.store.recent(e["channel"], first - 7 * 86400, limit=40)
               if self.engs.in_scope(e, r) and float(r["ts"]) < first][-15:]
        t0 = time.time()
        try:
            model = research_model(self.policy(), batch)
        except Exception as exc:  # routing must never block a reply
            logger.warning("slack-follow %s model routing failed: %s", eid, exc)
            model = ""
        own = self._own_posts(e, bot, first) if asks_about_our_posts(batch["trigger"], bot) else None
        prompt = batch_prompt(e, batch, ctx, self.name, bot, self._escalations(e), own_posts=own)
        rc, out = self.spawn(e, prompt, model=model) if model else self.spawn(e, prompt)
        logger.info("slack-follow: %s turn for %d message(s) rc=%s in %.0fs (waited %.1fs from message to spawn)%s",
                    eid, len(batch["trigger"]), rc, time.time() - t0, t0 - first,
                    f" model={model}" if model else "")
        sid = parse_session_id(out)
        if sid and sid != e.get("follower_session"):
            self.engs.update(eid, follower_session=sid)
        if rc != 0:
            logger.warning("slack-follow %s turn failed rc=%s: %s", eid, rc, out[-500:])
            return False
        self.engs.mark_handled(eid, batch["newest"])
        return True

    def notes_due(self, notes: list[dict], now: Optional[float] = None) -> bool:
        """Release held result notes? Yes when one is marked final or the newest is RESULT_QUIET_S old."""
        if not notes:
            return False
        if any(n.get("final") for n in notes):
            return True
        now = time.time() if now is None else now
        quiet = float((self.policy().get("follower") or {}).get("result_quiet_s") or RESULT_QUIET_S)
        return now - max(float(n.get("at") or 0) for n in notes) >= quiet

    def _own_posts(self, e: dict, bot: str, since: float) -> list[dict]:
        rows = [r for r in self.store.recent(e["channel"], since - 2 * 86400, limit=200)
                if r.get("user") == bot and self.engs.in_scope(e, r)]
        return rows[-8:]

    def _escalations(self, e: dict) -> list[dict]:
        """This channel's Inbox cards, each tagged with the owner's resolved decision on it (e.g. ignore)."""
        out = []
        for x in self.engs.escalations(e["channel"]):
            d = decided(lambda k: self.inbox_outcome(k, e.get("follower_profile")), x["key"]) if self.inbox_outcome else {}
            out.append(dict(x, decision=d.get("outcome")) if d else x)
        return out

    def _join_if_public(self, e: dict) -> None:
        if e.get("private"):
            return
        try:
            core.call(self.token(), "conversations.join", {"channel": e["channel"]})
        except Exception as exc:  # private channel / already in: fine
            logger.info("slack-follow join %s: %s", e["channel"], exc)

    def kick(self, eid: str) -> None:
        with self._lock:
            if eid in self._busy:
                self._again.add(eid)
                return
            self._busy.add(eid)
        threading.Thread(target=self._loop, args=(eid,), daemon=True, name=f"slack-follow-{eid}").start()

    def _loop(self, eid: str) -> None:
        try:
            time.sleep(DEBOUNCE_S)
            for _ in range(20):
                with self._lock:
                    self._again.discard(eid)
                ran = self.run_once(eid)
                with self._lock:
                    if not ran and eid not in self._again:
                        break
        except Exception:
            logger.warning("slack-follow runner %s crashed", eid, exc_info=True)
        finally:
            with self._lock:
                self._busy.discard(eid)
                again = eid in self._again
                self._again.discard(eid)
            if again:
                self.kick(eid)

    # ---- tick -----------------------------------------------------------------------------------
    def start(self, every: float = 60) -> None:
        if self._ticker and self._ticker.is_alive():
            return

        def loop():
            while True:
                try:
                    self.tick()
                except Exception:
                    logger.warning("slack-follow tick failed", exc_info=True)
                time.sleep(every)
        self._ticker = threading.Thread(target=loop, daemon=True, name="slack-follow-tick")
        self._ticker.start()

    def tick(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        bot = self.bot_user_id()
        for e in self.engs.list(states=("active",)):
            if e.get("expires") and e["expires"] <= now:
                self.engs.set_state(e["id"], "ended")
                continue
            quiet = now - float(e.get("last_activity") or e.get("created") or now)
            if quiet >= QUIET_WARN_S and e.get("warned_quiet"):
                # the owner's press on the quiet-end card wins over the auto-end, even when this tick is late
                if self._quiet_decision(e, now):
                    continue
                quiet = now - float(self.engs.get(e["id"]).get("last_activity") or now)
            if quiet >= QUIET_END_S:
                self.engs.set_state(e["id"], "ended")
                continue
            if quiet >= QUIET_WARN_S and not e.get("warned_quiet") and self.inbox_post:
                try:
                    self.inbox_post(
                        title=f"Slack {scope_label(e)} quiet 6 days; follower ends tomorrow",
                        body=f"Engagement `{e['id']}` has had no activity for 6 days. It auto-ends in 24 h.",
                        accept_prompt=f"Keep following: call slack_engagements(action='update', id='{e['id']}') "
                                      f"after resetting activity with action='resume', id='{e['id']}'.",
                        key=engage.inbox_key(e["channel"], e["thread"], "quiet-end"), profile=e.get("follower_profile"),
                        type="slack-quiet-end", fields={"engagement": e["id"]})
                except Exception:
                    logger.warning("slack-follow quiet card failed", exc_info=True)
                self.engs.update(e["id"], warned_quiet=now)
            if e.get("kickoff") or self.notes_due(self.engs.notes(e["id"]), now) or self.engs.unhandled(e["id"], bot_user_id=bot)["trigger"]:
                self.kick(e["id"])
        self.deliver_mbp(now)

    def _quiet_decision(self, e: dict, now: float) -> bool:
        """Honour the owner's press on the quiet-end card: Keep following restarts the quiet clock (and re-arms the
        warning), Let it end ends the engagement now. True when the engagement was ended."""
        if not self.inbox_outcome:
            return False
        key = engage.inbox_key(e["channel"], e["thread"], "quiet-end")
        try:
            o = self.inbox_outcome(key, e.get("follower_profile"))
        except Exception:
            logger.warning("slack-follow quiet outcome read failed", exc_info=True)
            return False
        act = (o or {}).get("outcome") if (o or {}).get("state") == "resolved" else None
        warned = float(e.get("warned_quiet") or 0)
        if act not in ("keep", "let_end") or float(o.get("at") or 0) < warned:  # an older card's decision
            return False
        if act == "let_end":
            self.engs.set_state(e["id"], "ended")
            return True
        self.engs.update(e["id"], last_activity=now, warned_quiet=0)
        return False

    # ---- queue to another machine's session (session-mentions courier) ------------------------
    def deliver_mbp(self, now: Optional[float] = None) -> None:
        # See mbp_mode(). The courier claims each request atomically, so it and the pull cron never
        # both deliver one; a failed courier send is due for the puller at once (fallback) and for
        # the courier again after MBP_RETRY_S. An unknown outcome is never resent.
        policy = self.store.policy()
        mode = mbp_mode(policy)
        if mode == "pull":
            return
        device = (policy.get("follower") or {}).get("mbp_device") or MBP_DEVICE
        now = time.time() if now is None else now
        for q in self.engs.due_mbp(now):
            if self._courier_backoff.get(q["id"], 0) > now:
                continue
            if not self.engs.mbp_claim_one(q["id"], now=now, lease=PICKUP_WAIT_S + 300):
                continue
            target = courier_target(q["target"], device)
            res = self._courier_send(target, q["message"])
            ok, err = res[0], res[1]
            if len(res) > 2 and res[2]:
                self.engs.mbp_uncertain(q["id"], err)
                logger.warning("slack-follow %s to %s: outcome unknown (%s); not resending", q["id"], target, err)
                continue
            self.engs.mbp_attempt(q["id"], ok=ok, error=f"courier: {err}" if err else "",
                                  retry_in=0 if mode == "courier" else MBP_RETRY_S)
            if ok:
                self._courier_backoff.pop(q["id"], None)
                logger.info("slack-follow delivered %s to %s", q["id"], target)
            else:
                self._courier_backoff[q["id"]] = now + MBP_RETRY_S

    def _courier_send(self, target: str, message: str) -> tuple[bool, str, bool]:
        """(ok, error, uncertain). uncertain = the Desktop may have delivered it."""
        base = self.root_home / "session_mentions"
        for sub in ("outbox", "claimed", "results"):
            (base / sub).mkdir(parents=True, exist_ok=True)
        rid = uuid.uuid4().hex
        req = {"id": rid, "action": "send", "route": target_route(target),
               "args": {"target": target, "message": message},
               "wait_for_reply": 0, "sender": {"session_id": "", "title": "slack follower", "profile": DEFAULT_FOLLOWER},
               "created_at": time.time(), "ttl": 600}
        out = base / "outbox" / f"{rid}.json"
        tmp = out.with_name(f".{rid}.tmp")
        tmp.write_text(json.dumps(req))
        os.replace(tmp, out)
        res = base / "results" / f"{rid}.json"
        deadline = time.time() + PICKUP_WAIT_S
        while time.time() < deadline + (120 if not out.exists() else 0):
            if res.exists():
                try:
                    data = json.loads(res.read_text())
                except ValueError:
                    time.sleep(0.3)
                    continue
                res.unlink(missing_ok=True)
                ok = bool(data.get("ok"))
                uncertain = not ok and (data.get("status") == "unknown" or data.get("delivered") is None
                                        and data.get("retry_safe") is False)
                return ok, str(data.get("error") or data.get("status") or ""), uncertain
            if out.exists() and time.time() > deadline:
                try:
                    out.unlink()
                except FileNotFoundError:  # claimed just now: keep waiting for the result
                    deadline += 120
                    continue
                return False, "Desktop courier offline (Mac asleep or Desktop not running)", False
            time.sleep(0.5)
        return False, "courier claimed it but sent no result", True


# =================================================================================================
# Posting + escalation (follower tools)
# =================================================================================================

class Poster:
    """slack_post with the engagement invariants: only inside an active engagement, exactly one
    reply per message, and at most ``engage.BOT_TURN_CAP`` replies to bots per thread until a human
    speaks again."""

    def __init__(self, engs, api: Callable[[str, Optional[dict]], dict], bot_user_id: Callable[[], str]):
        self.engs, self.api, self.bot_user_id = engs, api, bot_user_id

    def _message(self, channel: str, ts: str) -> Optional[dict]:
        rows = [r for r in self.engs.store.recent(channel, float(ts) - 0.0001, limit=5) if r["ts"] == ts]
        return rows[0] if rows else None

    def post(self, channel: str, text: str, thread_ts: str = "", reply_to: str = "",
             owner_approved: bool = False) -> dict:
        """``owner_approved``: the owner pressed Do it on an Inbox card (the bot cap is the follower's
        own brake, not a limit on the owner)."""
        thread_ts = thread_ts or ""
        e = self.engs.active_for(channel, thread_ts or reply_to or "")
        if not e:
            return {"ok": False, "error": "not_engaged: no active engagement covers this channel/thread "
                                          "(it may have been paused or ended); nothing was posted"}
        if e["thread"] and not thread_ts:
            thread_ts = e["thread"]
        if not (text or "").strip():
            return {"ok": False, "error": "empty text"}
        to_bot = False
        if reply_to:
            m = self._message(channel, reply_to)
            to_bot = bool(m and m.get("bot") and m.get("user") != self.bot_user_id())
            if to_bot and not owner_approved and not self.engs.bot_turn_allowed(channel, thread_ts or reply_to):
                return {"ok": False, "error": f"bot_loop_cap: {engage.BOT_TURN_CAP} bot-to-bot turns in this "
                                              "thread already; stop replying and post one Inbox card with "
                                              "slack_escalate (topic 'bot loop') instead"}
            if not self.engs.claim_reply(channel, reply_to):
                return {"ok": False, "error": "already_replied: this message already has a reply",
                        "posted_ts": self.engs.reply_for(channel, reply_to)}
        try:
            out = self.api("chat.postMessage", {"channel": channel, "text": text, "thread_ts": thread_ts or None,
                                                "unfurl_links": False, "unfurl_media": False})
        except Exception as exc:
            if reply_to:
                self.engs.release_reply(channel, reply_to)
            return {"ok": False, "error": f"post failed: {exc}"}
        ts = out.get("ts", "")
        verified = False
        try:
            r = self.api("conversations.replies", {"channel": channel, "ts": thread_ts or ts, "limit": 200})
            verified = any(x.get("ts") == ts for x in r.get("messages") or [])
        except Exception:
            pass
        if reply_to:
            self.engs.record_reply(channel, reply_to, ts)
            if to_bot:
                self.engs.bump_bot_turns(channel, thread_ts or reply_to)
        self.engs.update(e["id"], last_activity=time.time())
        if reply_to:  # done: swap the instant :eyes: for :white_check_mark: (best effort)
            for method, name in (("reactions.remove", ACK_EMOJI), ("reactions.add", DONE_EMOJI)):
                try:
                    self.api(method, {"channel": channel, "timestamp": reply_to, "name": name})
                except Exception:
                    pass
        return {"ok": True, "ts": ts, "thread_ts": thread_ts or ts, "verified": verified, "engagement": e["id"]}


ACK_EMOJI, DONE_EMOJI = "eyes", "white_check_mark"
# Plain-text address: "alpha -- ...", "alpha, ...", "hey alpha ...". Not "alphashot", "alpha-punch-fleece".
_ALPHA_NAME = re.compile(r"^\W{0,3}(?:(?:hey|hi|hello|yo|ok|okay|thanks|thx)[ ,]+)?alpha(?![\w-])(?!\s*[-\u2013\u2014]\w)",
                         re.I)


def names_alpha(text: str) -> bool:
    return bool(_ALPHA_NAME.match(text or ""))



def ack_if_addressed(store, api: Callable[[str, Optional[dict]], dict], event: dict, bot_user_id: str) -> bool:
    """Deterministic :eyes: (no LLM) on a message addressed to the bot: an @-mention, a DM, or a reply in a
    thread the bot already posted in. Unaddressed chatter gets nothing. Never raises."""
    try:
        if not engage.is_trigger(event, bot_user_id) or event.get("subtype"):
            return False
        ch, ts = event.get("channel") or "", event.get("ts") or ""
        thread = event.get("thread_ts") or ""
        addressed = engage.mentions(event.get("text") or "", bot_user_id) or ch.startswith("D") \
            or names_alpha(event.get("text") or "")
        if not addressed and thread and thread != ts:
            addressed = any(r.get("user") == bot_user_id and r.get("thread_ts") == thread
                            for r in store.recent(ch, float(thread) - 0.0001, limit=200))
        if not addressed:
            return False
        api("reactions.add", {"channel": ch, "timestamp": ts, "name": ACK_EMOJI})
        return True
    except Exception as exc:
        if "already_reacted" not in str(exc):
            logger.info("slack-follow ack failed: %s", exc)
        return False


def decided(inbox_outcome: Optional[Callable], key: str) -> dict:
    """The owner's resolved decision on the newest card with this key ({} when open, archived, none, or no reader).
    Never raises: an unreadable card is simply undecided."""
    try:
        o = inbox_outcome(key) if inbox_outcome else None
    except Exception:
        return {}
    return o if o and o.get("state") == "resolved" and o.get("outcome") else {}


def escalate(engs, inbox_post: Callable, *, channel: str, thread_ts: str, topic: str, title: str, body: str,
             reply_text: str = "", reply_to: str = "", action: str = "", permalink: str = "",
             inbox_outcome: Optional[Callable] = None) -> dict:
    """One Inbox card per (channel, thread, topic); a repeat updates the open card. A topic the owner pressed
    Ignore on is not raised again (``ignored``): a follow-up on it would only re-open the card he dismissed."""
    e = engs.active_for(channel, thread_ts or reply_to or "") or {}
    key = engage.inbox_key(channel, thread_ts, topic)
    if decided(inbox_outcome, key).get("outcome") == "ignore":
        return {"ok": True, "key": key, "ignored": True,
                "note": "the owner pressed Ignore on this topic's card; nothing was posted anywhere. Do not raise "
                        "this topic again unless he asks."}
    where = f"<#{channel}>" + (f" thread {thread_ts}" if thread_ts else "")
    if reply_text:
        accept = (f"Post this reply as the bot with slack_post(channel='{channel}', thread_ts='{thread_ts}'"
                  + (f", reply_to='{reply_to}'" if reply_to else "") + ", text=<the text below>), then confirm "
                  f"the returned ts.\n\nReply text:\n{reply_text}")
        if action:
            accept = f"{action}\n\nThen: {accept}"
    elif action:
        accept = action
    else:
        return {"ok": False, "error": "give reply_text (drafted answer) and/or action (exact instruction)"}
    if e and engs.is_test(e["id"]) and not engs.test_inbox():
        card = {"title": title, "body": body, "accept_prompt": accept}
        engs.record_escalation(key, channel=channel, thread=thread_ts, topic=topic, title=title, suppressed=card)
        return {"ok": True, "key": key, "suppressed": True,
                "note": "test engagement: escalation recorded, NOT sent to the owner's Inbox (test_inbox is off). "
                        "Post nothing in the channel, as for a real escalation."}
    full_body = (f"**Where:** {where}" + (f" ({permalink})" if permalink else "")
                 + (f" · engagement `{e['id']}`" if e else "") + f"\n\n{body}")
    typed = ({"type": "slack-reply", "fields": {"channel": channel, "thread": thread_ts or "",
                                                "reply_to": reply_to or "", "reply": reply_text}}
             if reply_text else {"type": "slack-action", "fields": {}})
    res = inbox_post(title=title, body=full_body, accept_prompt=accept, key=key,
                     poster=f"slack · {('#' + e['name']) if e.get('name') else channel}", **typed)
    engs.record_escalation(key, channel=channel, thread=thread_ts, topic=topic, title=title,
                           session_id=(res or {}).get("session_id") or "")
    return {"ok": True, "key": key, **res}


# =================================================================================================
# Peer (follower profile side)
# =================================================================================================

def read_env_value(path: Path, key: str) -> str:
    try:
        for line in Path(path).read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


class Peer:
    """Config: ``<HERMES_HOME>/slack-workspace/peer.json``:
    ``{"peer_home": "/path/to/bot/profile", "self_gateway": "<this gateway's name>"}`` (same host as the bot) or
    ``{"remote_cmd": ["python3", "/path/to/transport.py"]}`` (engagement ops only, run elsewhere)."""

    def __init__(self, home: Path):
        self.home = Path(home)
        self.cfg_path = self.home / "slack-workspace" / "peer.json"
        self._store = None
        self._engs = None
        self._bot: Optional[str] = None

    def cfg(self) -> dict:
        try:
            return json.loads(self.cfg_path.read_text())
        except (OSError, ValueError):
            return {}

    @property
    def local(self) -> bool:
        return bool(self.cfg().get("peer_home"))

    @property
    def remote(self) -> bool:
        return bool(self.cfg().get("remote_cmd")) and not self.local

    def peer_home(self) -> Path:
        return Path(self.cfg()["peer_home"])

    def store(self):
        if self._store is None:
            self._store = core.Store(self.peer_home() / "slack-workspace")
            self._engs = engage.Engagements(self._store)
        return self._store

    def engs(self) -> "engage.Engagements":
        self.store()
        return self._engs  # type: ignore[return-value]

    def token(self) -> str:
        tok = read_env_value(self.peer_home() / ".env", "SLACK_BOT_TOKEN")
        if not tok:
            raise RuntimeError("no SLACK_BOT_TOKEN in the bot profile's .env")
        return tok

    def api(self, method: str, params: Optional[dict] = None) -> dict:
        return core.call(self.token(), method, params)

    def bot_user_id(self) -> str:
        if not self._bot:
            self._bot = self.api("auth.test").get("user_id", "")
        return self._bot or ""

    def remote_op(self, op: str, args: dict) -> dict:
        cmd = self.cfg()["remote_cmd"]
        r = subprocess.run(cmd if isinstance(cmd, list) else [cmd], input=json.dumps({"op": op, "args": args}),
                           capture_output=True, text=True, timeout=240)
        out = (r.stdout or "").strip().splitlines()
        try:
            return json.loads(out[-1])
        except (IndexError, ValueError):
            return {"ok": False, "error": f"remote transport failed (rc={r.returncode}): "
                                          f"{(r.stderr or r.stdout or '')[-400:]}"}


def b64json(obj: Any) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()
