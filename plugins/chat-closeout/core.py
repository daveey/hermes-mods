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
MAX_WAKES_PER_HOUR = 2        # hard cap per chat, whatever the cards do
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


class ReadPool:
    """``ro_connect`` once per path for one pass, closed by ``close()``. The sidebar's waiting pass proves ~50
    finished cards against every state.db; a fresh connection per proof was most of its cost."""

    def __init__(self) -> None:
        self._conns: dict[str, sqlite3.Connection] = {}

    def get(self, path: Path) -> sqlite3.Connection:
        key = str(path)
        if key not in self._conns:
            self._conns[key] = ro_connect(Path(path))
        return self._conns[key]

    def drop(self, path: Path) -> None:
        conn = self._conns.pop(str(path), None)
        if conn is not None:
            conn.close()

    def close(self) -> None:
        for key in list(self._conns):
            self.drop(Path(key))


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


# ---- cards -----------------------------------------------------------------------------------------------------
def card_key(card: dict[str, Any]) -> str:
    """Retain core's owner-qualified key; local boards live in this ledger's scope."""
    if card.get('remote') or card.get('gateway'):
        if all(card.get(k) for k in ('gateway', 'profile', 'board', 'id')):
            return '/'.join(str(card[k]) for k in ('gateway', 'profile', 'board', 'id'))
        # Legacy remote keys remain remote, never fall through to a local bare ID.
        return str(card.get('key') or f"unknown/unknown/unknown/{card['id']}")
    return f"{card.get('board') or 'default'}/{card['id']}"


def read_cards(board_paths: Iterable[Path], ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """``{id: {id, status, block_kind, title, summary, completed_at, board}}`` for the ids found on any board."""
    want = sorted({str(i) for i in ids if i})
    found: dict[str, dict[str, Any]] = {}
    if not want:
        return found
    ambiguous: set[str] = set()
    for path in dict.fromkeys(board_paths):
        board = Path(path).parent.name if Path(path).parent.parent.name == 'boards' else 'default'
        left = sorted({i.split('/')[-1] for i in want
                       if '/' not in i or (i.count('/') == 1 and i.split('/')[0] == board)})
        if not left:
            continue
        try:
            conn = ro_connect(Path(path))
        except sqlite3.Error:
            ambiguous.update(i for i in want if '/' not in i)
            continue
        try:
            cols = _cols(conn, "tasks")
            bk = "t.block_kind" if "block_kind" in cols else "NULL"
            comp = "t.completed_at" if "completed_at" in cols else "NULL"
            res = "t.result" if "result" in cols else "NULL"
            hr = "t.human_request" if "human_request" in cols else "NULL"
            has_runs = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_runs'").fetchone()
            summary = (f"COALESCE((SELECT r.summary FROM task_runs r WHERE r.task_id = t.id AND r.summary IS NOT NULL "
                       f"AND r.summary != '' ORDER BY r.id DESC LIMIT 1), {res})") if has_runs else res
            ph = ",".join("?" for _ in left)
            for r in conn.execute(f"SELECT t.id, t.status, t.title, {bk} AS block_kind, {comp} AS completed_at, "
                                  f"{summary} AS summary, {hr} AS human_request FROM tasks t WHERE t.id IN ({ph})", left):
                card = {**dict(r), 'board': board}
                for key in (r['id'], card_key(card)):
                    if key in want:
                        if key in found:
                            ambiguous.add(key)
                        found[key] = card
        except sqlite3.Error as exc:
            ambiguous.update(i for i in want if '/' not in i)
            logger.debug("chat-closeout: card read failed on %s: %s", path, exc)
        finally:
            conn.close()
    return {key: card for key, card in found.items() if key not in ambiguous}


def is_settled(card: Optional[dict[str, Any]]) -> bool:
    """The chat no longer waits on this card's WORK: done/archived, in review, or blocked on a human. A card that
    missing from a board read is unknown, never proof of completion."""
    if card is None:
        return False
    status = card.get("status")
    if card.get('remote'):
        if card.get('stale') is not False:
            return False
        if status in DONE_STATUSES:
            return card.get('unfinished') is False and card.get('via') == 'origin' and bool(card.get('binding_id'))
        # Remote review is unfinished agent/verification work, not a human ask.
        return status in ('blocked', 'triage') and card.get('block_kind') in ('needs_input', 'capability')
    if status in DONE_STATUSES or status in HUMAN_STATUSES:
        return True
    if status in ("blocked", "triage"):
        kind = card.get("block_kind")
        return kind in HUMAN_BLOCK_KINDS if status == "blocked" else kind in ("needs_input", "capability")
    return False


def needs_human(card: Optional[dict[str, Any]]) -> bool:
    return bool(card) and is_settled(card) and card.get("status") not in DONE_STATUSES


# ---- ask relay: a card that needs the user, told to the chat while other batch cards still work -----------------
ASK_FIELDS = ("action", "why_human", "interaction", "scope", "evidence")
ASK_INTERACTIONS = ("approval", "reply", "user_terminal", "physical", "external")
NONHUMAN_BLOCK_KINDS = ("dependency", "transient", "time")


def ask_of(card: Optional[dict[str, Any]]) -> Optional[dict[str, str]]:
    """The card's validated human ask (core ``kanban_human_request`` contract: all five fields, a known interaction,
    not a dependency/transient/time wait) while it is parked, else None. ``human_request`` may be the stored JSON
    text (local boards) or the decoded dict (remote projection). A stale remote row is never an ask."""
    if not card or card.get("status") not in ("blocked", "triage"):
        return None
    if card.get("remote") and card.get("stale") is not False:
        return None
    if card.get("block_kind") in NONHUMAN_BLOCK_KINDS:
        return None
    value = card.get("human_request")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict) or any(not isinstance(value.get(f), str) or not value[f].strip()
                                          for f in ASK_FIELDS):
        return None
    if value["interaction"] not in ASK_INTERACTIONS:
        return None
    return {f: " ".join(value[f].split()) for f in ASK_FIELDS}


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def ask_premise(tid: str, card: Optional[dict[str, Any]]) -> Optional[str]:
    """``<card key>@<hash>`` naming WHY the card waits on the user: its status, block kind and ask. Re-blocked for
    another reason = another premise; the same block seen again = the same one."""
    ask = ask_of(card)
    if ask is None:
        return None
    basis = json.dumps([card.get("status"), card.get("block_kind") or "", _norm(ask["action"]),
                        _norm(ask["why_human"]), _norm(ask["scope"])])
    return f"{tid}@{hashlib.sha1(basis.encode()).hexdigest()[:12]}"


def _premise_card(premise: str) -> str:
    return premise.rsplit("@", 1)[0]


def ask_candidates(row: dict[str, Any], cards: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
    """``[(card key, premise)]`` for batch cards that wait on the user with an ask this chat was never told about.
    A card covered by a close-out that recorded no premise (rows from before the relay) is left alone, so a deploy
    never relays old asks; a covered card whose recorded premise changed (re-blocked for a new reason) is new."""
    relayed = set(row.get("relayed") or [])
    told = {_premise_card(p) for p in relayed}
    covered = set(row.get("covered") or [])
    out = []
    for tid in sorted(set(row.get("batch") or [])):
        premise = ask_premise(tid, cards.get(tid))
        if premise and premise not in relayed and (tid not in covered or tid in told):
            out.append((tid, premise))
    return out


def ask_relay_key(row: dict[str, Any], cards: dict[str, dict[str, Any]]) -> Optional[str]:
    """Key of the asks to relay now, or None. A pending close-out takes precedence: it reports every settled card,
    asks included, so the relay only covers the case it can't: the batch is not settled because other cards work
    (usually a card that waits on the blocked one)."""
    if pending_key(row, cards):
        return None
    cands = ask_candidates(row, cards)
    if not cands:
        return None
    return "ask:" + hashlib.sha1(";".join(p for _, p in cands).encode()).hexdigest()[:16]


def ask_premises(ids: Iterable[str], cards: dict[str, dict[str, Any]]) -> set[str]:
    return {p for p in (ask_premise(i, cards.get(i)) for i in ids) if p}


def settled_sig(batch: Iterable[str], cards: dict[str, dict[str, Any]]) -> Optional[str]:
    """Signature of the batch's settled state, or None while any card is still working."""
    ids = sorted(set(batch))
    if not ids or not all(is_settled(cards.get(i)) for i in ids):
        return None
    raw = ";".join(f"{i}={(cards.get(i) or {}).get('status', 'gone')}" for i in ids)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def uncovered(row: dict[str, Any], cards: dict[str, dict[str, Any]]) -> list[str]:
    """Settled batch cards no close-out has reported yet, when the whole batch is settled; else []."""
    batch = row.get("batch") or []
    if not settled_sig(batch, cards):
        return []
    covered = set(row.get("covered") or [])
    return sorted(i for i in set(batch) if i not in covered)


def closeout_key(row: dict[str, Any], cards: dict[str, dict[str, Any]]) -> Optional[str]:
    """Wake/close-out key: the id set of the settled cards not yet covered by a close-out. Statuses are not part of
    it, so settled->settled churn (blocked->archived, done->archived) never makes a new key; only a card that was
    not reported yet does."""
    new = uncovered(row, cards)
    return hashlib.sha1(";".join(new).encode()).hexdigest()[:16] if new else None


def reconcile(row: dict[str, Any], cards: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Coverage upkeep: a covered card that went back to work (unblocked -> running) is uncovered, so its real
    result gets a close-out; a covered card that is done/archived leaves the batch. A relayed ask whose card went
    back to work or left the batch is forgotten, so a later block (even for the same reason) is a new ask.
    Returns changed fields."""
    batch, covered = set(row.get("batch") or []), set(row.get("covered") or [])
    relayed = set(row.get("relayed") or [])
    keep = {p for p in relayed if _premise_card(p) in batch
            and (cards.get(_premise_card(p)) is None or is_settled(cards[_premise_card(p)]))}
    reopened = {i for i in covered if cards.get(i) is not None and not is_settled(cards[i])}
    gone = {i for i in covered & batch if is_settled(cards.get(i)) and (cards.get(i) or {}).get("status") in DONE_STATUSES}
    out: dict[str, Any] = {}
    if reopened or gone:
        out["covered"] = (covered - reopened) - gone
    if reopened:  # the same id set may settle again: its old key must not suppress the new close-out
        out.update(done_sig=None, claim_sig=None)
    if gone:
        out["batch"] = batch - gone
        keep = {p for p in keep if _premise_card(p) not in gone}
    if keep != relayed:
        out["relayed"] = keep
    return out


def pending_key(row: dict[str, Any], cards: dict[str, dict[str, Any]]) -> Optional[str]:
    """THE close-out decision, shared by the sidebar, the pre_llm_call brief, the waker and the watchdog: the key of
    the settled-but-unreported cards, unless a close-out turn already ended for exactly that key."""
    key = closeout_key(row, cards)
    return key if key and key != row.get("done_sig") else None


def _notice_matches(text: str, tid: str) -> bool:
    """Legacy notices are local-board scoped. Never strip a remote owner to a bare id."""
    match = re.match(r"^\s*✔\s*\[([^\]]+)\]\s+@\S+\s+Kanban\s+(\S+)\s+(done|archived)(?:\s|$)", text)
    if not match:
        return False
    board, named, _ = match.groups()
    parts = str(tid).split('/')
    if len(parts) == 4:
        return named == tid and board == parts[2]
    if len(parts) == 2:
        return board == parts[0] and named == parts[1]
    return len(parts) == 1 and board == 'default' and named == tid


def reconcile_done(row: dict[str, Any], cards: dict[str, dict[str, Any]],
                   proof: Optional[Callable[[str, dict[str, Any]], bool]] = None) -> dict[str, Any]:
    """Settle a row whose ``done_sig`` equals its current key (so nothing would ever process it) by evidence:

    - (when the batch is still partly working, only the transcript proof applies, per card)
    - a done card finished at or before ``done_at``, or ``proof(id, card)`` shows a notice for it was delivered and
      answered after it finished: acknowledged, so it is covered (and leaves the batch);
    - a done card finished AFTER ``done_at`` with no such proof: the stamp belongs to an earlier state (the card
      was reopened), so ``done_sig`` is cleared and the final handoff gets its close-out;
    - anything else (no timestamps, remote or unknown state) is left exactly as it is.
    Returns changed fields (empty when nothing is proven)."""
    if row.get("closing") is not None:
        return {}  # a close-out turn is running: its own turn end settles the row
    key = closeout_key(row, cards)
    stamped = bool(key) and key == row.get("done_sig")
    done_at = float(row.get("done_at") or 0)
    covered = set(row.get("covered") or [])
    acked: set[str] = set()
    stale = False
    for tid in sorted(set(row.get("batch") or [])):
        card = cards.get(tid)
        if not card or not is_settled(card) or card.get("status") not in DONE_STATUSES or tid in covered:
            continue
        at = card.get("completed_at")
        local = at is not None and not card.get("remote")
        if stamped and local and float(at) <= done_at:
            acked.add(tid)
        elif proof is not None and proof(tid, card):
            acked.add(tid)  # evidence beats the batch state: the final notice was shown and answered
        elif stamped and local and float(at) > done_at:
            stale = True
    out: dict[str, Any] = {}
    if acked:
        covered = set(row.get("covered") or []) | acked
        out["covered"] = covered - acked  # acked cards are done: they leave both sets
        out["batch"] = set(row.get("batch") or []) - acked
    if stale:
        out.update(done_sig=None, claim_sig=None)
    return out


def ack_proven(state_dbs: Iterable[Path], ids: Iterable[str], tid: str, since: float,
               pool: Optional[ReadPool] = None) -> bool:
    """An owner-exact final notice followed by a successful final assistant reply.

    Missing legacy completion metadata is ambiguous, not acknowledgement. Inactive,
    commentary, failed, interrupted and tool-calling messages cannot close work.
    ``pool`` (one pass's connections) is reused and left open; without it each db is opened and closed here.
    """
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids or not tid:
        return False
    for path in state_dbs:
        try:
            conn = pool.get(Path(path)) if pool is not None else ro_connect(Path(path))
        except sqlite3.Error:
            continue
        failed = False
        try:
            required = {'display_kind', 'finish_reason', 'tool_calls', 'active'}
            if not required.issubset(_cols(conn, 'messages')):
                continue
            ph = ",".join("?" for _ in ids)
            rows = conn.execute(f"SELECT role, content, timestamp, display_kind, finish_reason, tool_calls "
                                f"FROM messages WHERE session_id IN ({ph}) AND active=1 "
                                f"AND timestamp >= ? ORDER BY timestamp", (*ids, float(since))).fetchall()
            notice_at = None
            for r in rows:
                text = str(r["content"] or "")
                if r['role'] == 'user':
                    # A different user turn breaks the causal notice/reply pair.
                    notice_at = float(r['timestamp']) if _notice_matches(text, tid) else None
                elif (notice_at is not None and r['role'] == 'assistant' and text.strip()
                      and float(r['timestamp']) > notice_at
                      and r['display_kind'] in (None, '', 'final')
                      and r['finish_reason'] in ('stop', 'end_turn')
                      and r['tool_calls'] in (None, '', '[]', 'null')):
                    return True
        except sqlite3.Error:
            failed = True
        finally:
            if pool is None:
                conn.close()
            elif failed:
                pool.drop(Path(path))
    return False


# ---- the ledger ------------------------------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS waiting (
  session_id TEXT PRIMARY KEY,
  batch TEXT NOT NULL DEFAULT '[]',      -- every card the chat handed work to since its last close-out
  open TEXT NOT NULL DEFAULT '[]',       -- waiting_on at the last turn end
  stamped_at REAL,
  settled_sig TEXT, settled_at REAL,     -- first time the batch was seen fully settled (this sig)
  done_sig TEXT, done_at REAL,           -- a turn ended after the batch settled: the close-out happened
  claim_sig TEXT, claim_at REAL, claim_pid INTEGER, wake_result TEXT,
  covered TEXT NOT NULL DEFAULT '[]',    -- batch cards a close-out already reported (never wake for them again)
  closing TEXT,                          -- JSON batch ids while a close-out turn runs (NULL otherwise)
  wakes TEXT NOT NULL DEFAULT '[]',      -- wall times of recent wakes (per-chat hourly cap)
  wake_count INTEGER NOT NULL DEFAULT 0,
  relayed TEXT NOT NULL DEFAULT '[]'     -- ask premises (card@hash) the chat was told about (ask relay)
);
CREATE TABLE IF NOT EXISTS unseen_seen (session_id TEXT PRIMARY KEY, since REAL NOT NULL);
CREATE TABLE IF NOT EXISTS flags (key TEXT PRIMARY KEY, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


_MIGRATE = (("covered", "TEXT NOT NULL DEFAULT '[]'"), ("closing", "TEXT"),
            ("wakes", "TEXT NOT NULL DEFAULT '[]'"), ("wake_count", "INTEGER NOT NULL DEFAULT 0"),
            ("relayed", "TEXT NOT NULL DEFAULT '[]'"))
_JSON_SETS = ("batch", "open", "covered", "relayed")


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
        have = _cols(c, "waiting")
        for col, ddl in _MIGRATE:
            if col not in have:
                c.execute(f"ALTER TABLE waiting ADD COLUMN {col} {ddl}")
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
        enc = {k: (json.dumps(sorted(v)) if k in _JSON_SETS else
                   json.dumps(list(v)) if k in ("closing", "wakes") and v is not None else v)
               for k, v in fields.items()}
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


def pending_closeouts(ledger: "Ledger", read: Callable[[list[str]], dict],
                      proof: Optional[Callable[[str, str, dict[str, Any]], bool]] = None) -> dict[str, list[str]]:
    """``{session_id: batch ids}`` for chats whose whole batch has settled but whose close-out turn has not run for
    that state: the sidebar keeps them grey "waiting" until the closing answer lands. Read-only; it applies the
    same reconcile the waker persists, so the sidebar can never wait on a row the waker would skip."""
    out: dict[str, list[str]] = {}
    for row in ledger.pending():
        cards = read(row["batch"])
        sid = row["session_id"]
        fix = reconcile_done(row, cards, (lambda t, c, sid=sid: proof(sid, t, c)) if proof else None)
        row = {**row, **fix}
        if row["batch"] and pending_key(row, cards):
            out[sid] = sorted(row["batch"])
    return out


def _row(r) -> Optional[dict[str, Any]]:
    if r is None:
        return None
    d = dict(r)
    for k in ("batch", "open", "covered", "wakes", "relayed"):
        try:
            d[k] = list(json.loads(d.get(k) or "[]"))
        except ValueError:
            d[k] = []
    try:
        d["closing"] = json.loads(d["closing"]) if d.get("closing") else None
    except ValueError:
        d["closing"] = None
    return d


# ---- turn end --------------------------------------------------------------------------------------------------
def record_turn_end(ledger: Ledger, sid: str, open_ids: Iterable[str], read: Callable[[list[str]], dict],
                    now: Optional[float] = None, observed_sig: Optional[str] = None) -> dict[str, Any]:
    """Stamp ``waiting_on`` for the chat at a turn end. Every card it handed work to joins the batch.

    A turn is a close-out when it was briefed on the settled batch (``observed_sig`` from ``pre_llm_call``) or was
    started by the waker (``closing`` in the ledger). Only then are the batch's settled cards marked covered,
    including cards the close-out turn itself created that are already settled (a needs_input card): those must
    not make the batch "newly settled" and wake the chat again. An ordinary turn during which a card finished
    covers nothing, so it can't silently acknowledge a handoff nobody saw."""
    now = time.time() if now is None else now
    row = ledger.get(sid) or {}
    open_now = set(open_ids)
    # Old-lineage cards (compression rotated the id) are still in the batch: keep them open until settled.
    batch = set(row.get("batch") or []) | open_now
    cards = read(sorted(batch)) if batch else {}
    open_now |= {i for i in batch if not is_settled(cards.get(i))}
    view = {**row, "batch": sorted(batch)}
    fix = reconcile(view, cards)
    view.update(fix)
    batch, covered = set(view["batch"]), set(view.get("covered") or [])
    key = closeout_key(view, cards)
    fields: dict[str, Any] = {"open": open_now, "stamped_at": now}
    fields.update({k: fix[k] for k in ("done_sig", "claim_sig", "relayed") if k in fix})
    if key and key != row.get("settled_sig"):
        fields.update(settled_sig=key, settled_at=now)  # the watchdog's "settled since" clock
    closing = bool(observed_sig) or row.get("closing") is not None
    if closing:
        # The waker covered the batch before the turn, so its key was stamped. Only trust that stamp while the whole
        # batch is still settled: a card reopened during the turn (reviewer -> running) makes the stamp stale, and
        # stamping it as done would hide the card's final handoff forever.
        done = key or (row.get("settled_sig") if settled_sig(batch, cards) else None)
        if done:
            fields.update(done_sig=done, done_at=now)
        covered |= {i for i in batch if is_settled(cards.get(i))}
        # the close-out reported these asks: the relay must not tell them again while later cards work
        fields["relayed"] = set(view.get("relayed") or []) | ask_premises(covered, cards)
        gone = {i for i in covered if is_settled(cards.get(i)) and (cards.get(i) or {}).get("status") in DONE_STATUSES}
        batch -= gone   # reported and finished; human-blocked cards stay (covered) until they move
        covered -= gone
        fields["relayed"] = {p for p in fields["relayed"] if _premise_card(p) in batch}
        fields["closing"] = None
    fields["batch"], fields["covered"] = batch, covered
    if not batch and not row:
        return {"open": [], "batch": []}
    ledger.write(sid, **fields)
    return {**fields, "open": sorted(open_now), "batch": sorted(batch), "closeout": closing,
            "sig": fields.get("done_sig") if closing else None}


# ---- close-out decision ----------------------------------------------------------------------------------------
def wakes_last_hour(row: dict[str, Any], now: float) -> int:
    return sum(1 for t in row.get("wakes") or [] if 0 <= now - float(t) < 3600)


def closeout_due(row: dict[str, Any], cards: dict[str, dict[str, Any]], *, unseen: int, last_user_at: float,
                 now: float) -> Optional[str]:
    """The close-out key when this chat needs a close-out turn now, else None. Rules: every batch card settled
    and at least one not reported by an earlier close-out; nobody claimed this key; its notices were handed over
    (no unseen results); the user did not type here in the last 5 min; under the per-chat hourly wake cap."""
    key = pending_key(row, cards)
    if not key or key == row.get("claim_sig"):
        return None
    if unseen > 0 or now - (last_user_at or 0) < USER_QUIET_S:
        return None
    if wakes_last_hour(row, now) >= MAX_WAKES_PER_HOUR:
        return None
    return key


def ask_relay_due(row: dict[str, Any], cards: dict[str, dict[str, Any]], *, unseen: int, last_user_at: float,
                  now: float) -> Optional[str]:
    """The ask-relay key when this chat should get its one ask turn now, else None. Same gates as a close-out: not
    claimed already, notices handed over, the user quiet for 5 min, under the per-chat hourly wake cap."""
    key = ask_relay_key(row, cards)
    if not key or key == row.get("claim_sig"):
        return None
    if unseen > 0 or now - (last_user_at or 0) < USER_QUIET_S:
        return None
    if wakes_last_hour(row, now) >= MAX_WAKES_PER_HOUR:
        return None
    return key


PLAIN_ASK_RULE = (
    "For a card waiting on the user, say in plain words: what is stuck (the work, by what it does), the one thing "
    "the user must do and where (the page, app or link; read the card with kanban_show if the ask is vague), and "
    "what happens after they do it: quote the card's ask line verbatim, including what unblocking and what "
    "archiving the card mean. Card ids are not the explanation: name the work. If an agent can do the step "
    "itself, do it instead of asking.")


def _ask_line(c: dict[str, Any]) -> str:
    ask = ask_of(c)
    if not ask:
        return ""
    # The ask travels verbatim (never truncated): it is the one line the user must act on, and it carries what
    # unblock and archive mean (core kanban_ask_lint requires both on agent-filed user blocks).
    return (f"\n  needs the user to: {ask['action']}\n  why only the user: {ask['why_human'][:200]}"
            f" ({ask['interaction']}; scope: {ask['scope'][:160]})")


def ask_relay_prompt(row: dict[str, Any], cards: dict[str, dict[str, Any]], ids: Iterable[str]) -> str:
    ids = sorted(set(ids))
    lines = [f"{TAG} Work this chat started is now waiting on the user, while other work in this chat waits behind "
             "it. This turn tells the user. Waiting on the user:"]
    for tid in ids:
        c = cards.get(tid) or {}
        lines.append(f"- {c.get('title') or tid} (card {tid}, {c.get('status')}/{c.get('block_kind') or '-'})"
                     f"{_ask_line(c)}")
    behind = [cards.get(t) or {} for t in sorted(set(row.get("batch") or [])) if t not in ids
              and not is_settled(cards.get(t))]
    if behind:
        lines.append("Still in progress or queued behind it: " + "; ".join(
            f"{c.get('title') or '(untitled)'} [{c.get('status') or '?'}]" for c in behind[:8]))
    lines.append("Write a short message for the user now. " + PLAIN_ASK_RULE + " Don't say that nothing needs them. "
                 "Keep the turn to this ask; the other cards report when they finish.")
    return "\n".join(lines)


def _ask_notice_lines(cards: dict[str, dict[str, Any]], ids: Iterable[str]) -> list[str]:
    """One "Needs you" line per card waiting on the user: its title and its ask, verbatim. A user-blocked card with no
    validated ask still gets a line that says so, so the notice never reads as "blocked" with nothing to do."""
    lines = []
    for tid in sorted(set(ids)):
        c = cards.get(tid) or {}
        title = " ".join(str(c.get("title") or tid).split())[:120]
        ask = ask_of(c)
        lines.append(f"Needs you: {title}: {ask['action']}" if ask
                     else f"Needs you: {title} (the card names no ask; this turn asks the agent to spell it out)")
    return lines


def ask_notice_text(cards: dict[str, dict[str, Any]], ids: Iterable[str]) -> str:
    return "\n".join(_ask_notice_lines(cards, ids))


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
        lines.append(f"- {tid} [{state}] {c.get('title') or ''}{_ask_line(c)}\n  handoff: {handoff}")
    lines.append(
        "Write the closing answer for the user now: what each card delivered (from its handoff; check any claim you "
        "can cheaply verify), and for a card blocked on the user the exact one-line ask. For each problem the "
        "handoffs leave open that an agent can fix, fix it now if it's small and reversible; otherwise create a "
        "linked card with acceptance criteria and name it. List as still open only items that are the user's own "
        "call, or that a card you checked this turn is actively running. " + PLAIN_ASK_RULE)
    return "\n".join(lines)


def notice_text(row: dict[str, Any], cards: dict[str, dict[str, Any]]) -> str:
    n = len(row.get("batch") or [])
    waiting = [i for i in row.get("batch") or [] if needs_human(cards.get(i))
               and (cards.get(i) or {}).get("status") in ("blocked", "triage")
               and (cards.get(i) or {}).get("block_kind") not in NONHUMAN_BLOCK_KINDS]
    head = f"Close-out: the {n} card{'s' if n != 1 else ''} this chat waited on settled"
    if not waiting:
        return head
    return "\n".join([f"{head}; {len(waiting)} waiting on you"] + _ask_notice_lines(cards, waiting))


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
