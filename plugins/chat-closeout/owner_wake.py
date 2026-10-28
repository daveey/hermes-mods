"""Owner wake: a card that parks for an agent-owned reason wakes the chat that owns it. [daveey]

On 10-26 a remote card's ``continuation_exhausted`` notice landed in its owning chat and started no turn; the
chat's tracker had narrowed its wake, and the sidebar said "waiting on N cards, 0 blocked" for ~5 h while nothing
was producing what those cards waited for. Archived and reaped chats still owned open cards, and nothing woke them.

Each tick (the chat-closeout backend thread) reads every parked card a chat created or shepherds, local and on other
gateways (core ``kanban_session_activity.parked_waits``: what it waits on and whether only an agent can end it, from
``kanban_wait_info``), and for each one that parked for an agent-owned reason:

- a live owning chat gets ONE supervise turn (in-process ``server._wake_session_turn``, the close-out waker's path)
  naming the cards, what each waits on and since when;
- an archived, reaped or missing chat routes the card to the board supervisor instead (a ready card on this
  gateway's default board, assigned to ``kanban.orchestrator_profile`` or ``default``), so the card never orphans.

Agent-owned: a transient/capability/assignment block, triage (gave_up / block loop), continuation_exhausted, or a
park with no named producer. A legacy park whose producer can't be told (an opaque probe, no ``awaiting``) only
counts once it has stalled (2 h).

Rate limits: once per card per premise (``kanban_wait_info.premise``: status, kind and reason text, never
timestamps, so a re-park on the same reason never wakes again), and at most ``MAX_WAKES_PER_CHAT_HOUR`` wakes per
chat per hour. A busy/refused/error wake releases its claim for a later tick. The claim is an INSERT in this
module's own SQLite file, so two backends never both wake for one premise.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


def _ro_connect(path, timeout: float = 2.0) -> sqlite3.Connection:
    """Read-only open. ``mode=ro`` fails on a WAL db whose -wal/-shm writers deleted on last close, so fall back to
    ``mode=rw`` + ``query_only`` (reads the same data, never writes rows; ``mode=rw`` never creates a missing db)."""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)
        c.execute("select 1 from sqlite_master limit 1")
        return c
    except sqlite3.OperationalError:
        c = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=timeout)
        c.execute("PRAGMA query_only=ON")
        return c


logger = logging.getLogger("chat_closeout.owner_wake")

MAX_WAKES_PER_CHAT_HOUR = 3
# Orphaned chats (archived/reaped/gone) route to the board supervisor: one card per chat per pass, at most this many
# per hour across the gateway, so the first pass over an old backlog can't flood the board.
MAX_SUPERVISOR_ROUTES_HOUR = 6
# Gateway-wide caps on chat wakes, so a backlog (first deploy, a board-wide outage) can't wake every chat at once.
MAX_CHAT_WAKES_PER_TICK = 3
MAX_CHAT_WAKES_HOUR = 10
# A wait older than this when first seen is history, not news: it is recorded (never wakes) instead of waking. Short,
# so the first pass after deploy does not turn days of backlog into hours of wakes (the board still shows them amber).
MAX_AGE_S = 48 * 3600
# A claim whose wake never finished (the backend died mid-wake) expires, so the premise is not lost forever.
CLAIM_TTL_S = 600.0
USER_QUIET_S = 120.0  # never wake over a user who typed in the last 2 minutes; the next tick retries
SKIP_SOURCES = {"cron", "kanban", "webhook", "subagent"}
REAPED_END_REASONS = {"startup_orphan_reap", "orphan_reap", "reaped"}
# chat_info result when a state store could not be read and no store had the chat: unknown, never "gone".
UNKNOWN = {"unknown": True}
TAG = "[owner-wake]"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fired (
  card_key TEXT NOT NULL, premise TEXT NOT NULL, session_id TEXT NOT NULL, claimed_at REAL NOT NULL,
  result TEXT, route TEXT, supervisor_task TEXT, PRIMARY KEY (card_key, premise));
CREATE TABLE IF NOT EXISTS wakes (session_id TEXT NOT NULL, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS wakes_by_session ON wakes (session_id, at);
"""


class WakeLog:
    """Which (card, premise) pairs already woke someone, and when each chat was last woken."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _conn(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(_SCHEMA)
        return c

    def _run(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            c = self._conn()
            try:
                cur = c.execute(sql, args)
                return cur.fetchall() if sql.lstrip().upper().startswith("SELECT") else [(cur.rowcount,)]
            finally:
                c.close()

    def fired(self, card_key: str, premise: str, now: Optional[float] = None) -> bool:
        if now is not None:
            return bool(self._run("SELECT 1 FROM fired WHERE card_key=? AND premise=? AND (result IS NOT NULL "
                                  "OR claimed_at >= ?)", (card_key, premise, now - CLAIM_TTL_S)))
        return bool(self._run("SELECT 1 FROM fired WHERE card_key=? AND premise=?", (card_key, premise)))

    def claim(self, card_key: str, premise: str, session_id: str, now: float) -> bool:
        """Atomic: True for exactly one caller per (card, premise). An unfinished claim older than CLAIM_TTL_S is
        dropped first (its backend died between claim and result)."""
        self._run("DELETE FROM fired WHERE card_key=? AND premise=? AND result IS NULL AND claimed_at < ?",
                  (card_key, premise, now - CLAIM_TTL_S))
        return self._run("INSERT OR IGNORE INTO fired (card_key, premise, session_id, claimed_at) VALUES (?,?,?,?)",
                         (card_key, premise, session_id, now))[0][0] == 1

    def release(self, card_key: str, premise: str) -> None:
        self._run("DELETE FROM fired WHERE card_key=? AND premise=? AND result IS NULL", (card_key, premise))

    def finish(self, card_key: str, premise: str, result: str, route: str, supervisor_task: str = "") -> None:
        self._run("UPDATE fired SET result=?, route=?, supervisor_task=? WHERE card_key=? AND premise=?",
                  (result, route, supervisor_task or None, card_key, premise))

    def note_wake(self, session_id: str, now: float) -> None:
        self._run("INSERT INTO wakes (session_id, at) VALUES (?, ?)", (session_id, now))
        self._run("DELETE FROM wakes WHERE at < ?", (now - 86400,))

    def wakes_last_hour(self, session_id: str, now: float) -> int:
        return int(self._run("SELECT COUNT(*) FROM wakes WHERE session_id=? AND at > ?",
                             (session_id, now - 3600))[0][0])

    def chat_wakes_last_hour(self, now: float) -> int:
        return self.wakes_last_hour("__chats__", now)

    def note_route(self, now: float) -> None:
        self.note_wake("__supervisor__", now)

    def supervisor_routes_last_hour(self, now: float) -> int:
        return self.wakes_last_hour("__supervisor__", now)

    def rows(self) -> list[tuple]:
        return self._run("SELECT card_key, premise, session_id, result, route, supervisor_task FROM fired")


# ---- what counts -----------------------------------------------------------------------------------------------
def due(card: dict[str, Any]) -> bool:
    """A parked card whose wait only an agent can end (certain, or a legacy park that has stalled)."""
    w = card.get("wait") or {}
    return bool(w.get("agent_owned") and w.get("premise")
                and (w.get("agent_owned_certain", True) or w.get("wait_state") == "stalled"))


def _since(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(float(ts)))
    except (TypeError, ValueError):
        return "an unknown time"


def card_line(card: dict[str, Any]) -> str:
    w = card.get("wait") or {}
    where = f" on {card['gateway']}" if card.get("gateway") else ""
    title = f" \"{card['title']}\"" if card.get("title") else ""
    state = "STALLED, " if w.get("wait_state") == "stalled" else ""
    deps = card.get("dependents") or []
    known = [d for d in deps if not d.startswith("(")]
    needed = (f"; still needed by {', '.join(known)}: do NOT close or archive it while they wait" if known else "")
    needed += "".join(f"; {d}" for d in deps if d.startswith("("))
    return (f"- {card.get('key')}{where}{title}: {state}waiting on {w.get('waiting_on') or 'nothing named'} since "
            f"{_since(w.get('waiting_since'))}{needed}")


# On 10-27 a supervisor wake called a card "safe to close" because its owning chat was archived, while a card on
# another gateway was parked waiting on it; closing it would have left each naming the other as owner.
REVERSE_DEPS_RULE = (
    "Before you close, complete or archive any of these cards, check its reverse dependencies on every gateway: open "
    "cards whose parents, awaiting or park/block reason name it, including its replica or tracker on the other "
    "gateway (cross_task / `hermes kanban show` there). An archived or reaped owning chat never makes a card safe "
    "to close; a card another card still waits on stays open (hand it to a live owner instead).")


def wake_prompt(cards: list[dict[str, Any]]) -> str:
    lines = "\n".join(card_line(c) for c in cards)
    return (
        f"{TAG} Card(s) this chat owns parked for a reason only an agent can clear; nothing else will produce what "
        f"they wait for:\n{lines}\n\n"
        "Supervise them now. For each: remove the cause with capabilities this gateway holds (credentials, SSO, "
        "hosts, repos) when it is within the approved plan; otherwise create the owner card on the gateway that "
        "holds the capability and point the wait at it, or reassign. A tracker here may clear a capability gap its "
        "origin gateway holds. Do not re-park on the same premise, and do not turn this into a question for David "
        "unless it truly needs his decision (then make it a visible blocked ask). "
        f"{REVERSE_DEPS_RULE} Report in one short paragraph what you changed.")


def notice_text(cards: list[dict[str, Any]]) -> str:
    first = cards[0]
    more = f" (+{len(cards) - 1} more)" if len(cards) > 1 else ""
    return (f"Owner wake: {first.get('key')} waits on {(first.get('wait') or {}).get('waiting_on') or 'nothing named'}"
            f"{more}; supervising")


def supervisor_body(cards: list[dict[str, Any]], session_id: str, why: str) -> str:
    lines = "\n".join(card_line(c) for c in cards)
    return (
        f"{TAG} These card(s) parked for an agent-owned reason and their owning chat {session_id} is {why}, so "
        f"nobody would wake for them:\n{lines}\n\n"
        "Supervise each: read the card (cross-gateway: `hermes kanban wait-check task <id> --gateway <gw>`, "
        "cross_task), then clear the cause with this gateway's capabilities within the card's approved plan, create "
        "the owner card where the capability lives, or reassign it. Do not re-park on the same premise and do not ask "
        f"David unless a real human decision is needed (then a visible blocked ask). {REVERSE_DEPS_RULE} Complete "
        "this card with what you changed.")


# ---- the tick --------------------------------------------------------------------------------------------------
def tick(*, cards: Iterable[dict[str, Any]], wake: Optional[Callable[..., str]], log: WakeLog,
         chat_info: Callable[[str], Optional[dict[str, Any]]],
         supervise: Callable[[list[dict[str, Any]], str, str], str],
         last_user: Callable[[str], float], now: Optional[float] = None,
         canonical: Callable[[str], str] = lambda sid: sid,
         dependents: Optional[Callable[[dict[str, Any]], list[str]]] = None) -> list[dict[str, Any]]:
    """One owner-wake pass. ``cards``: ``parked_waits()`` rows. ``chat_info(sid)``: ``{archived, end_reason, source}``
    or None when no store has the chat. ``supervise(cards, sid, why)`` creates one board-supervisor card for an
    orphaned chat's due cards and returns its id. ``canonical(sid)`` maps a session id to the chat it belongs to now
    (its compression tip), so one chat is one group and one rate-limit counter. ``dependents(card)`` names the open
    cards still waiting on a card (``dependents_from``); the wake text says not to close those. Returns what it did."""
    now = time.time() if now is None else now

    def annotate(claimed: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if dependents is None:
            return claimed
        out = []
        for c in claimed:
            try:
                deps = list(dependents(c))
            except Exception:
                logger.debug("owner-wake: dependents(%s) failed", c.get("key"), exc_info=True)
                deps = ["(unreadable here: check before closing)"]
            out.append({**c, "dependents": deps} if deps else c)
        return out

    by_chat: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        if not (due(card) and card.get("session")) or log.fired(str(card["key"]), card["wait"]["premise"], now):
            continue
        since = (card.get("wait") or {}).get("waiting_since")
        if since and now - float(since) > MAX_AGE_S:
            # History, not news: record it so it never wakes (the board still shows it amber).
            if log.claim(str(card["key"]), card["wait"]["premise"], str(card["session"]), now):
                log.finish(str(card["key"]), card["wait"]["premise"], "too_old", "none")
            continue
        try:
            sid = canonical(str(card["session"])) or str(card["session"])
        except Exception:
            sid = str(card["session"])
        by_chat.setdefault(sid, []).append(card)
    done: list[dict[str, Any]] = []
    tried_this_tick = 0
    for sid, pending in sorted(by_chat.items()):
        try:
            info = chat_info(sid)
        except Exception:
            logger.debug("owner-wake: chat_info(%s) failed", sid, exc_info=True)
            info = UNKNOWN
        if info is not None and info.get("unknown"):
            continue  # a store was unreadable: unknown is not gone; the next tick reads again
        if info is not None and str(info.get("source") or "").lower() in SKIP_SOURCES:
            continue  # a worker/cron/subagent session: its dispatcher or parent owns the card
        orphaned = None
        if info is None:
            orphaned = "gone"
        elif info.get("archived"):
            orphaned = "archived"
        elif str(info.get("end_reason") or "") in REAPED_END_REASONS:
            orphaned = f"reaped ({info.get('end_reason')})"
        if orphaned is None and wake is None:
            continue  # this process can't run turns (not a Desktop/TUI backend); the owning backend will
        if orphaned is None:
            if now - float(last_user(sid) or 0) < USER_QUIET_S:
                continue
            if tried_this_tick >= MAX_CHAT_WAKES_PER_TICK or log.chat_wakes_last_hour(now) >= MAX_CHAT_WAKES_HOUR:
                continue  # gateway-wide cap: the rest wake on later ticks
            if log.wakes_last_hour(sid, now) >= MAX_WAKES_PER_CHAT_HOUR:
                logger.warning("owner-wake: %s hit %d wakes/hour; %d card(s) wait for the next window", sid,
                               MAX_WAKES_PER_CHAT_HOUR, len(pending))
                continue
            claimed = [c for c in pending if log.claim(str(c["key"]), c["wait"]["premise"], sid, now)]
            if not claimed:
                continue
            assert wake is not None
            tried_this_tick += 1  # failed attempts count too: a refusing chat can't burn the tick
            try:
                result = wake(sid, wake_prompt(annotate(claimed)), display_kind="kanban_notice",
                              display_metadata={"display_text": notice_text(claimed), "owner_wake": True,
                                                "task_ids": [c["key"] for c in claimed]},
                              label="owner-wake")
            except Exception:
                logger.warning("owner-wake: wake of %s failed", sid, exc_info=True)
                result = "error"
            if result == "started":
                _bookkeep(lambda: (log.note_wake(sid, now), log.note_wake("__chats__", now)), sid)
                for c in claimed:
                    _bookkeep(lambda c=c: log.finish(str(c["key"]), c["wait"]["premise"], "started", "chat"), sid)
                logger.info("owner-wake: woke %s for %s", sid, [c["key"] for c in claimed])
                done.append({"session_id": sid, "route": "chat", "result": result, "cards": [c["key"] for c in claimed]})
                continue
            if result != "missing":
                if result == "busy":  # the chat is mid-turn: retry next tick
                    for c in claimed:
                        log.release(str(c["key"]), c["wait"]["premise"])
                # refused/error: keep the unfinished claim; it expires after CLAIM_TTL_S, which is the retry backoff
                logger.info("owner-wake: wake of %s deferred (%s)", sid, result)
                done.append({"session_id": sid, "route": "chat", "result": result, "cards": [c["key"] for c in claimed]})
                continue
            for c in claimed:  # no store has the chat after all: the supervisor takes it
                log.release(str(c["key"]), c["wait"]["premise"])
            orphaned = "gone"
        if log.supervisor_routes_last_hour(now) >= MAX_SUPERVISOR_ROUTES_HOUR:
            logger.warning("owner-wake: %d supervisor routes this hour; %s's %d card(s) wait for the next window",
                           MAX_SUPERVISOR_ROUTES_HOUR, sid, len(pending))
            continue
        claimed = [c for c in pending if log.claim(str(c["key"]), c["wait"]["premise"], sid, now)]
        if not claimed:
            continue
        try:
            # One supervisor card per orphaned chat per pass, listing every card it newly holds.
            task_id = supervise(annotate(claimed), sid, orphaned)
        except Exception:
            logger.warning("owner-wake: supervisor route failed for %s", sid, exc_info=True)
            for c in claimed:
                log.release(str(c["key"]), c["wait"]["premise"])
            continue
        _bookkeep(lambda: log.note_route(now), sid)
        for c in claimed:
            _bookkeep(lambda c=c: log.finish(str(c["key"]), c["wait"]["premise"], "routed", "supervisor", task_id),
                      sid)
        logger.info("owner-wake: chat %s is %s; routed %s to supervisor card %s", sid, orphaned,
                    [c["key"] for c in claimed], task_id)
        done.append({"session_id": sid, "route": "supervisor", "result": "routed",
                     "cards": [c["key"] for c in claimed], "supervisor_task": task_id, "why": orphaned})
    return done


def _bookkeep(fn: Callable[[], Any], sid: str) -> None:
    """Record a wake that already happened. Retried (SQLite busy), then logged: never raised into the tick, where an
    unfinished claim would expire and wake the chat a second time."""
    for attempt in range(3):
        try:
            fn()
            return
        except Exception:
            if attempt == 2:
                logger.warning("owner-wake: bookkeeping for %s failed", sid, exc_info=True)
            else:
                time.sleep(0.2)


# ---- real collaborators ----------------------------------------------------------------------------------------
_CLOSED = ("done", "archived")
# Fallback for older cores; the live list is core ``kanban_wait_info.PARK_EVENT_KINDS``.
_PARK_EVENTS = ("scheduled", "blocked", "gave_up", "continuation_exhausted", "block_loop_detected", "triage_swept",
                "supervisor_escalated", "continued", "status", "dependency_wait", "agent_block_escalated")
UNREADABLE = "(some boards or replicas were unreadable here: check reverse dependencies before closing)"


def _names(text: Any, tid: str) -> bool:
    """``text`` names card ``tid`` as a whole id (t_159f0063 does not match t_159f00631)."""
    return bool(tid) and re.search(rf"(?<![A-Za-z0-9_]){re.escape(tid)}(?![A-Za-z0-9_])", str(text or "")) is not None


def _ro(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    c.execute("SELECT 1 FROM sqlite_master LIMIT 1")
    return c


def _replica_wait_text(t: dict[str, Any]) -> str:
    """The fields of a replicated card that say what it waits on (never its body: bodies cite cards as context)."""
    run = t.get("run") if isinstance(t.get("run"), dict) else {}
    return json.dumps([run.get("summary"), t.get("park"), t.get("awaiting"), t.get("wake_check"),
                       t.get("block_reason")], default=str)


def dependents_from(board_paths: Callable[[], list[Path]], replica_path: Callable[[], Optional[Path]], *,
                    open_ro: Callable[[Path], sqlite3.Connection] = _ro,
                    park_kinds: Iterable[str] = _PARK_EVENTS) -> Callable[[dict[str, Any]], list[str]]:
    """``dependents(card)``: open cards still waiting on ``card``.

    - Local boards: open children (``task_links``), open cards whose latest park event or wake check names it.
    - Every gateway's cards in the hub replica (``hub.db`` ``replica`` coll ``tasks``, read once per finder: build
      one per tick): open cards whose parents list it or whose last run summary names it (``park``/``awaiting``/
      ``wake_check``/``block_reason`` too, if a replica ever carries them). A remote card that waits only through a
      wake check is not visible here; the wake text's rule covers it. Stale duplicate replica rows can add a false
      "still needed by", which errs toward keeping the card open.
    - A board or replica that cannot be read adds ``UNREADABLE``: unknown is never "nothing depends on it"."""
    park_kinds = tuple(park_kinds)
    replica_cache: list[tuple[list[dict[str, Any]], bool]] = []

    def local(tid: str) -> tuple[list[str], bool]:
        found: list[str] = []
        failed = False
        like = f"%{tid}%"
        for path in board_paths():
            board = path.parent.name if path.parent.parent.name == "boards" else "default"
            try:
                c = open_ro(path)
            except sqlite3.Error:
                failed = True
                continue
            try:
                tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                ids: set[str] = set()
                closed = ",".join("?" for _ in _CLOSED)
                if "task_links" in tables:
                    ids.update(r[0] for r in c.execute(
                        f"SELECT l.child_id FROM task_links l JOIN tasks t ON t.id = l.child_id WHERE l.parent_id = ? "
                        f"AND t.status NOT IN ({closed})", (tid, *_CLOSED)))
                if "task_events" in tables:
                    kinds = ",".join("?" for _ in park_kinds)
                    for task, payload in c.execute(
                            f"SELECT e.task_id, e.payload FROM task_events e JOIN tasks t ON t.id = e.task_id "
                            f"WHERE t.status NOT IN ({closed}) AND e.payload LIKE ? AND e.id = (SELECT MAX(x.id) FROM "
                            f"task_events x WHERE x.task_id = e.task_id AND x.kind IN ({kinds}))",
                            (*_CLOSED, like, *park_kinds)):
                        if _names(payload, tid):
                            ids.add(task)
                if "task_wake_checks" in tables:
                    for task, cmd in c.execute(
                            f"SELECT w.task_id, w.cmd FROM task_wake_checks w JOIN tasks t ON t.id = w.task_id "
                            f"WHERE t.status NOT IN ({closed}) AND w.cmd LIKE ?", (*_CLOSED, like)):
                        if _names(cmd, tid):
                            ids.add(task)
            except sqlite3.Error:
                logger.debug("owner-wake: dependents read failed for %s", path, exc_info=True)
                failed = True
                continue
            finally:
                c.close()
            found.extend(f"{board}/{i}" for i in sorted(ids - {tid}))
        return found, failed

    def replica() -> tuple[list[dict[str, Any]], bool]:
        if not replica_cache:
            rows: list[dict[str, Any]] = []
            failed = False
            try:
                path = replica_path()
                if path is not None:
                    c = open_ro(path)
                    try:
                        raw = c.execute("SELECT data FROM replica WHERE coll = 'tasks'").fetchall()
                    finally:
                        c.close()
                    for (data,) in raw:
                        try:
                            t = json.loads(data)
                        except (TypeError, ValueError):
                            continue
                        if isinstance(t, dict) and t.get("id") and str(t.get("status") or "") not in _CLOSED:
                            rows.append(t)
            except Exception:
                logger.debug("owner-wake: replica unreadable for dependents", exc_info=True)
                failed = True
            replica_cache.append((rows, failed))
        return replica_cache[0]

    def remote(tid: str, seen: set[str]) -> tuple[list[str], bool]:
        rows, failed = replica()
        out = []
        for t in rows:
            other = str(t.get("id"))
            if other == tid or other in seen:
                continue
            parents = t.get("parents") if isinstance(t.get("parents"), list) else []
            if tid in [str(p) for p in parents] or _names(_replica_wait_text(t), tid):
                seen.add(other)
                where = f" on {t['gateway']}" if t.get("gateway") else ""
                out.append(f"{t.get('board') or 'default'}/{other}{where}")
        return out, failed

    def dependents(card: dict[str, Any]) -> list[str]:
        tid = str(card.get("id") or str(card.get("key") or "").rsplit("/", 1)[-1])
        here, failed_here = local(tid)
        there, failed_there = remote(tid, {k.rsplit("/", 1)[-1] for k in here})
        out = list(dict.fromkeys(here + there))
        return out + [UNREADABLE] if (failed_here or failed_there) else out

    return dependents


def chat_info_from(state_dbs: Callable[[], list[Path]], tip: Callable[[str], Optional[str]]
                   ) -> Callable[[str], Optional[dict[str, Any]]]:
    """``chat_info`` over the state DBs. A compressed chat is judged by its compression tip (the live continuation)."""

    def read(sid: str) -> Optional[dict[str, Any]]:
        """The chat's row; None only when every store read cleanly without it; UNKNOWN when a store failed."""
        failed = False
        for path in state_dbs():
            try:
                c = _ro_connect(path, timeout=2)
            except sqlite3.Error:
                failed = True
                continue
            try:
                cols = {r[1] for r in c.execute("PRAGMA table_info(sessions)")}
                pick = ", ".join(x if x in cols else f"NULL AS {x}" for x in ("archived", "end_reason", "source"))
                row = c.execute(f"SELECT {pick} FROM sessions WHERE id = ?", (sid,)).fetchone()
            except sqlite3.Error:
                failed, row = True, None
            finally:
                c.close()
            if row is not None:
                return {"archived": bool(row[0]), "end_reason": row[1], "source": row[2]}
        return UNKNOWN if failed else None

    def info(sid: str) -> Optional[dict[str, Any]]:
        here = read(sid)
        try:
            t = tip(sid)
        except Exception:
            t = None
        if t and t != sid:
            later = read(t)
            if later is not None and not later.get("unknown"):
                return later
            if later is not None:
                return UNKNOWN
        return here

    return info


def supervise_on_board(cards: list[dict[str, Any]], session_id: str, why: str) -> str:
    """A ready card on this gateway's default board for the board supervisor, covering ``cards`` (one orphaned
    chat's newly due cards). Idempotent per chat + card premises."""
    import hashlib

    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing

    assignee = "default"
    try:
        from hermes_cli.config import load_config_readonly
        assignee = (load_config_readonly().get("kanban") or {}).get("orchestrator_profile") or "default"
    except Exception:
        pass
    basis = "|".join(sorted(f"{c.get('key')}:{(c.get('wait') or {}).get('premise')}" for c in cards))
    first = cards[0].get("key")
    more = f" +{len(cards) - 1}" if len(cards) > 1 else ""
    with connect_closing() as conn:
        return kb.create_task(
            conn, title=f"Owner wake: supervise {first}{more} (owning chat {why})"[:200],
            body=supervisor_body(cards, session_id, why), assignee=assignee, created_by="owner-wake",
            idempotency_key=f"owner-wake:{session_id}:{hashlib.sha1(basis.encode()).hexdigest()[:16]}")
