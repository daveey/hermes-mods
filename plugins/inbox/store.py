"""Hermes Inbox store.

An inbox message IS a session: a ``source='desktop'`` row created ``hidden`` (so it stays out
of Recents) whose first message is Hermes's proposal (role=assistant). A sidecar SQLite file,
``<HERMES_HOME>/inbox/inbox.db``, records which sessions are inbox messages plus their metadata
(dedupe key, poster, the prompt "Do it" sends).

Lifecycle, all derived from the session row so other doors (sidebar archive, restore) agree:
  open      sidecar state 'open', session hidden, not archived  -> listed in the Inbox
  accepted  the user ran it (Do it / typed a reply): session un-hidden -> a normal chat in Recents
  archived  session archived (restorable from the archived view)
  resolved  a ``resolve`` action of the card's type ran: outcome recorded, session archived
Unread = the session's ``last_read_at`` watermark predates the message's last post.

Card types (card_types.py): each item snapshots its type's fields + actions (``actions_json``) at
post time. ``act()`` runs an action; ``outcome(key)`` reads the decision back for the poster. Rows
from before types have ``type`` NULL and behave as the built-in ``default`` type (Do it / Archive).
"""
from __future__ import annotations

import importlib.util
import hashlib
from contextlib import contextmanager
import json
import os
import sqlite3
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

MAX_OPEN = 20          # past this, the oldest READ open messages are archived
MAX_TITLE = 120
MAX_BODY = 20_000
MAX_PROMPT = 4_000
STALE_QUEUING_S = 300  # a task claim older than this had a crashed claimant; act() may retry it
# Noise breaker: a poster whose last MUTE_AFTER closed cards (within MUTE_WINDOW_S) were all archived
# with no action is refused, and one Kanban card is filed to fix that poster. David's Archive is the signal.
MUTE_AFTER = 3
MUTE_WINDOW_S = 7 * 86400

_lock = threading.RLock()
_UNSET = object()  # explicit expected=None is invalid, never an unconditional fallback


class PosterMuted(ValueError):
    """The poster's recent cards were all dismissed; its posts are refused until it is fixed + unmuted."""


class AgentFixablePost(ValueError):
    """An automated poster tried to put agent-fixable state in the Inbox (owner-only decisions)."""


# Posters that report board state an agent can fix: never the Inbox, always a Kanban comment/card.
AGENT_FIXABLE_POSTERS = frozenset({"kanban-dispatcher", "chat-closeout"})
# A "Do it" that tells an agent to fix a Kanban card and unblock it is not a decision for David.
_AGENT_FIX_PROMPT = re.compile(r"kanban\s+card\b.*\bfix\b.*\bunblock", re.I | re.S)


def agent_fixable_reason(poster: Optional[str], accept_prompt: str) -> Optional[str]:
    """Why this post belongs on Kanban instead of the Inbox, or None when it may be posted."""
    if poster and poster.strip().lower() in AGENT_FIXABLE_POSTERS:
        return f"poster {poster!r} reports agent-fixable board state"
    if accept_prompt and _AGENT_FIX_PROMPT.search(accept_prompt):
        return "its 'Do it' asks an agent to fix and unblock a Kanban card"
    return None


class TaskError(RuntimeError):
    """A ``task`` action could not queue its Kanban card (the inbox card stays open)."""


def kanban_task_creator(*, title: str, body: str, assignee: str, board=None, skills=None,
                        idempotency_key=None) -> str:
    """Create a Kanban card through the core API (the one ``kanban_create`` uses); returns its id.
    The kanban home is shared by every profile, so a card posted on another profile's inbox lands
    on the same board; ``assignee`` names the profile that runs it."""
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    if board and not kb.board_exists(board):
        raise TaskError(f"kanban board {board!r} does not exist")
    with kbc.connect_closing(board=board) as conn:
        return kb.create_task(conn, title=title, body=body, assignee=assignee, board=board,
                              skills=list(skills or []) or None, idempotency_key=idempotency_key,
                              created_by="inbox")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  session_id    TEXT PRIMARY KEY,
  key           TEXT,
  title         TEXT NOT NULL,
  poster        TEXT,
  accept_prompt TEXT NOT NULL,
  created_at    REAL NOT NULL,
  updated_at    REAL NOT NULL,
  state         TEXT NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS items_key ON items(key);
CREATE TABLE IF NOT EXISTS unmutes (
  poster     TEXT PRIMARY KEY,
  unmuted_at REAL NOT NULL
);
"""
# Added with card types; ALTERed into older sidecars on open (idempotent).
_COLUMNS = (("type", "TEXT"), ("fields_json", "TEXT"), ("actions_json", "TEXT"), ("outcome", "TEXT"),
            ("outcome_value", "TEXT"), ("resolved_at", "REAL"), ("outcome_task", "TEXT"),
            ("cwd_explicit", "INTEGER"))  # NULL = legacy/unknown; never infer intent from a path
# Not "types.py": a sibling named like the stdlib module shadows it whenever this dir is on sys.path.
_TYPES = Path(__file__).resolve().parent / "card_types.py"


_ROUTING = Path(__file__).resolve().parent / "routing.py"


def routing_module():
    """routing.py loaded by path, like card_types.py."""
    key = f"_hermes_inbox_routing_{int(_ROUTING.stat().st_mtime_ns)}"
    mod = sys.modules.get(key)
    if mod is None:
        spec = importlib.util.spec_from_file_location(key, _ROUTING)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod = sys.modules.setdefault(key, mod)
    return mod


def types_module():
    """card_types.py loaded by path (this module itself is loaded without a package)."""
    key = f"_hermes_inbox_card_types_{int(_TYPES.stat().st_mtime_ns)}"
    mod = sys.modules.get(key)
    if mod is None:
        spec = importlib.util.spec_from_file_location(key, _TYPES)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod = sys.modules.setdefault(key, mod)
    return mod


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


_PROFILE_NAME = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def profile_homes(root: Path) -> list[tuple[str, Path]]:
    """('default', root) plus every named profile under ``root/profiles`` that has an inbox, so a
    gateway's one dashboard can list messages its profiles posted (Desktop reads root only)."""
    root = Path(root)
    out = [("default", root)]
    pdir = root / "profiles"
    if pdir.is_dir():
        for p in sorted(pdir.iterdir()):
            if p.is_dir() and _PROFILE_NAME.match(p.name) and (p / "inbox" / "inbox.db").exists():
                out.append((p.name, p))
    return out


def profile_home(root: Path, profile: Optional[str]) -> Path:
    if not profile or profile == "default":
        return Path(root)
    if not _PROFILE_NAME.match(profile):
        raise ValueError(f"bad profile name {profile!r}")
    return Path(root) / "profiles" / profile


class Inbox:
    """``db_factory`` returns a SessionDB (injectable for tests); ``home`` holds the sidecar."""

    def __init__(self, home: Optional[Path] = None, db_factory: Optional[Callable[[], Any]] = None,
                 clock: Callable[[], float] = time.time, types: Any = None,
                 task_creator: Optional[Callable[..., str]] = None,
                 read_factory: Optional[Callable[[], Any]] = None):
        self.home = Path(home) if home else _home()
        self.path = self.home / "inbox" / "inbox.db"
        self._db_factory = db_factory
        self._read_factory = read_factory
        self.clock = clock
        self._types = types
        self._task_creator = task_creator

    @property
    def types(self):
        """The card-type Registry for this home (injectable for tests)."""
        if self._types is None:
            self._types = types_module().Registry(self.home)
        return self._types

    @classmethod
    def for_home(cls, home: Path, **kw) -> "Inbox":
        """An inbox bound to *home*'s own sidecar AND state.db (another profile's home)."""
        home = Path(home)

        def factory():
            from hermes_state import SessionDB
            return SessionDB(db_path=home / "state.db")

        def reader():
            from hermes_state import SessionDB
            return SessionDB(db_path=home / "state.db", read_only=True)
        kw.setdefault("read_factory", reader)
        return cls(home=home, db_factory=factory, **kw)

    # ── plumbing ────────────────────────────────────────────────────────────
    def _side(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        have = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        for col, decl in _COLUMNS:
            if col not in have:
                try:
                    conn.execute(f"ALTER TABLE items ADD COLUMN {col} {decl}")
                except sqlite3.OperationalError as exc:  # a concurrent opener added it first
                    if "duplicate column" not in str(exc):
                        raise
        return conn

    def _sessions(self):
        if self._db_factory:
            return self._db_factory()
        from hermes_state import SessionDB
        return SessionDB()

    def _reader(self):
        """state.db for list(), which Desktop polls once per profile: read-only, because a writable SessionDB()
        runs schema init plus a system-wide open-file scan (core/backend-writable-open-guard.md). An injected
        db_factory without a read_factory (tests, custom stores) is used as given."""
        if self._read_factory is not None:
            return self._read_factory()
        if self._db_factory is not None:
            return self._db_factory()
        from hermes_state import SessionDB
        return SessionDB(read_only=True)

    @staticmethod
    def _close(db) -> None:
        if not getattr(db, "_inbox_keep_open", False):
            try:
                db.close()
            except Exception:
                pass

    def exists(self) -> bool:
        return self.path.exists()

    # ── social routing backfill ─────────────────────────────────────────────
    def backfill_social(self, *, apply: bool = False, include_archived: bool = False) -> list[dict[str, Any]]:
        """Move clearly-social cards sitting in the generic fallback cwd (the home dir, or none) into
        this host's `pr` project folder, only with recorded non-explicit cwd provenance and an
        authoritative session ``ended_at``. Legacy unknown provenance and unended/missing sessions
        fail closed regardless of age or message count. Explicit home is a choice too. Other
        directories and archived cards (unless asked) stay put. Dry run unless ``apply``."""
        rt = routing_module()
        target = rt.project_path(self.home)
        out: list[dict[str, Any]] = []
        if not target or not self.exists():
            return out
        side, db = self._side(), self._sessions()
        try:
            rows = side.execute("SELECT session_id, key, title, poster, type, cwd_explicit FROM items").fetchall()
            for r in rows:
                if not rt.is_social(key=r["key"], type=r["type"], title=r["title"] or "", poster=r["poster"]):
                    continue
                s = db.get_session(r["session_id"]) or {}
                cwd = s.get("cwd") or ""
                rec = {"session_id": r["session_id"], "key": r["key"], "from": cwd, "to": target}
                if rt.within(cwd, target):
                    rec["action"] = "already"
                elif s.get("archived") and not include_archived:
                    rec["action"] = "skip:archived"
                elif r["cwd_explicit"] is None:
                    rec["action"] = "skip:unknown-cwd-provenance"
                elif r["cwd_explicit"] != 0:
                    rec["action"] = "skip:explicit-cwd"
                elif cwd and os.path.normpath(cwd) != os.path.normpath(str(Path.home())):
                    rec["action"] = "skip:explicit-cwd"
                elif s.get("ended_at") is None:
                    rec["action"] = "skip:active"
                else:
                    rec["action"] = "moved" if apply else "would-move"
                    if apply:
                        # Recheck lifecycle + cwd in the write transaction: a concurrent resume
                        # or cwd change must not be overwritten after the eligibility read.
                        changed = db._execute_write(lambda c, sid=r["session_id"]: c.execute(
                            "UPDATE sessions SET cwd=? WHERE id=? AND ended_at=? AND cwd IS ? "
                            "AND (? OR COALESCE(archived, 0)=0)",
                            (target, sid, s["ended_at"], s.get("cwd"), include_archived)).rowcount)
                        if not changed:
                            rec["action"] = "skip:changed"
                out.append(rec)
            return out
        finally:
            side.close()
            self._close(db)

    # ── posting ─────────────────────────────────────────────────────────────
    def post(self, title: str, body: str, accept_prompt: str = "", *, key: Optional[str] = None,
             poster: Optional[str] = None, cwd: Optional[str] = None,
             model: Optional[str] = None, prompt_label: Optional[str] = None,
             type: Optional[str] = None, fields: Optional[dict] = None,
             inherited_cwd: Optional[str] = None, if_absent: bool = False,
             expected: Any = _UNSET) -> dict[str, Any]:
        """``cwd`` is an explicit caller choice and always wins; ``inherited_cwd`` is the posting
        session's own cwd, which social cards replace with this host's `pr` project folder.
        New cards persist cwd_explicit (including explicit home); reposts retain original provenance."""
        ct = types_module()
        title = " ".join(str(title or "").split())[:MAX_TITLE]
        prompt_label = " ".join(str(prompt_label or "").split())[:200] or None
        body = str(body or "").strip()[:MAX_BODY]
        accept_prompt = str(accept_prompt or "").strip()[:MAX_PROMPT]
        key = (str(key).strip() or None) if key else None
        poster = (" ".join(str(poster).split())[:80] or None) if poster else None
        if not title:
            raise ValueError("title is required")
        why = agent_fixable_reason(poster, accept_prompt)
        if why:  # detection-level guard: refuse before David ever sees it (the noise breaker needs 3)
            raise AgentFixablePost(
                f"refused: {why}. The Inbox is for decisions only David can make; put this on the "
                f"Kanban board instead (kanban_comment on the card, or kanban_create a fix card).")
        tdef = self.types.get(type or "default")
        fvals = ct.check_fields(tdef, fields)
        if not accept_prompt and ct.needs_accept_prompt(tdef):
            raise ValueError("accept_prompt is required (the instruction 'Do it' sends)")
        snap = ct.snapshot(tdef)
        tname, fjson, ajson = snap["name"], json.dumps(fvals), json.dumps(snap)
        legacy = ct.is_builtin_default(snap)  # by content: a look-alike type can't borrow the legacy text
        # A type with resolve actions is a decision card: after the user acted on it, a re-post is a
        # new card with buttons, never a follow-up note in the old chat.
        decision = any(x["kind"] in ("resolve", "task") for x in snap["actions"])
        now = self.clock()
        if if_absent or expected is not _UNSET:
            if not key or (if_absent and expected is not _UNSET):
                raise ValueError("conditional post needs a key and exactly one condition")
            if expected is not _UNSET and (not isinstance(expected, dict) or
                    expected.get("key") != key or not expected.get("revision") or
                    not expected.get("session_id")):
                raise ValueError("expected must be an outcome snapshot with key, session_id and revision")
            return self._conditional_post(
                title=title, body=body, accept_prompt=accept_prompt, key=key, poster=poster,
                cwd=cwd, inherited_cwd=inherited_cwd, model=model, prompt_label=prompt_label,
                snap=snap, fvals=fvals, legacy=legacy, now=now,
                if_absent=if_absent, expected=expected)
        with _lock:
            side = self._side()
            db = self._sessions()
            try:
                existing = self._open_by_key(side, db, key) if key else None
                if existing:
                    sid = existing
                    text = (_update_text(body, accept_prompt, now, prompt_label) if legacy
                            else _typed_update_text(body, snap, fvals, now))
                    db.append_message(sid, "assistant", text)
                    side.execute("UPDATE items SET title=?, poster=COALESCE(?, poster), accept_prompt=?, "
                                 "updated_at=?, type=?, fields_json=?, actions_json=? WHERE session_id=?",
                                 (title, poster, accept_prompt, now, tname, fjson, ajson, sid))
                    side.commit()
                    _set_title(db, sid, title)
                    db.set_session_read(sid, False)
                    return {"session_id": sid, "updated": True}
                talking = self._accepted_by_key(side, db, key) if key and not decision else None
                if talking:  # the user replied to this card: the follow-up joins that conversation
                    db.append_message(talking, "assistant", _update_text(body, accept_prompt, now, prompt_label)
                                      if legacy else _typed_update_text(body, snap, fvals, now))
                    side.execute("UPDATE items SET updated_at=? WHERE session_id=?", (now, talking))
                    side.commit()
                    db.set_session_read(talking, False)
                    return {"session_id": talking, "updated": True, "conversation": True}
                ignored = self._ignored_streak(side, db, poster, now) if poster else 0
                if poster and ignored >= MUTE_AFTER:
                    self._file_mute_card(poster, title, body)
                    raise PosterMuted(
                        f"poster {poster!r} is muted: its last {ignored} inbox cards were archived with no "
                        f"action, so its posts are noise to David. Route what it reports to a Kanban card "
                        f"(an agent fixes it) and post to the Inbox only a decision David alone can make. "
                        f"Unmute after fixing the poster: `hermes inbox unmute {poster!r}`.")
                from hermes_state_ids import new_session_id
                sid = new_session_id()
                rt = routing_module()
                cwd_explicit = int(cwd is not None)
                cwd = rt.route_cwd(self.home, explicit=cwd, inherited=inherited_cwd, social=rt.is_social(
                    key=key, type=type, title=title, body=body, poster=poster), poster=poster)
                db.create_session(sid, "desktop", model=model, cwd=cwd or str(Path.home()))
                db.set_session_hidden(sid, True)
                text = (_post_text(title, body, accept_prompt, poster, prompt_label) if legacy
                        else _typed_post_text(title, body, poster, snap, fvals))
                db.append_message(sid, "assistant", text)
                _set_title(db, sid, title)
                db.set_session_read(sid, False)
                side.execute("INSERT INTO items(session_id,key,title,poster,accept_prompt,created_at,updated_at,state,"
                             "type,fields_json,actions_json,cwd_explicit) VALUES (?,?,?,?,?,?,?, 'open',?,?,?,?)",
                             (sid, key, title, poster, accept_prompt, now, now, tname, fjson, ajson, cwd_explicit))
                side.commit()
                self._enforce_cap(side, db)
                return {"session_id": sid, "updated": False}
            finally:
                side.close()
                self._close(db)

    @contextmanager
    def _atomic(self):
        """Lock BOTH SQLite writers before reading either store (including core-only writers).

        ATTACH precedes BEGIN IMMEDIATE: this reserves both databases, not a second-read
        approximation or a process-local lock. WAL gives concurrency atomicity and exception
        rollback, but SQLite does NOT promise cross-file power-loss atomicity in WAL mode.
        Keep SessionDB open for its schema setup and WAL generation guard.
        """
        with _lock:
            side = self._side()
            side.close()
            db = self._sessions()
            conn = None
            try:
                conn = sqlite3.connect(str(db.db_path), timeout=30, isolation_level=None)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("ATTACH DATABASE ? AS inbox", (str(self.path),))
                conn.execute("BEGIN IMMEDIATE")
                yield conn, db
                conn.commit()
            except BaseException:
                if conn is not None:
                    conn.rollback()
                raise
            finally:
                if conn is not None:
                    conn.close()
                self._close(db)

    @staticmethod
    def _snapshot(conn, key):
        row = conn.execute("SELECT * FROM inbox.items WHERE key=? ORDER BY updated_at DESC, "
                           "created_at DESC, rowid DESC LIMIT 1", (key,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (item["session_id"],)).fetchone()
        session = dict(row) if row else None
        messages = [dict(r) for r in conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id", (item["session_id"],))]
        # Full content, not merely a count/timestamp: edits and deletes also invalidate the token.
        revision = hashlib.sha256(json.dumps([item, session, messages], sort_keys=True,
                                             default=repr).encode()).hexdigest()
        state, outcome = item["state"], item.get("outcome")
        if state == "queuing":
            state, outcome = "open", None
        elif state in ("open", "accepted"):
            if not session or session.get("archived"):
                state, outcome = "archived", outcome or "archived"
            elif state == "open" and not session.get("hidden"):
                state = "accepted"
        elif state == "archived":
            outcome = outcome or "archived"
        return {"key": item["key"], "session_id": item["session_id"], "state": state,
                "outcome": outcome, "value": None if item["state"] == "queuing" else item.get("outcome_value"),
                "fields": item_fields(item), "at": (item["updated_at"] if item["state"] == "queuing" else
                                                     item.get("resolved_at") or item["updated_at"]),
                "type": item.get("type") or "default", "title": item["title"],
                "task": None if item["state"] == "queuing" else item.get("outcome_task"), "revision": revision,
                "item_state": item["state"], "updated_at": item["updated_at"],
                "message_count": len(messages),
                "last_message_id": messages[-1]["id"] if messages else None}

    def _conditional_post(self, *, title, body, accept_prompt, key, poster, cwd,
                          inherited_cwd, model, prompt_label, snap, fvals, legacy, now,
                          if_absent, expected):
        with self._atomic() as (conn, db):
            current = self._snapshot(conn, key)
            conflict = (current is not None) if if_absent else (
                current is None or current["state"] != "open" or current["item_state"] != "open" or
                any(current[k] != expected[k] for k in ("key", "session_id", "revision")))
            if conflict:
                return {"applied": False, "reason": "exists" if if_absent else "changed",
                        "snapshot": current}
            # Reuse core message encoding/counters/guards on THIS transaction, never a nested
            # SessionDB commit. All helper calls receive conn explicitly.
            class Writer:
                def __getattr__(self, name):
                    return getattr(db, name)

                def _execute_write(self, fn, **kwargs):
                    return fn(conn)

                def get_session(self, sid):
                    r = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
                    return dict(r) if r else None

            writer: Any = Writer()
            if current is None:
                if poster and self._ignored_streak(conn, writer, poster, now) >= MUTE_AFTER:
                    raise PosterMuted(f"poster {poster!r} is muted; fix it before unmuting")
                from hermes_state_ids import new_session_id
                sid = new_session_id()
                rt = routing_module()
                routed = rt.route_cwd(self.home, explicit=cwd, inherited=inherited_cwd,
                                      social=rt.is_social(key=key, type=snap["name"], title=title,
                                                          body=body, poster=poster),
                                      poster=poster)
                type(db)._insert_session_row(writer, sid, "desktop", model=model,
                                             cwd=routed or str(Path.home()))
                conn.execute("UPDATE sessions SET hidden=1 WHERE id=?", (sid,))
                conn.execute("INSERT INTO inbox.items(session_id,key,title,poster,accept_prompt,"
                             "created_at,updated_at,state,type,fields_json,actions_json,cwd_explicit) "
                             "VALUES (?,?,?,?,?,?,?,'open',?,?,?,?)",
                             (sid, key, title, poster, accept_prompt, now, now, snap["name"],
                              json.dumps(fvals), json.dumps(snap), int(cwd is not None)))
                text = (_post_text(title, body, accept_prompt, poster, prompt_label) if legacy else
                        _typed_post_text(title, body, poster, snap, fvals))
            else:
                sid = current["session_id"]
                conn.execute("UPDATE inbox.items SET title=?,poster=COALESCE(?,poster),accept_prompt=?,"
                             "updated_at=?,type=?,fields_json=?,actions_json=? WHERE session_id=?",
                             (title, poster, accept_prompt, now, snap["name"], json.dumps(fvals),
                              json.dumps(snap), sid))
                text = (_update_text(body, accept_prompt, now, prompt_label) if legacy else
                        _typed_update_text(body, snap, fvals, now))
            db._check_transcript_write_guards(conn, sid, None, reject_active_turn_lease=True)
            type(db).append_message(writer, sid, "assistant", text)
            # Match the legacy title collision fallback without reading outside the transaction.
            for n in range(1, 50):
                candidate = title if n == 1 else f"{title} ({n})"
                if not conn.execute("SELECT 1 FROM sessions WHERE title=? AND id<>?",
                                    (candidate, sid)).fetchone():
                    conn.execute("UPDATE sessions SET title=?,title_source='user' WHERE id=?",
                                 (candidate, sid))
                    break
            conn.execute("UPDATE sessions SET last_read_at=0 WHERE id=?", (sid,))
            if current is None:
                items = self._open_items(conn, writer)
                extra = max(0, len(items) - MAX_OPEN)
                read = sorted((i for i in items if not i["unread"]), key=lambda i: i["updated_at"])
                for item in read[:extra]:
                    conn.execute("UPDATE sessions SET archived=1 WHERE id=?", (item["session_id"],))
                    conn.execute("UPDATE inbox.items SET state='archived',outcome=COALESCE(outcome,'archived') "
                                 "WHERE session_id=?", (item["session_id"],))
            return {"applied": True, "session_id": sid, "updated": current is not None,
                    "snapshot": self._snapshot(conn, key)}

    def _ignored_streak(self, side, db, poster: str, now: float) -> int:
        """How many of *poster*'s most recent closed cards (inside MUTE_WINDOW_S, after its last unmute)
        in a row were archived with no action. Accepted (Do it / a reply) or resolved breaks the streak;
        cards still open don't count either way."""
        row = side.execute("SELECT unmuted_at FROM unmutes WHERE poster=?", (poster,)).fetchone()
        since = max(now - MUTE_WINDOW_S, float(row["unmuted_at"]) if row else 0.0)
        streak = 0
        for r in side.execute("SELECT session_id, state FROM items WHERE poster=? AND created_at>=? "
                              "ORDER BY created_at DESC LIMIT 50", (poster, since)):
            if r["state"] in ("accepted", "resolved", "queuing"):
                break
            s = db.get_session(r["session_id"])
            if s and not s.get("archived") and not s.get("hidden"):
                break  # un-hidden through another door = David engaged with it
            if r["state"] == "archived" or not s or s.get("archived"):
                streak += 1
                if streak >= MUTE_AFTER:
                    break
        return streak

    def _file_mute_card(self, poster: str, title: str, body: str) -> None:
        """One Kanban card per muted poster (idempotent): an agent fixes the poster's signal."""
        try:
            (self._task_creator or kanban_task_creator)(
                title=f"Inbox poster muted as noise: {poster}"[:120],
                body=(f"David archived the last {MUTE_AFTER} Hermes Inbox cards from `{poster}` with no "
                      f"action, so the inbox now refuses its posts. Find the code that posts as this poster "
                      f"and change it: problems an agent can fix go to a Kanban card (assignee default), "
                      f"routine status goes nowhere, and only a decision David alone can make goes to the "
                      f"Inbox. Then run `hermes inbox unmute '{poster}'` on the gateway that refused it.\n\n"
                      f"Refused post:\n\n**{title}**\n\n{body[:3000]}"),
                assignee="default", idempotency_key=f"inbox-muted:{poster}")
        except Exception as exc:
            # The refusal itself is the guard; the card is best-effort but never silent: a Kanban worker's
            # terminal is fenced from board writes (HERMES_DELEGATED_CHILD_CONTEXT) and used to hide that here.
            msg = (f"inbox: poster {poster!r} is muted but the Kanban fix card (inbox-muted:{poster}) could "
                   f"NOT be filed: {type(exc).__name__}: {str(exc)[:200]}. File it from outside a worker "
                   f"fence (hub cron / kanban_create).")
            try:
                import logging
                logging.getLogger(__name__).warning(msg)
            except Exception:
                pass
            try:
                print(msg, file=sys.stderr)
            except Exception:
                pass

    def unmute(self, poster: str) -> None:
        with _lock:
            side = self._side()
            try:
                side.execute("INSERT INTO unmutes(poster, unmuted_at) VALUES (?, ?) ON CONFLICT(poster) "
                             "DO UPDATE SET unmuted_at=excluded.unmuted_at", (poster, self.clock()))
                side.commit()
            finally:
                side.close()

    def _open_by_key(self, side, db, key) -> Optional[str]:
        for row in side.execute("SELECT session_id FROM items WHERE key=? AND state='open' "
                                "ORDER BY updated_at DESC", (key,)):
            s = db.get_session(row["session_id"])
            if s and not s.get("archived") and s.get("hidden"):
                return row["session_id"]
        return None

    def _accepted_by_key(self, side, db, key) -> Optional[str]:
        for row in side.execute("SELECT session_id FROM items WHERE key=? AND state='accepted' "
                                "ORDER BY updated_at DESC", (key,)):
            s = db.get_session(row["session_id"])
            if s and not s.get("archived"):
                return row["session_id"]
        return None

    def _enforce_cap(self, side, db) -> None:
        items = self._open_items(side, db)
        extra = len(items) - MAX_OPEN
        if extra <= 0:
            return
        read = sorted((i for i in items if not i["unread"]), key=lambda i: i["updated_at"])
        for item in read[:extra]:
            db.set_session_archived(item["session_id"], True)
            side.execute("UPDATE items SET state='archived', outcome=COALESCE(outcome, 'archived') "
                         "WHERE session_id=?", (item["session_id"],))
        side.commit()

    # ── reading ─────────────────────────────────────────────────────────────
    def _open_items(self, side, db) -> list[dict[str, Any]]:
        out = []
        for row in side.execute("SELECT * FROM items WHERE state='open' ORDER BY updated_at DESC"):
            s = db.get_session(row["session_id"])
            if not s:
                continue
            if s.get("archived") or not s.get("hidden"):
                # Archived / un-hidden through another door: the inbox follows the session.
                continue
            last_read = s.get("last_read_at")
            item = dict(row)
            item["unread"] = float(last_read or 0) < float(row["updated_at"])
            item["session_title"] = s.get("title") or row["title"]
            out.append(item)
        return out

    def list(self) -> list[dict[str, Any]]:
        if not self.exists():
            return []
        with _lock:
            side = self._side()
            db = self._reader()
            try:
                return self._open_items(side, db)
            finally:
                side.close()
                self._close(db)

    def get(self, session_id: str) -> Optional[dict[str, Any]]:
        if not self.exists():
            return None
        side = self._side()
        try:
            row = side.execute("SELECT * FROM items WHERE session_id=?", (session_id,)).fetchone()
            return dict(row) if row else None
        finally:
            side.close()

    # ── actions ─────────────────────────────────────────────────────────────
    def accept(self, session_id: str) -> Optional[dict[str, Any]]:
        """Turn an open message into a normal chat (un-hide → Recents). Returns the item, or None
        when the id is not an open inbox message."""
        with _lock:
            item = self.get(session_id)
            if not item or item["state"] != "open":
                return None
            side = self._side()
            try:
                # Claim the transition in the sidecar first (state='open' guard): another process (the
                # dashboard, the CLI, an agent's accept hook) may be acting on the same card.
                won = side.execute("UPDATE items SET state='accepted' WHERE session_id=? AND state='open'",
                                   (session_id,)).rowcount == 1
                side.commit()
            finally:
                side.close()
            if not won:
                return None
            db = self._sessions()
            try:
                db.set_session_hidden(session_id, False)
            finally:
                self._close(db)
            item["state"] = "accepted"
            return item

    def archive(self, session_id: str) -> bool:
        with _lock:
            item = self.get(session_id)
            if not item:
                return False
            db = self._sessions()
            side = self._side()
            try:
                db.set_session_archived(session_id, True)
                # Archive = dismissed with no decision; an already-resolved card keeps its outcome.
                side.execute("UPDATE items SET state=CASE WHEN state IN ('resolved','queuing') THEN state ELSE 'archived' END, "
                             "outcome=COALESCE(outcome, 'archived'), resolved_at=COALESCE(resolved_at, ?) "
                             "WHERE session_id=?", (self.clock(), session_id))
                side.commit()
            finally:
                side.close()
                self._close(db)
            return True

    def act(self, session_id: str, action_id: Optional[str] = None, input: Optional[str] = None
            ) -> Optional[dict[str, Any]]:
        """Run one of the card's actions. ``action_id`` None = its first ``prompt`` action (the old
        Do it). Returns the item plus ``action``/``kind`` and, for prompt actions, the rendered
        ``prompt`` (also in ``accept_prompt``); None when *session_id* is not an open card.
        Raises ValueError for an unknown action or bad input."""
        ct = types_module()
        with _lock:
            item = self.get(session_id)
            if not item:
                return None
            stale_claim = (item["state"] == "queuing"
                           and self.clock() - float(item.get("resolved_at") or 0) > STALE_QUEUING_S)
            if item["state"] != "open" and not stale_claim:
                return None
            claim_from = ("state='queuing' AND resolved_at=?", item.get("resolved_at")) if stale_claim else (
                "state='open'", None)
            if action_id == "archive":
                self.archive(session_id)
                return dict(self.get(session_id) or item, action="archive", kind="archive")
            snap = item_type(item)
            actions = snap["actions"]
            if action_id is None:
                act = next((a for a in actions if a["kind"] == "prompt"), None)
                if not act:
                    raise ValueError(f"card type {snap['name']} has no prompt action to accept")
            else:
                act = next((a for a in actions if a["id"] == action_id), None)
                if not act:
                    raise ValueError(f"card type {snap['name']} has no action {action_id!r} "
                                     f"(actions: {', '.join([a['id'] for a in actions] + ['archive'])})")
            spec = act.get("input")
            value = None if input is None else str(input)
            if spec is None:
                value = None
            else:
                if spec.get("required", True) and not (value or "").strip():
                    raise ValueError(f"action {act['id']} needs an input value")
                mx = spec.get("max") or ct.MAX_FIELD_VALUE
                if value is not None and len(value) > mx:
                    raise ValueError(f"input is {len(value)} chars, over the max of {mx}")
            fields = item_fields(item)
            if spec and spec.get("field") and value is not None:
                fields[spec["field"]] = value
                ct.check_fields(dict(snap, fields=snap.get("fields")), fields)  # re-check max lengths
            now = self.clock()
            prompt = None
            if act["kind"] == "prompt":
                prompt = ct.render(act["prompt"], dict(fields, input=value or "", key=item.get("key") or "",
                                                       title=item.get("title") or "",
                                                       accept_prompt=item.get("accept_prompt") or ""))
                if len(prompt) > ct.MAX_RENDERED:
                    raise ValueError(f"rendered prompt is {len(prompt)} chars, over {ct.MAX_RENDERED}")
            state = "accepted" if prompt is not None else "resolved"
            task_args = None
            if act["kind"] == "task":
                ctx = dict(fields, input=value or "", key=item.get("key") or "", title=item.get("title") or "",
                           accept_prompt=item.get("accept_prompt") or "")
                body = ct.render(act["prompt"], ctx)
                if len(body) > ct.MAX_RENDERED:
                    raise ValueError(f"rendered task prompt is {len(body)} chars, over {ct.MAX_RENDERED}")
                ttl = " ".join(ct.render(act["title"], ctx).split()) if act.get("title") else (
                    f"{snap.get('label') or snap['name']}: {act['label']} \u2014 {item.get('title') or ''}")
                task_args = dict(title=ttl[:ct.MAX_TITLE].strip() or act["label"], body=body,
                                 assignee=act.get("assignee") or "default", board=act.get("board"),
                                 skills=act.get("skills"), idempotency_key=f"inbox:{session_id}:{act['id']}")
            old_fields = item.get("fields_json")
            # A task action is claimed as 'queuing' (readers still see an open card, no decision) until
            # its Kanban card exists; only then does it become 'resolved'.
            claim_state = "queuing" if task_args is not None else state
            side = self._side()
            try:
                # Claim the transition first (state guard, atomic across processes), then touch the
                # session row only if this caller won.
                won = side.execute("UPDATE items SET state=?, outcome=?, outcome_value=?, fields_json=?, "
                                   f"resolved_at=? WHERE session_id=? AND {claim_from[0]}",
                                   (claim_state, act["id"], value, json.dumps(fields), now, session_id,
                                    *([claim_from[1]] if claim_from[1] is not None else []))).rowcount == 1
                side.commit()
            finally:
                side.close()
            if not won:
                return None
            if task_args is not None:
                # The decision is claimed; queue the work. If that fails, reopen the card so a decision
                # is never recorded for work that was not queued. The idempotency key makes a retry
                # (or a crash between create and record) reuse the card instead of duplicating it.
                try:
                    tid = (self._task_creator or kanban_task_creator)(**task_args)
                    if not tid:
                        raise TaskError("kanban returned no task id")
                except Exception as exc:
                    side = self._side()
                    try:  # only our own claim is reverted (state + stamp), never a later decision
                        side.execute("UPDATE items SET state='open', outcome=NULL, outcome_value=NULL, "
                                     "fields_json=?, resolved_at=NULL, outcome_task=NULL "
                                     "WHERE session_id=? AND state='queuing' AND resolved_at=?",
                                     (old_fields, session_id, now))
                        side.commit()
                    finally:
                        side.close()
                    if type(exc).__name__ == "TaskError":
                        raise
                    raise TaskError(f"could not queue the Kanban task: {type(exc).__name__}: {exc}") from exc
                # The work is queued: the decision stands even if recording the id fails (a stale
                # 'queuing' claim is retried with the same idempotency key and finds this card).
                for attempt in (1, 2):
                    try:
                        side = self._side()
                        try:
                            side.execute("UPDATE items SET state='resolved', outcome_task=? "
                                         "WHERE session_id=? AND state='queuing' AND resolved_at=?",
                                         (str(tid), session_id, now))
                            side.commit()
                        finally:
                            side.close()
                        break
                    except sqlite3.Error:
                        if attempt == 2:
                            import logging
                            logging.getLogger(__name__).warning("inbox: queued %s for %s but could not record it",
                                                                tid, session_id)
                item["outcome_task"] = str(tid)
            db = self._sessions()
            try:
                if prompt is not None:
                    db.set_session_hidden(session_id, False)
                    item.update(prompt=prompt, accept_prompt=prompt)
                else:
                    db.set_session_archived(session_id, True)
            finally:
                self._close(db)
            item.update(state=state, resolved_at=now)
            item.update(outcome=act["id"], outcome_value=value, fields_json=json.dumps(fields),
                        action=act["id"], kind=act["kind"])
            return item

    def outcome(self, key: str) -> Optional[dict[str, Any]]:
        """What happened to the newest card with *key*: {key, state, outcome, value, fields, at,
        session_id, type}. state is open / accepted / resolved / archived, derived from the session
        too (archived from the sidebar = archived). None when no card ever had that key."""
        if not key or not self.exists():
            return None
        with self._atomic() as (conn, _db):
            return self._snapshot(conn, str(key))

    def on_user_turn(self, session_id: str) -> bool:
        """A user turn started in *session_id*: if it is an open message, accept it."""
        if not session_id or not self.exists():
            return False
        item = self.get(session_id)
        if not item or item["state"] != "open":
            return False
        return self.accept(session_id) is not None


def _set_title(db, sid: str, title: str) -> None:
    for n in range(1, 50):
        candidate = title if n == 1 else f"{title} ({n})"
        try:
            db.set_session_title(sid, candidate)
            return
        except ValueError:
            continue


def _do_it_line(accept_prompt: str, prompt_label: Optional[str]) -> str:
    """With a short label the full prompt stays out of the card (Do it still sends all of it)."""
    return f"**Do it:** {prompt_label}" if prompt_label else f"**Proposed:** {accept_prompt}"


def _post_text(title: str, body: str, accept_prompt: str, poster: Optional[str],
               prompt_label: Optional[str] = None) -> str:
    parts = [f"**{title}**"]
    if body:
        parts.append(body)
    parts.append(_do_it_line(accept_prompt, prompt_label))
    if prompt_label:
        parts.append(f"_From {poster or 'Hermes'}. Reply to adjust it, or archive to dismiss._")
        return "\n\n".join(parts)
    parts.append(f"_Inbox message{f' from {poster}' if poster else ''}. Press **Do it** to run the "
                 "proposal, reply to adjust it, or archive to dismiss._")
    return "\n\n".join(parts)


def _update_text(body: str, accept_prompt: str, now: float, prompt_label: Optional[str] = None) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    parts = [f"**Update {stamp}**"]
    if body:
        parts.append(body)
    parts.append(_do_it_line(accept_prompt, prompt_label))
    return "\n\n".join(parts)


# ── typed cards ─────────────────────────────────────────────────────────────
def item_type(item: dict[str, Any]) -> dict[str, Any]:
    """The card's type snapshot; legacy rows (no snapshot) are the built-in default type."""
    raw = item.get("actions_json")
    if raw:
        try:
            snap = json.loads(raw)
            if isinstance(snap, dict) and isinstance(snap.get("actions"), list):
                return snap
        except ValueError:
            pass
    ct = types_module()
    return ct.snapshot(ct.validate(dict(ct.DEFAULT)))


def item_fields(item: dict[str, Any]) -> dict[str, str]:
    try:
        f = json.loads(item.get("fields_json") or "{}")
        return f if isinstance(f, dict) else {}
    except ValueError:
        return {}


def public_item(item: dict[str, Any]) -> dict[str, Any]:
    """REST/tool view: adds ``type``, ``type_label``, ``fields``, ``actions`` (Archive excluded:
    every card has it) and ``field_specs``; drops the raw json columns."""
    out = {k: v for k, v in item.items() if k not in ("fields_json", "actions_json")}
    snap = item_type(item)
    out["type"] = item.get("type") or "default"
    out["type_label"] = snap.get("label") or out["type"]
    out["fields"] = item_fields(item)
    out["field_specs"] = snap.get("fields") or {}
    out["actions"] = snap["actions"]
    return out


def _previews(snap: dict[str, Any], fields: dict[str, str]) -> list[str]:
    out = []
    for name, spec in (snap.get("fields") or {}).items():
        if not spec.get("preview") or name not in fields:
            continue
        val = fields[name]
        quoted = "\n".join("> " + ln if ln else ">" for ln in val.splitlines() or [""])
        mx = spec.get("max")
        count = f"{len(val)}/{mx} chars" if mx else f"{len(val)} chars"
        out.append(f"{quoted}\n\n_{spec.get('label') or name}: {count}_")
    return out


def _actions_line(snap: dict[str, Any], poster: Optional[str]) -> str:
    labels = [f"**{a['label']}**" for a in snap["actions"]] + ["**Archive**"]
    return (f"_{snap.get('label') or snap['name']}{f' from {poster}' if poster else ''}. "
            f"Actions: {', '.join(labels)}._")


def _typed_post_text(title: str, body: str, poster: Optional[str], snap: dict[str, Any],
                     fields: dict[str, str]) -> str:
    parts = [f"**{title}**"]
    if body:
        parts.append(body)
    parts += _previews(snap, fields)
    parts.append(_actions_line(snap, poster))
    return "\n\n".join(parts)


def _typed_update_text(body: str, snap: dict[str, Any], fields: dict[str, str], now: float) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    parts = [f"**Update {stamp}**"]
    if body:
        parts.append(body)
    parts += _previews(snap, fields)
    return "\n\n".join(parts)
