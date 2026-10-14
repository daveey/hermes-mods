"""Pure logic for the slack-workspace plugin: Web API client, method tiers, the observed-message
store, policy, and approval requests. No Hermes imports here, so it is unit-testable on its own."""

from __future__ import annotations

import fnmatch
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

API = "https://slack.com/api/"

# --------------------------------------------------------------------------------------------
# Web API
# --------------------------------------------------------------------------------------------


class SlackError(Exception):
    def __init__(self, method: str, error: str, data: Optional[dict] = None):
        super().__init__(f"{method}: {error}")
        self.method, self.error, self.data = method, error, data or {}


def call(token: str, method: str, params: Optional[dict] = None, *, timeout: float = 20,
         _retries: int = 2) -> dict:
    """POST a Slack Web API method (JSON body; form for the few methods that reject JSON).
    Retries once on 429 using Retry-After. Raises SlackError on ``ok: false``."""
    params = {k: v for k, v in (params or {}).items() if v is not None}
    form_only = method in FORM_ONLY_METHODS
    if form_only:
        data = urllib.parse.urlencode(
            {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items()}).encode()
        ctype = "application/x-www-form-urlencoded"
    else:
        data, ctype = json.dumps(params).encode(), "application/json; charset=utf-8"
    req = urllib.request.Request(API + method, data=data, headers={
        "Authorization": "Bearer " + token, "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 429 and _retries > 0:
            time.sleep(min(float(e.headers.get("Retry-After") or 2), 30))
            return call(token, method, params, timeout=timeout, _retries=_retries - 1)
        raise SlackError(method, f"http_{e.code}") from e
    if not out.get("ok"):
        if out.get("error") == "ratelimited" and _retries > 0:
            time.sleep(3)
            return call(token, method, params, timeout=timeout, _retries=_retries - 1)
        raise SlackError(method, str(out.get("error")), out)
    return out


# Read methods that Slack only accepts as GET/form params.
FORM_ONLY_METHODS = {
    "conversations.history", "conversations.replies", "conversations.list", "conversations.info",
    "conversations.members", "users.info", "users.list", "users.lookupByEmail", "users.conversations",
    "users.profile.get", "users.getPresence", "team.info", "emoji.list", "pins.list", "bookmarks.list",
    "reactions.get", "reactions.list", "files.info", "files.list", "usergroups.list",
    "usergroups.users.list", "dnd.info", "reminders.list", "bots.info", "chat.getPermalink",
    "search.messages", "search.files", "search.all", "team.profile.get", "canvases.sections.lookup",
}

# --------------------------------------------------------------------------------------------
# Method tiers
# --------------------------------------------------------------------------------------------

READ, ACT, ADMIN = "read", "act", "admin"

_READ_VERBS = ("list", "info", "history", "replies", "get", "lookup", "members", "search", "test")
# Ordinary bot actions, auto-allowed when they target the conversation the bot is talking in.
_ACT_METHODS = {
    "chat.postMessage", "chat.postEphemeral", "chat.update", "chat.delete", "chat.scheduleMessage",
    "chat.deleteScheduledMessage", "chat.unfurl", "chat.meMessage",
    "reactions.add", "reactions.remove", "pins.add", "pins.remove",
    "bookmarks.add", "bookmarks.edit", "bookmarks.remove",
    "files.upload", "files.getUploadURLExternal", "files.completeUploadExternal",
    "conversations.mark", "conversations.join", "conversations.open",
    "canvases.create", "canvases.edit", "conversations.canvases.create",
    "reminders.add", "reminders.complete", "reminders.delete",
    "assistant.threads.setStatus", "assistant.threads.setTitle", "assistant.threads.setSuggestedPrompts",
    "lists.create", "lists.items.create", "lists.items.update",
}
# Methods that never target a conversation (fine from anywhere).
ACT_ANYWHERE = {"conversations.open", "reminders.add", "reminders.complete", "reminders.delete",
                 "files.getUploadURLExternal", "canvases.create", "lists.create",
                 "lists.items.create", "lists.items.update"}


def method_tier(method: str) -> str:
    """read / act / admin. Unknown methods are admin (fail closed)."""
    m = (method or "").strip()
    if m.startswith(("search.", "assistant.search.")) or m in ("auth.test", "api.test"):
        return READ
    if m.startswith("admin.") or m in ("users.profile.set", "users.setPresence", "users.deletePhoto",
                                         "users.setPhoto"):
        return ADMIN
    last = m.rsplit(".", 1)[-1]
    if any(last.startswith(v) for v in _READ_VERBS) and not last.startswith("set"):
        return READ
    if m in _ACT_METHODS:
        return ACT
    return ADMIN


def target_channel(params: dict) -> str:
    for k in ("channel", "channel_id"):
        v = params.get(k)
        if isinstance(v, str) and v:
            return v
    v = params.get("channels")
    if isinstance(v, str) and v and "," not in v:
        return v
    return ""


# --------------------------------------------------------------------------------------------
# Store: observed messages + channel/user caches + policy + requests
# --------------------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  team TEXT, channel TEXT NOT NULL, ts TEXT NOT NULL, thread_ts TEXT, user TEXT, bot TEXT,
  text TEXT, subtype TEXT, edited INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0,
  reply_count INTEGER DEFAULT 0, seen_at REAL, PRIMARY KEY (channel, ts));
CREATE INDEX IF NOT EXISTS messages_ts ON messages(ts);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at REAL, type TEXT, channel TEXT,
  user TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS names (id TEXT PRIMARY KEY, name TEXT, kind TEXT, private INTEGER,
  extra TEXT, at REAL);
CREATE TABLE IF NOT EXISTS watermarks (key TEXT PRIMARY KEY, ts TEXT);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

DEFAULT_POLICY = {
    # Public channels the bot may join without asking (fnmatch on name, or a channel id). "*" = all.
    "auto_join": [],
    # Channel names/ids it must never join.
    "never_join": [],
    # Admin-tier methods admins pre-approved for anyone ("always allow" button adds here).
    "auto_methods": [],
    # Channel name/id patterns whose observed messages are kept but never injected as context.
    "quiet_channels": [],
    "retention_days": 30,
}


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "observed.sqlite"
        self.policy_path = self.root / "policy.json"
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=10)
        c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        return c

    # ---- policy ---------------------------------------------------------------------------
    def policy(self) -> dict:
        p = dict(DEFAULT_POLICY)
        try:
            p.update(json.loads(self.policy_path.read_text()))
        except (OSError, ValueError):
            pass
        return p

    def update_policy(self, **changes) -> dict:
        with self._lock:
            p = self.policy()
            for k, v in changes.items():
                if isinstance(p.get(k), list) and isinstance(v, (list, tuple, set)):
                    p[k] = sorted(set(p[k]) | set(v))
                else:
                    p[k] = v
            tmp = self.policy_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(p, indent=2) + "\n")
            tmp.replace(self.policy_path)
            return p

    # ---- observation ----------------------------------------------------------------------
    def record_event(self, event: dict, team: str = "") -> None:
        """Ingest one Events API ``event`` payload. Messages (incl. edits/deletes) go to
        ``messages``; membership/channel lifecycle events go to ``events``."""
        et = event.get("type")
        now = time.time()
        with self._conn() as c:
            if et == "message":
                st = event.get("subtype") or ""
                ch = event.get("channel") or ""
                if st == "message_changed":
                    m = event.get("message") or {}
                    c.execute("UPDATE messages SET text=?, edited=1 WHERE channel=? AND ts=?",
                              (m.get("text") or "", ch, m.get("ts")))
                    return
                if st == "message_deleted":
                    c.execute("UPDATE messages SET deleted=1, text='' WHERE channel=? AND ts=?",
                              (ch, event.get("deleted_ts")))
                    return
                if st in ("message_replied",):
                    return
                ts = event.get("ts")
                if not (ch and ts):
                    return
                text = event.get("text") or ""
                files = [f.get("name") or f.get("title") for f in event.get("files") or []]
                if files:
                    text += "".join(f"\n[file: {n}]" for n in files if n)
                c.execute(
                    "INSERT OR REPLACE INTO messages(team,channel,ts,thread_ts,user,bot,text,subtype,seen_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (team or event.get("team") or "", ch, ts, event.get("thread_ts") or "",
                     event.get("user") or "", event.get("bot_id") or
                     (event.get("bot_profile") or {}).get("name") or "", text, st, now))
                tt = event.get("thread_ts")
                if tt and tt != ts:
                    c.execute("UPDATE messages SET reply_count=reply_count+1 WHERE channel=? AND ts=?",
                              (ch, tt))
            elif et in ("member_joined_channel", "member_left_channel", "channel_created",
                        "channel_rename", "channel_archive", "channel_unarchive", "channel_deleted",
                        "group_rename", "group_archive", "channel_topic", "pin_added",
                        "reaction_added", "emoji_changed", "team_join", "user_change",
                        "subteam_created", "subteam_updated", "app_mention", "file_shared",
                        "channel_shared", "channel_unshared"):
                ch = event.get("channel")
                if isinstance(ch, dict):
                    ch = ch.get("id")
                item = event.get("item") or {}
                ch = ch or item.get("channel") or ""
                user = event.get("user")
                if isinstance(user, dict):
                    user = user.get("id")
                if et in ("app_mention", "user_change", "emoji_changed"):
                    return  # duplicates a message event / noisy
                detail = {k: event[k] for k in ("reaction", "name", "inviter") if k in event}
                if et == "reaction_added":
                    detail["item_ts"] = item.get("ts")
                if isinstance(event.get("channel"), dict):
                    detail["name"] = event["channel"].get("name")
                c.execute("INSERT INTO events(at,type,channel,user,detail) VALUES (?,?,?,?,?)",
                          (now, et, ch or "", user or "", json.dumps(detail)))
                if et in ("channel_rename", "group_rename", "channel_created") and detail.get("name"):
                    c.execute("INSERT OR REPLACE INTO names(id,name,kind,private,extra,at) VALUES (?,?,?,?,?,?)",
                              (ch, detail["name"], "channel", 0, "{}", now))

    def prune(self) -> None:
        days = float(self.policy().get("retention_days") or 30)
        cutoff = time.time() - days * 86400
        with self._conn() as c:
            c.execute("DELETE FROM messages WHERE CAST(ts AS REAL) < ?", (cutoff,))
            c.execute("DELETE FROM events WHERE at < ?", (cutoff,))

    def recent(self, channel: Optional[str] = None, since_ts: float = 0, limit: int = 200,
               exclude_ts: Iterable[str] = (), top_level_only: bool = False) -> list[dict]:
        q = "SELECT * FROM messages WHERE CAST(ts AS REAL) > ? AND deleted=0"
        args: list[Any] = [since_ts]
        if channel:
            q += " AND channel=?"
            args.append(channel)
        if top_level_only:
            q += " AND (thread_ts='' OR thread_ts=ts)"
        q += " ORDER BY CAST(ts AS REAL) DESC LIMIT ?"
        args.append(limit)
        ex = set(exclude_ts)
        with self._conn() as c:
            rows = [dict(r) for r in c.execute(q, args) if r["ts"] not in ex]
        return list(reversed(rows))

    def activity_summary(self, since_ts: float) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT channel, COUNT(*) n, COUNT(DISTINCT user) people, MAX(ts) last_ts FROM messages"
                " WHERE CAST(ts AS REAL) > ? AND deleted=0 GROUP BY channel ORDER BY n DESC", (since_ts,))]

    def events_since(self, since: float, channel: Optional[str] = None, limit: int = 100) -> list[dict]:
        q = "SELECT * FROM events WHERE at > ?"
        args: list[Any] = [since]
        if channel:
            q += " AND channel=?"
            args.append(channel)
        q += " ORDER BY at DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args)][::-1]

    # ---- names cache ------------------------------------------------------------------------
    def cache_name(self, id_: str, name: str, kind: str, private: bool = False, extra: Optional[dict] = None):
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO names(id,name,kind,private,extra,at) VALUES (?,?,?,?,?,?)",
                      (id_, name, kind, int(bool(private)), json.dumps(extra or {}), time.time()))

    def cached(self, id_: str, max_age: float = 86400 * 3) -> Optional[dict]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM names WHERE id=?", (id_,)).fetchone()
        if r and time.time() - (r["at"] or 0) < max_age:
            return dict(r)
        return None

    # ---- watermarks / kv ----------------------------------------------------------------------
    def watermark(self, key: str) -> Optional[str]:
        with self._conn() as c:
            r = c.execute("SELECT ts FROM watermarks WHERE key=?", (key,)).fetchone()
        return r["ts"] if r else None

    def set_watermark(self, key: str, ts: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO watermarks(key,ts) VALUES (?,?)", (key, ts))

    def kv_get(self, key: str) -> Any:
        with self._conn() as c:
            r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else None

    def kv_set(self, key: str, value: Any) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO kv(key,value) VALUES (?,?)", (key, json.dumps(value)))

    # ---- approval requests ----------------------------------------------------------------
    def new_request(self, *, requester: str, origin: dict, ops: list[dict], reason: str) -> dict:
        req = {"id": uuid.uuid4().hex[:10], "created": time.time(), "requester": requester,
               "origin": origin, "ops": ops, "reason": reason, "status": "pending", "results": [],
               "dm": []}
        self.kv_set("req:" + req["id"], req)
        return req

    def request(self, rid: str) -> Optional[dict]:
        return self.kv_get("req:" + rid)

    def save_request(self, req: dict) -> None:
        self.kv_set("req:" + req["id"], req)


def matches(patterns: Iterable[str], channel_id: str, name: str) -> bool:
    for p in patterns or ():
        if p == channel_id or fnmatch.fnmatchcase(name or "", p.lstrip("#")):
            return True
    return False


def op_label(op: dict) -> str:
    if op.get("method") == "conversations.join":
        return f"join #{op.get('name') or op['params'].get('channel')}"
    params = {k: v for k, v in op.get("params", {}).items() if k not in ("blocks", "attachments")}
    s = json.dumps(params, ensure_ascii=False)
    return f"{op['method']} {s[:140]}{'…' if len(s) > 140 else ''}"
