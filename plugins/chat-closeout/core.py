"""chat-closeout logic: the waiting ledger, card settlement, the close-out decision, the promise lint and the
watchdog. No Hermes imports at module level; everything that touches Hermes is passed in or imported lazily, so the
tests run on throwaway SQLite files."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger("chat_closeout")

# A card the chat no longer waits on for work: finished, or parked on a human.
DONE_STATUSES = ("done", "archived")
HUMAN_STATUSES = ("review",)
HUMAN_BLOCK_KINDS = (None, "", "needs_input", "capability")
USER_QUIET_S = 300.0          # skip a wake while the user typed in the chat this recently
STALE_S = 1800.0              # watchdog: unseen results / missing close-out older than this
WATCHDOG_EVERY_S = 900.0
NOTICE_RE = re.compile(r"^\s*(?:[✔⏸✖⏱🔄]\s*\[|\[chat-closeout\])")
TAG = "[chat-closeout]"


# ---- sqlite helpers --------------------------------------------------------------------------------------------
def ro_connect(path: Path, timeout: float = 2.0) -> sqlite3.Connection:
    """Read-only open that survives a WAL db whose -wal/-shm were deleted (mode=rw + query_only fallback)."""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)
        c.execute("select 1 from sqlite_master limit 1")
    except sqlite3.OperationalError:
        c = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=timeout)
        c.execute("PRAGMA query_only=ON")
    c.row_factory = sqlite3.Row
    return c


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


# ---- cards -----------------------------------------------------------------------------------------------------
def read_cards(board_paths: Iterable[Path], ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """``{id: {id, status, block_kind, title, summary, completed_at, board}}`` for the ids found on any board."""
    want = sorted({str(i) for i in ids if i})
    found: dict[str, dict[str, Any]] = {}
    if not want:
        return found
    for path in board_paths:
        left = [i for i in want if i not in found]
        if not left:
            break
        try:
            conn = ro_connect(Path(path))
        except sqlite3.Error:
            continue
        try:
            cols = _cols(conn, "tasks")
            bk = "t.block_kind" if "block_kind" in cols else "NULL"
            comp = "t.completed_at" if "completed_at" in cols else "NULL"
            res = "t.result" if "result" in cols else "NULL"
            has_runs = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_runs'").fetchone()
            summary = (f"COALESCE((SELECT r.summary FROM task_runs r WHERE r.task_id = t.id AND r.summary IS NOT NULL "
                       f"AND r.summary != '' ORDER BY r.id DESC LIMIT 1), {res})") if has_runs else res
            ph = ",".join("?" for _ in left)
            for r in conn.execute(f"SELECT t.id, t.status, t.title, {bk} AS block_kind, {comp} AS completed_at, "
                                  f"{summary} AS summary FROM tasks t WHERE t.id IN ({ph})", left):
                board = Path(path).parent.name if Path(path).parent.parent.name == "boards" else "default"
                found[r["id"]] = {**dict(r), "board": board}
        except sqlite3.Error as exc:
            logger.debug("chat-closeout: card read failed on %s: %s", path, exc)
        finally:
            conn.close()
    return found


def is_settled(card: Optional[dict[str, Any]]) -> bool:
    """The chat no longer waits on this card's WORK: done/archived, in review, or blocked on a human. A card that
    missing from a board read is unknown, never proof of completion."""
    if card is None:
        return False
    status = card.get("status")
    if status in DONE_STATUSES or status in HUMAN_STATUSES:
        return True
    if status in ("blocked", "triage"):
        kind = card.get("block_kind")
        return kind in HUMAN_BLOCK_KINDS if status == "blocked" else kind in ("needs_input", "capability")
    return False


def needs_human(card: Optional[dict[str, Any]]) -> bool:
    return bool(card) and is_settled(card) and card.get("status") not in DONE_STATUSES


def settled_sig(batch: Iterable[str], cards: dict[str, dict[str, Any]]) -> Optional[str]:
    """Signature of the batch's settled state, or None while any card is still working."""
    ids = sorted(set(batch))
    if not ids or not all(is_settled(cards.get(i)) for i in ids):
        return None
    raw = ";".join(f"{i}={(cards.get(i) or {}).get('status', 'gone')}" for i in ids)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# ---- the ledger ------------------------------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS waiting (
  session_id TEXT PRIMARY KEY,
  batch TEXT NOT NULL DEFAULT '[]',      -- every card the chat handed work to since its last close-out
  open TEXT NOT NULL DEFAULT '[]',       -- waiting_on at the last turn end
  stamped_at REAL,
  settled_sig TEXT, settled_at REAL,     -- first time the batch was seen fully settled (this sig)
  done_sig TEXT, done_at REAL,           -- a turn ended after the batch settled: the close-out happened
  claim_sig TEXT, claim_at REAL, claim_pid INTEGER, wake_result TEXT
);
CREATE TABLE IF NOT EXISTS unseen_seen (session_id TEXT PRIMARY KEY, since REAL NOT NULL);
CREATE TABLE IF NOT EXISTS flags (key TEXT PRIMARY KEY, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


class Ledger:
    """Per-chat waiting ledger in its own SQLite file; safe across processes (every write is one statement or a
    short IMMEDIATE transaction)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _conn(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(_SCHEMA)
        return c

    def get(self, sid: str) -> Optional[dict[str, Any]]:
        with self._lock:
            c = self._conn()
            try:
                r = c.execute("SELECT * FROM waiting WHERE session_id = ?", (sid,)).fetchone()
            finally:
                c.close()
        return _row(r)

    def pending(self) -> list[dict[str, Any]]:
        """Rows still holding a batch (not closed out)."""
        with self._lock:
            c = self._conn()
            try:
                rows = c.execute("SELECT * FROM waiting WHERE batch != '[]'").fetchall()
            finally:
                c.close()
        return [_row(r) for r in rows]

    def write(self, sid: str, **fields: Any) -> None:
        enc = {k: (json.dumps(sorted(v)) if k in ("batch", "open") else v) for k, v in fields.items()}
        cols = ", ".join(enc)
        sets = ", ".join(f"{k}=excluded.{k}" for k in enc)
        with self._lock:
            c = self._conn()
            try:
                c.execute(f"INSERT INTO waiting (session_id, {cols}) VALUES (?, {', '.join('?' for _ in enc)}) "
                          f"ON CONFLICT(session_id) DO UPDATE SET {sets}", (sid, *enc.values()))
            finally:
                c.close()

    def claim(self, sid: str, sig: str, now: float) -> bool:
        """CAS: exactly one process wins the wake for (chat, settled state)."""
        with self._lock:
            c = self._conn()
            try:
                cur = c.execute("UPDATE waiting SET claim_sig=?, claim_at=?, claim_pid=?, wake_result='claimed' "
                                "WHERE session_id=? AND COALESCE(claim_sig,'') != ? AND COALESCE(done_sig,'') != ?",
                                (sig, now, os.getpid(), sid, sig, sig))
                return cur.rowcount == 1
            finally:
                c.close()

    def release(self, sid: str, sig: str, result: str) -> None:
        with self._lock:
            c = self._conn()
            try:
                c.execute("UPDATE waiting SET claim_sig=NULL, wake_result=? WHERE session_id=? AND claim_sig=?",
                          (result, sid, sig))
            finally:
                c.close()

    def has_flag(self, key: str) -> bool:
        with self._lock:
            c = self._conn()
            try:
                return c.execute("SELECT 1 FROM flags WHERE key=?", (key,)).fetchone() is not None
            finally:
                c.close()

    def flag_once(self, key: str, now: float) -> bool:
        with self._lock:
            c = self._conn()
            try:
                return c.execute("INSERT OR IGNORE INTO flags (key, at) VALUES (?, ?)", (key, now)).rowcount == 1
            finally:
                c.close()

    def lease(self, name: str, every_s: float, now: float) -> bool:
        """At most one process runs ``name`` per ``every_s``."""
        with self._lock:
            c = self._conn()
            try:
                c.execute("BEGIN IMMEDIATE")
                r = c.execute("SELECT v FROM meta WHERE k=?", (name,)).fetchone()
                if r and now - float(r["v"] or 0) < every_s:
                    c.execute("COMMIT")
                    return False
                c.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                          (name, str(now)))
                c.execute("COMMIT")
                return True
            finally:
                c.close()

    def unseen_since(self, current: dict[str, int], now: float) -> dict[str, float]:
        """Track since when each chat has had unseen kanban results; returns ``{sid: since}`` for current ones."""
        with self._lock:
            c = self._conn()
            try:
                have = {r["session_id"]: r["since"] for r in c.execute("SELECT * FROM unseen_seen")}
                for sid in set(have) - set(current):
                    c.execute("DELETE FROM unseen_seen WHERE session_id=?", (sid,))
                for sid in set(current) - set(have):
                    c.execute("INSERT OR IGNORE INTO unseen_seen VALUES (?, ?)", (sid, now))
                    have[sid] = now
            finally:
                c.close()
        return {sid: have[sid] for sid in current}


def pending_closeouts(ledger: "Ledger", read: Callable[[list[str]], dict]) -> dict[str, list[str]]:
    """``{session_id: batch ids}`` for chats whose whole batch has settled but whose close-out turn has not run for
    that state: the sidebar keeps them grey "waiting" until the closing answer lands."""
    out: dict[str, list[str]] = {}
    for row in ledger.pending():
        sig = settled_sig(row["batch"], read(row["batch"]))
        if sig and sig != row.get("done_sig"):
            out[row["session_id"]] = sorted(row["batch"])
    return out


def _row(r) -> Optional[dict[str, Any]]:
    if r is None:
        return None
    d = dict(r)
    for k in ("batch", "open"):
        try:
            d[k] = list(json.loads(d.get(k) or "[]"))
        except ValueError:
            d[k] = []
    return d


# ---- turn end --------------------------------------------------------------------------------------------------
def record_turn_end(ledger: Ledger, sid: str, open_ids: Iterable[str], read: Callable[[list[str]], dict],
                    now: Optional[float] = None, observed_sig: Optional[str] = None) -> dict[str, Any]:
    """Stamp ``waiting_on`` for the chat at a turn end. Every card it handed work to joins the batch; once the batch
    is fully settled, clear it only if this turn received that exact handoff signature before inference.
    Completion during an unrelated turn must not silently acknowledge unseen handoffs."""
    now = time.time() if now is None else now
    row = ledger.get(sid) or {}
    open_now = set(open_ids)
    # Old-lineage cards (compression rotated the id) are still in the batch: keep them open until settled.
    batch = set(row.get("batch") or []) | open_now
    cards = read(sorted(batch)) if batch else {}
    open_now |= {i for i in batch if not is_settled(cards.get(i))}
    sig = settled_sig(batch, cards)
    fields: dict[str, Any] = {"open": open_now, "stamped_at": now}
    if sig and sig != row.get("settled_sig"):
        fields.update(settled_sig=sig, settled_at=now)  # the watchdog's "settled since" clock
    if sig and sig == observed_sig:
        fields.update(done_sig=sig, done_at=now)
        if not any(needs_human(cards.get(i)) for i in batch):
            batch = set()  # all done and this turn closed it out
    fields["batch"] = batch
    if not batch and not row:
        return {"open": [], "batch": []}
    ledger.write(sid, **fields)
    return {**fields, "open": sorted(open_now), "batch": sorted(batch), "sig": sig}


# ---- close-out decision ----------------------------------------------------------------------------------------
def closeout_due(row: dict[str, Any], cards: dict[str, dict[str, Any]], *, unseen: int, last_user_at: float,
                 now: float) -> Optional[str]:
    """The settled signature when this chat needs a close-out turn now, else None. Rules: every batch card settled;
    no close-out turn for this state yet (``done_sig``) and nobody claimed it (``claim_sig``); its notices were
    handed over (no unseen results); the user did not type here in the last 5 min."""
    sig = settled_sig(row.get("batch") or [], cards)
    if not sig or sig == row.get("done_sig") or sig == row.get("claim_sig"):
        return None
    if unseen > 0 or now - (last_user_at or 0) < USER_QUIET_S:
        return None
    return sig


def closeout_prompt(row: dict[str, Any], cards: dict[str, dict[str, Any]]) -> str:
    lines = [f"{TAG} Every kanban card this chat handed work to has settled. This turn is the close-out:"]
    for tid in sorted(row.get("batch") or []):
        c = cards.get(tid)
        if c is None:
            lines.append(f"- {tid}: no longer on any board")
            continue
        state = c.get("status") or "?"
        if state == "blocked":
            state = f"blocked ({c.get('block_kind') or 'needs a human'})"
        handoff = " ".join(str(c.get("summary") or "").split())[:700] or "(no handoff)"
        lines.append(f"- {tid} [{state}] {c.get('title') or ''}\n  handoff: {handoff}")
    lines.append(
        "Write the closing answer for the user now: what each card delivered (from its handoff; check any claim you "
        "can cheaply verify), what is still open, and for a card blocked on the user the exact one-line ask. Don't "
        "start new work unless a handoff says something promised is still undone; then create a card for it.")
    return "\n".join(lines)


def notice_text(row: dict[str, Any], cards: dict[str, dict[str, Any]]) -> str:
    n = len(row.get("batch") or [])
    blocked = sum(1 for i in row.get("batch") or [] if needs_human(cards.get(i)))
    tail = f"; {blocked} waiting on you" if blocked else ""
    return f"Close-out: the {n} card{'s' if n != 1 else ''} this chat waited on settled{tail}"


# ---- state.db: when did the user last type ---------------------------------------------------------------------
def last_user_at(state_dbs: Iterable[Path], ids: Iterable[str]) -> float:
    """Newest real user message (not a kanban/close-out notice or synthetic row) over the given session ids."""
    ids = [i for i in dict.fromkeys(ids) if i]
    best = 0.0
    for path in state_dbs:
        try:
            conn = ro_connect(Path(path))
        except sqlite3.Error:
            continue
        try:
            dk = "AND (display_kind IS NULL OR display_kind = '')" if "display_kind" in _cols(conn, "messages") else ""
            ph = ",".join("?" for _ in ids)
            for r in conn.execute(f"SELECT content, timestamp FROM messages WHERE role='user' AND session_id IN ({ph}) "
                                  f"{dk} ORDER BY timestamp DESC LIMIT 20", ids):
                if NOTICE_RE.match(str(r["content"] or "")):
                    continue
                best = max(best, float(r["timestamp"] or 0))
                break
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return best


# ---- turn-end promise lint -------------------------------------------------------------------------------------
_PROMISE = re.compile(
    r"\b(?:I'?ll|I will|I’ll|we'?ll|will)\s+(?:report back|let you know|ping you|update you|follow up|get back to you|"
    r"check back|circle back|tell you (?:when|once|if)|post (?:an? |the )?update|report (?:when|once|the result))"
    r"|\b(?:when|once) (?:it|they|that|this|the (?:card|cards|job|build|run|worker|task)s?) "
    r"(?:finish(?:es)?|complete[sd]?|land[s]?|(?:is|are) done)\b"
    r"|\brunning (?:now|in the background)\b",
    re.IGNORECASE)
_QUOTE_STRIP = re.compile(r"```.*?```|`[^`]*`|^>.*$", re.DOTALL | re.MULTILINE)


def find_promise(text: str) -> Optional[str]:
    """The first future-reporting promise in a reply (code and quotes ignored), or None."""
    m = _PROMISE.search(_QUOTE_STRIP.sub(" ", text or ""))
    return m.group(0) if m else None


LINT_NUDGE = (
    "{tag} Your reply promises to report later ({promise!r}), but nothing tracked will wake this chat: it has no open "
    "kanban card, background process or cron job. A promise needs a tracked thing behind it. Either start it now "
    "(kanban_create a card that does the work, or terminal(background=true, notify=true) for a long command), or "
    "rewrite the reply without the promise and give the result now.")
LINT_APPEND = ("⚠ Nothing tracked will report back on this (no open card, background process or cron job is linked "
               "to this chat); ask again to check on it.")
