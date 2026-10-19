"""Engagements: a Hermes session brings the bot into a channel (or one thread) and a *follower*
agent takes over replies there. Pure logic, no Hermes imports (unit-testable):

* ``Engagements`` — CRUD over an ``engagements`` table in the slack-workspace store, plus the
  per-message reply claim (exactly one reply per message), the bot-to-bot turn cap, and a retry
  queue for requests the follower must hand to another machine's session.
* ``route(event, ...)`` — the routing decision for one Slack event: ``ambient`` (the normal bot
  handles it) or ``engaged`` (the normal bot never sees it); ``trigger`` says whether it should
  wake the follower.
* ``inbox_key(channel, thread, topic)`` — stable dedupe key for Inbox escalations.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Optional

BOT_TURN_CAP = 3
QUIET_END_DAYS = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS engagements (
  id TEXT PRIMARY KEY, channel TEXT NOT NULL, thread TEXT NOT NULL DEFAULT '', name TEXT,
  brief TEXT, context TEXT, owner TEXT, kanban_ids TEXT, budget TEXT, private INTEGER DEFAULT 0,
  follower_profile TEXT, follower_session TEXT, last_handled_ts TEXT, state TEXT NOT NULL,
  kickoff INTEGER DEFAULT 1, created REAL, updated REAL, expires REAL, last_activity REAL,
  warned_quiet REAL);
CREATE TABLE IF NOT EXISTS eng_replies (channel TEXT, reply_to TEXT, posted_ts TEXT, at REAL,
  PRIMARY KEY (channel, reply_to));
CREATE TABLE IF NOT EXISTS eng_bot_turns (channel TEXT, thread TEXT, n INTEGER, at REAL,
  PRIMARY KEY (channel, thread));
CREATE TABLE IF NOT EXISTS eng_mbp_queue (id TEXT PRIMARY KEY, engagement TEXT, target TEXT,
  message TEXT, channel TEXT, thread TEXT, state TEXT, attempts INTEGER DEFAULT 0, error TEXT,
  next_at REAL, created REAL, delivered REAL);
"""

FIELDS = {"name", "brief", "context", "owner", "kanban_ids", "budget", "private", "follower_profile",
          "follower_session", "last_handled_ts", "state", "kickoff", "expires", "last_activity",
          "warned_quiet"}
STATES = ("active", "paused", "ended")


def _ts(x: Any) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def mentions(text: str, bot_user_id: str) -> bool:
    return bool(bot_user_id) and f"<@{bot_user_id}" in (text or "")


def inbox_key(channel: str, thread: str, topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (topic or "").lower()).strip("-")[:60] or "general"
    return f"slack:{channel}:{thread or '-'}:{slug}"


class Engagements:
    def __init__(self, store):
        self.store = store
        with store._conn() as c:
            c.executescript(SCHEMA)

    # ---- CRUD ---------------------------------------------------------------------------------
    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        d["kanban_ids"] = json.loads(d.get("kanban_ids") or "[]")
        d["private"] = bool(d.get("private"))
        return d

    def create(self, channel: str, brief: str, *, thread: str = "", name: str = "", context: str = "",
               owner: str = "", kanban_ids: Iterable[str] = (), budget: str = "",
               until: Optional[float] = None, private: bool = False,
               follower_profile: str = "slack-follow", last_handled_ts: Optional[str] = None) -> dict:
        if not channel:
            raise ValueError("channel is required")
        now = time.time()
        for e in self.list(states=("active", "paused")):
            if e["channel"] == channel and e["thread"] == (thread or ""):
                raise ValueError(f"engagement {e['id']} already covers this "
                                 f"{'thread' if thread else 'channel'} ({e['state']})")
        eid = "eng_" + uuid.uuid4().hex[:8]
        with self.store._conn() as c:
            c.execute(
                "INSERT INTO engagements(id,channel,thread,name,brief,context,owner,kanban_ids,budget,"
                "private,follower_profile,follower_session,last_handled_ts,state,kickoff,created,updated,"
                "expires,last_activity) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (eid, channel, thread or "", name, brief, context, owner, json.dumps(list(kanban_ids)),
                 budget, int(bool(private)), follower_profile, "",
                 last_handled_ts if last_handled_ts is not None else f"{now:.6f}", "active", 1, now, now,
                 until, now))
        return self.get(eid)

    def get(self, eid: str) -> Optional[dict]:
        with self.store._conn() as c:
            r = c.execute("SELECT * FROM engagements WHERE id=?", (eid,)).fetchone()
        return self._row(r) if r else None

    def list(self, states: Optional[Iterable[str]] = None) -> list[dict]:
        with self.store._conn() as c:
            rows = [self._row(r) for r in c.execute("SELECT * FROM engagements ORDER BY created")]
        return [r for r in rows if states is None or r["state"] in tuple(states)]

    def update(self, eid: str, **fields) -> dict:
        bad = set(fields) - FIELDS
        if bad:
            raise ValueError(f"unknown engagement field(s): {sorted(bad)}")
        if "kanban_ids" in fields:
            fields["kanban_ids"] = json.dumps(list(fields["kanban_ids"] or []))
        if "state" in fields and fields["state"] not in STATES:
            raise ValueError(f"state must be one of {STATES}")
        if not fields:
            return self.get(eid)
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.store._conn() as c:
            c.execute(f"UPDATE engagements SET {sets}, updated=? WHERE id=?",
                      (*fields.values(), time.time(), eid))
        return self.get(eid)

    def set_state(self, eid: str, state: str) -> dict:
        return self.update(eid, state=state)

    def active_for(self, channel: str, thread_ts: str, now: Optional[float] = None) -> Optional[dict]:
        now = time.time() if now is None else now
        hits = [e for e in self.list(states=("active",)) if e["channel"] == channel
                and (not e["expires"] or e["expires"] > now)
                and (not e["thread"] or e["thread"] == thread_ts)]
        hits.sort(key=lambda e: 0 if e["thread"] else 1)
        return hits[0] if hits else None

    # ---- batches --------------------------------------------------------------------------------
    def in_scope(self, e: dict, row: dict) -> bool:
        if row.get("channel") != e["channel"]:
            return False
        return not e["thread"] or (row.get("thread_ts") or row.get("ts")) == e["thread"]

    def unhandled(self, eid: str, *, bot_user_id: str) -> dict:
        """Messages after the engagement's watermark: ``trigger`` = the ones that need the follower
        (humans, and bots that address the bot), ``newest`` = the new watermark."""
        e = self.get(eid)
        since = _ts(e["last_handled_ts"])
        rows = [r for r in self.store.recent(e["channel"], since, limit=500) if self.in_scope(e, r)]
        trig = [r for r in rows if is_trigger(r, bot_user_id)]
        newest = max((r["ts"] for r in rows), key=_ts, default=None)
        return {"trigger": trig, "all": rows, "newest": newest}

    def mark_handled(self, eid: str, ts: str) -> None:
        e = self.get(eid)
        if e and _ts(ts) > _ts(e["last_handled_ts"]):
            self.update(eid, last_handled_ts=ts)

    # ---- one reply per message --------------------------------------------------------------
    def claim_reply(self, channel: str, reply_to: str) -> bool:
        with self.store._conn() as c:
            cur = c.execute("INSERT OR IGNORE INTO eng_replies(channel,reply_to,posted_ts,at) VALUES (?,?,?,?)",
                            (channel, reply_to, "", time.time()))
            return cur.rowcount == 1

    def release_reply(self, channel: str, reply_to: str) -> None:
        with self.store._conn() as c:
            c.execute("DELETE FROM eng_replies WHERE channel=? AND reply_to=?", (channel, reply_to))

    def record_reply(self, channel: str, reply_to: str, posted_ts: str) -> None:
        with self.store._conn() as c:
            c.execute("UPDATE eng_replies SET posted_ts=? WHERE channel=? AND reply_to=?",
                      (posted_ts, channel, reply_to))

    def reply_for(self, channel: str, reply_to: str) -> Optional[str]:
        with self.store._conn() as c:
            r = c.execute("SELECT posted_ts FROM eng_replies WHERE channel=? AND reply_to=?",
                          (channel, reply_to)).fetchone()
        return r["posted_ts"] if r else None

    # ---- bot-to-bot cap ---------------------------------------------------------------------
    def bot_turns(self, channel: str, thread: str) -> int:
        with self.store._conn() as c:
            r = c.execute("SELECT n FROM eng_bot_turns WHERE channel=? AND thread=?", (channel, thread)).fetchone()
        return int(r["n"]) if r else 0

    def bot_turn_allowed(self, channel: str, thread: str, cap: int = BOT_TURN_CAP) -> bool:
        return self.bot_turns(channel, thread) < cap

    def bump_bot_turns(self, channel: str, thread: str) -> int:
        n = self.bot_turns(channel, thread) + 1
        with self.store._conn() as c:
            c.execute("INSERT OR REPLACE INTO eng_bot_turns(channel,thread,n,at) VALUES (?,?,?,?)",
                      (channel, thread, n, time.time()))
        return n

    def reset_bot_turns(self, channel: str, thread: str) -> None:
        with self.store._conn() as c:
            c.execute("DELETE FROM eng_bot_turns WHERE channel=? AND thread=?", (channel, thread))

    # ---- test engagements ---------------------------------------------------------------------
    # A test engagement exercises the follower without bothering the owner: its escalations are
    # recorded but not sent to the Inbox unless the policy's ``test_inbox`` toggle is on.
    def set_test(self, eid: str, on: bool) -> None:
        self.store.kv_set("test:" + eid, bool(on))

    def is_test(self, eid: str) -> bool:
        return bool(self.store.kv_get("test:" + eid))

    def test_inbox(self) -> bool:
        return bool(self.store.policy().get("test_inbox"))

    # ---- notes for the follower (results coming back from another machine's session) --------
    def add_note(self, eid: str, text: str, sender: str = "") -> int:
        notes = self.store.kv_get("notes:" + eid) or []
        notes.append({"text": text, "sender": sender, "at": time.time()})
        self.store.kv_set("notes:" + eid, notes)
        return len(notes)

    def notes(self, eid: str) -> list[dict]:
        return self.store.kv_get("notes:" + eid) or []

    def take_notes(self, eid: str, n: int) -> None:
        self.store.kv_set("notes:" + eid, self.notes(eid)[n:])

    # ---- escalations (Inbox cards) ----------------------------------------------------------
    def record_escalation(self, key: str, *, channel: str, thread: str, topic: str, title: str,
                          session_id: str = "", suppressed: Optional[dict] = None) -> None:
        row = {"key": key, "channel": channel, "thread": thread, "topic": topic,
               "title": title, "session_id": session_id, "at": time.time()}
        if suppressed:
            row["suppressed"] = suppressed  # the card that would have gone to the Inbox (test engagement)
        self.store.kv_set("esc:" + key, row)

    def escalations(self, channel: str) -> list[dict]:
        with self.store._conn() as c:
            rows = [json.loads(r["value"]) for r in c.execute(
                "SELECT value FROM kv WHERE key LIKE ? ORDER BY key", (f"esc:slack:{channel}:%",))]
        return sorted(rows, key=lambda r: r.get("at") or 0)

    # ---- queue for another machine's session ------------------------------------------------
    def queue_mbp(self, eid: str, target: str, message: str, channel: str, thread: str) -> dict:
        qid = "q_" + uuid.uuid4().hex[:8]
        now = time.time()
        with self.store._conn() as c:
            c.execute("INSERT INTO eng_mbp_queue(id,engagement,target,message,channel,thread,state,attempts,"
                      "next_at,created) VALUES (?,?,?,?,?,?,'queued',0,?,?)",
                      (qid, eid, target, message, channel, thread, now, now))
        return self.mbp_get(qid)

    def mbp_get(self, qid: str) -> Optional[dict]:
        with self.store._conn() as c:
            r = c.execute("SELECT * FROM eng_mbp_queue WHERE id=?", (qid,)).fetchone()
        return dict(r) if r else None

    def mbp_list(self, eid: Optional[str] = None) -> list[dict]:
        with self.store._conn() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM eng_mbp_queue ORDER BY created")]
        return [r for r in rows if eid is None or r["engagement"] == eid]

    def due_mbp(self, now: Optional[float] = None) -> list[dict]:
        now = time.time() if now is None else now
        return [r for r in self.mbp_list() if r["state"] == "queued" and (r["next_at"] or 0) <= now]

    def mbp_claim(self, now: Optional[float] = None, lease: float = 600) -> list[dict]:
        """Hand due requests to a puller (the other machine fetching its own work) exactly once; an
        unsettled claim becomes due again after ``lease`` seconds."""
        now = time.time() if now is None else now
        due = self.due_mbp(now)
        with self.store._conn() as c:
            for r in due:
                c.execute("UPDATE eng_mbp_queue SET next_at=? WHERE id=?", (now + lease, r["id"]))
        return due

    def mbp_attempt(self, qid: str, *, ok: bool, error: str = "", retry_in: float = 120) -> None:
        now = time.time()
        with self.store._conn() as c:
            if ok:
                c.execute("UPDATE eng_mbp_queue SET state='delivered', attempts=attempts+1, delivered=?, "
                          "error='' WHERE id=?", (now, qid))
            else:
                c.execute("UPDATE eng_mbp_queue SET attempts=attempts+1, error=?, next_at=? WHERE id=?",
                          (error[:500], now + retry_in, qid))


def is_trigger(row: dict, bot_user_id: str, bot_id: str = "") -> bool:
    """A stored message (or raw event) that should wake the follower."""
    user = row.get("user") or ""
    bot = row.get("bot") or row.get("bot_id") or ""
    if (bot_user_id and user == bot_user_id) or (bot_id and bot == bot_id):
        return False
    if row.get("subtype") in ("message_changed", "message_deleted", "channel_join", "channel_leave",
                              "message_replied"):
        return False
    if bot:
        return mentions(row.get("text") or "", bot_user_id)
    return bool(user)


@dataclass
class Route:
    decision: str              # "ambient" | "engaged"
    trigger: bool = False
    engagement: Optional[dict] = None
    thread: str = ""


def route(event: dict, engagements: Engagements, *, bot_user_id: str, bot_id: str = "") -> Route:
    et = event.get("type")
    if et not in ("message", "app_mention"):
        return Route("ambient")
    ch = event.get("channel") or ""
    msg = event.get("message") if event.get("subtype") == "message_changed" else None
    ts = (msg or event).get("ts") or event.get("deleted_ts") or ""
    thread = (msg or event).get("thread_ts") or ts
    e = engagements.active_for(ch, thread)
    if not e:
        return Route("ambient")
    if et == "app_mention":  # the matching message event carries the trigger
        return Route("engaged", False, e, thread)
    trig = is_trigger(event, bot_user_id, bot_id)
    if trig and not (event.get("bot_id") or event.get("bot_profile")):
        engagements.reset_bot_turns(ch, thread)  # a human spoke: the bot-to-bot counter restarts
    return Route("engaged", trig, e, thread)
