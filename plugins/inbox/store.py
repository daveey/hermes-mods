"""Hermes Inbox store.

An inbox message IS a session: a ``source='desktop'`` row created ``hidden`` (so it stays out
of Recents) whose first message is Hermes's proposal (role=assistant). A sidecar SQLite file,
``<HERMES_HOME>/inbox/inbox.db``, records which sessions are inbox messages plus their metadata
(dedupe key, poster, the prompt "Do it" sends).

Lifecycle, all derived from the session row so other doors (sidebar archive, restore) agree:
  open      sidecar state 'open', session hidden, not archived  -> listed in the Inbox
  accepted  the user ran it (Do it / typed a reply): session un-hidden -> a normal chat in Recents
  archived  session archived (restorable from the archived view)
Unread = the session's ``last_read_at`` watermark predates the message's last post.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

MAX_OPEN = 20          # past this, the oldest READ open messages are archived
MAX_TITLE = 120
MAX_BODY = 20_000
MAX_PROMPT = 4_000

_lock = threading.RLock()

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
"""


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


_PROFILE_NAME = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


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
                 clock: Callable[[], float] = time.time):
        self.home = Path(home) if home else _home()
        self.path = self.home / "inbox" / "inbox.db"
        self._db_factory = db_factory
        self.clock = clock

    @classmethod
    def for_home(cls, home: Path, **kw) -> "Inbox":
        """An inbox bound to *home*'s own sidecar AND state.db (another profile's home)."""
        home = Path(home)

        def factory():
            from hermes_state import SessionDB
            return SessionDB(db_path=home / "state.db")
        return cls(home=home, db_factory=factory, **kw)

    # ── plumbing ────────────────────────────────────────────────────────────
    def _side(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        return conn

    def _sessions(self):
        if self._db_factory:
            return self._db_factory()
        from hermes_state import SessionDB
        return SessionDB()

    @staticmethod
    def _close(db) -> None:
        if not getattr(db, "_inbox_keep_open", False):
            try:
                db.close()
            except Exception:
                pass

    def exists(self) -> bool:
        return self.path.exists()

    # ── posting ─────────────────────────────────────────────────────────────
    def post(self, title: str, body: str, accept_prompt: str, *, key: Optional[str] = None,
             poster: Optional[str] = None, cwd: Optional[str] = None,
             model: Optional[str] = None) -> dict[str, Any]:
        title = " ".join(str(title or "").split())[:MAX_TITLE]
        body = str(body or "").strip()[:MAX_BODY]
        accept_prompt = str(accept_prompt or "").strip()[:MAX_PROMPT]
        key = (str(key).strip() or None) if key else None
        poster = (" ".join(str(poster).split())[:80] or None) if poster else None
        if not title:
            raise ValueError("title is required")
        if not accept_prompt:
            raise ValueError("accept_prompt is required (the instruction 'Do it' sends)")
        now = self.clock()
        with _lock:
            side = self._side()
            db = self._sessions()
            try:
                existing = self._open_by_key(side, db, key) if key else None
                if existing:
                    sid = existing
                    db.append_message(sid, "assistant", _update_text(body, accept_prompt, now))
                    side.execute("UPDATE items SET title=?, poster=COALESCE(?, poster), accept_prompt=?, "
                                 "updated_at=? WHERE session_id=?", (title, poster, accept_prompt, now, sid))
                    side.commit()
                    _set_title(db, sid, title)
                    db.set_session_read(sid, False)
                    return {"session_id": sid, "updated": True}
                talking = self._accepted_by_key(side, db, key) if key else None
                if talking:  # the user replied to this card: the follow-up joins that conversation
                    db.append_message(talking, "assistant", _update_text(body, accept_prompt, now))
                    side.execute("UPDATE items SET updated_at=? WHERE session_id=?", (now, talking))
                    side.commit()
                    db.set_session_read(talking, False)
                    return {"session_id": talking, "updated": True, "conversation": True}
                from hermes_state_ids import new_session_id
                sid = new_session_id()
                db.create_session(sid, "desktop", model=model, cwd=cwd or str(Path.home()))
                db.set_session_hidden(sid, True)
                db.append_message(sid, "assistant", _post_text(title, body, accept_prompt, poster))
                _set_title(db, sid, title)
                db.set_session_read(sid, False)
                side.execute("INSERT INTO items(session_id,key,title,poster,accept_prompt,created_at,updated_at,state) "
                             "VALUES (?,?,?,?,?,?,?, 'open')", (sid, key, title, poster, accept_prompt, now, now))
                side.commit()
                self._enforce_cap(side, db)
                return {"session_id": sid, "updated": False}
            finally:
                side.close()
                self._close(db)

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
            side.execute("UPDATE items SET state='archived' WHERE session_id=?", (item["session_id"],))
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
            db = self._sessions()
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
            db = self._sessions()
            side = self._side()
            try:
                db.set_session_hidden(session_id, False)
                side.execute("UPDATE items SET state='accepted' WHERE session_id=?", (session_id,))
                side.commit()
            finally:
                side.close()
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
                side.execute("UPDATE items SET state='archived' WHERE session_id=?", (session_id,))
                side.commit()
            finally:
                side.close()
                self._close(db)
            return True

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


def _post_text(title: str, body: str, accept_prompt: str, poster: Optional[str]) -> str:
    parts = [f"**{title}**"]
    if body:
        parts.append(body)
    parts.append(f"**Proposed:** {accept_prompt}")
    parts.append(f"_Inbox message{f' from {poster}' if poster else ''}. Press **Do it** to run the "
                 "proposal, reply to adjust it, or archive to dismiss._")
    return "\n\n".join(parts)


def _update_text(body: str, accept_prompt: str, now: float) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    parts = [f"**Update {stamp}**"]
    if body:
        parts.append(body)
    parts.append(f"**Proposed:** {accept_prompt}")
    return "\n\n".join(parts)
