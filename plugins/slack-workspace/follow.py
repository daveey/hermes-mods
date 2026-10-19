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
QUIET_END_S = engage.QUIET_END_DAYS * 86400
MBP_RETRY_S = 300
PICKUP_WAIT_S = 25


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


def batch_prompt(e: dict, batch: dict, context_rows: list[dict], name: Callable[[str], str],
                 bot_user_id: str, escalations: Optional[list[dict]] = None) -> str:
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
    esc = [x for x in (escalations or []) if not e["thread"] or x.get("thread") == e["thread"]]
    if esc:
        lines += ["", "Inbox cards you already posted here (reuse the SAME topic to update one):"]
        lines += [f"- topic '{x['topic']}' (thread {x['thread'] or '-'}): {x['title']}  [{x['key']}]" for x in esc[-10:]]
    lines += ["", "Follow the participation rules (skill slack-workspace-bot, 'Engaged channels'). "
              "Reply with slack_post(channel, thread_ts, reply_to=<ts>, text); one reply per message. "
              "Be quick: answer simple things right away; if a good answer needs more than ~30 s of research, "
              "first slack_react 'eyes' on the message, then reply when you have it. "
              "React with slack_react. Anything needing the owner → slack_escalate (nothing posted in the "
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
        if not (args.get("text") or "").strip():
            return {"ok": False, "error": "message needs text"}
        return {"ok": True, "queued": engs.add_note(eid, args["text"], args.get("sender") or ""),
                "note": "the follower gets it within ~1 minute and posts what the channel needs"}
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

def hermes_bin() -> list[str]:
    exe = shutil.which("hermes") or str(Path(sys.executable).with_name("hermes"))
    return [exe]


def parse_session_id(output: str) -> str:
    m = re.findall(r"session_id:\s*([\w.-]+)", output or "")
    return m[-1] if m else ""


class Runner:
    def __init__(self, store, engs, *, token: Callable[[], str], bot_user_id: Callable[[], str],
                 name: Callable[[str], str], root_home: Path, spawn: Optional[Callable] = None,
                 inbox_post: Optional[Callable] = None, self_gateway: str = ""):
        self.store, self.engs = store, engs
        self.token, self.bot_user_id, self.name = token, bot_user_id, name
        self.root_home = Path(root_home)
        self.spawn = spawn or self._spawn
        self.inbox_post = inbox_post
        self.self_gateway = self_gateway
        self._busy: set[str] = set()
        self._again: set[str] = set()
        self._lock = threading.Lock()
        self._ticker: Optional[threading.Thread] = None

    # ---- follower turns -------------------------------------------------------------------------
    def session_title(self, e: dict) -> str:
        return f"Slack {('#' + e['name']) if e.get('name') else e['channel']} · {e['id']}"

    def _spawn(self, e: dict, prompt: str) -> tuple[int, str]:
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
                              "--yolo", "--pass-session-id"]
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
        if notes:
            lines = [f"[Slack engagement {eid} · {scope_label(e)} · message for you]"]
            for n in notes:
                lines += [f"From {n.get('sender') or 'another session'}:", n["text"], ""]
            lines += ["Act on it per the participation rules: usually post the headline in the thread that was "
                      "waiting (slack_post), then end with a one-line activity note."]
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
        rc, out = self.spawn(e, batch_prompt(e, batch, ctx, self.name, bot, self.engs.escalations(e["channel"])))
        logger.info("slack-follow: %s turn for %d message(s) rc=%s in %.0fs", eid, len(batch["trigger"]), rc,
                    time.time() - t0)
        sid = parse_session_id(out)
        if sid and sid != e.get("follower_session"):
            self.engs.update(eid, follower_session=sid)
        if rc != 0:
            logger.warning("slack-follow %s turn failed rc=%s: %s", eid, rc, out[-500:])
            return False
        self.engs.mark_handled(eid, batch["newest"])
        return True

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
                        key=engage.inbox_key(e["channel"], e["thread"], "quiet-end"), profile=e.get("follower_profile"))
                except Exception:
                    logger.warning("slack-follow quiet card failed", exc_info=True)
                self.engs.update(e["id"], warned_quiet=now)
            if e.get("kickoff") or self.engs.notes(e["id"]) or self.engs.unhandled(e["id"], bot_user_id=bot)["trigger"]:
                self.kick(e["id"])
        self.deliver_mbp(now)

    # ---- queue to another machine's session (session-mentions courier) ------------------------
    def deliver_mbp(self, now: Optional[float] = None) -> None:
        for q in self.engs.due_mbp(now):
            ok, err = self._courier_send(q["target"], q["message"])
            self.engs.mbp_attempt(q["id"], ok=ok, error=err, retry_in=MBP_RETRY_S)
            if ok:
                logger.info("slack-follow delivered %s to %s", q["id"], q["target"])

    def _courier_send(self, target: str, message: str) -> tuple[bool, str]:
        base = self.root_home / "session_mentions"
        for sub in ("outbox", "claimed", "results"):
            (base / sub).mkdir(parents=True, exist_ok=True)
        rid = uuid.uuid4().hex
        req = {"id": rid, "action": "send", "args": {"target": target, "message": message},
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
                return bool(data.get("ok")), str(data.get("error") or "")
            if out.exists() and time.time() > deadline:
                out.unlink(missing_ok=True)
                return False, "Desktop courier offline (Mac asleep or Desktop not running)"
            time.sleep(0.5)
        return False, "courier timed out"


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
        return {"ok": True, "ts": ts, "thread_ts": thread_ts or ts, "verified": verified, "engagement": e["id"]}


def escalate(engs, inbox_post: Callable, *, channel: str, thread_ts: str, topic: str, title: str, body: str,
             reply_text: str = "", reply_to: str = "", action: str = "", permalink: str = "") -> dict:
    """One Inbox card per (channel, thread, topic); a repeat updates the open card."""
    e = engs.active_for(channel, thread_ts or reply_to or "") or {}
    key = engage.inbox_key(channel, thread_ts, topic)
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
    res = inbox_post(title=title, body=full_body, accept_prompt=accept, key=key,
                     poster=f"slack · {('#' + e['name']) if e.get('name') else channel}")
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
