"""chat-closeout: a chat that hands work to Kanban cards always shows what it waits on, and closes itself out when
they finish. Logic lives in core.py; this file wires Hermes hooks and the backend thread.

  post_llm_call     waiting ledger: stamp the chat's open cards at every turn end (and close a settled batch)
  pre_llm_call      a turn that runs after the whole batch settled gets the close-out brief (handoffs + what to say)
  pre_final_gate    a reply promising to report later with nothing tracked behind it is held (then annotated)
  thread (serve)    owner wake (owner_wake.py): a card parked for an agent-owned reason starts one supervise turn in
                    its owning chat (archived/reaped/gone chat -> board-supervisor card), once per card per premise
  thread (serve)    close-out waker: wake an idle chat whose cards all settled, once per batch state (in-process
                    resume via server._wake_session_turn), at most MAX_WAKES_PER_HOUR per chat; watchdog every 15 min: Kanban card + WARNING for a chat
                    with unseen kanban results > 30 min or a settled batch with no close-out > 30 min
Every hook fails open. [daveey]
"""
from __future__ import annotations

import bisect
import contextlib
import importlib.util
import json
import logging
import math
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

if __package__:
    from . import core
    from . import owner_wake
    from . import routing
else:  # pytest imports the hyphenated plugin dir without a parent package
    _spec = importlib.util.spec_from_file_location("chat_closeout_core", Path(__file__).with_name("core.py"))
    core = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(core)
    core = sys.modules.setdefault("chat_closeout_core", core)
    _spec = importlib.util.spec_from_file_location("chat_closeout_owner_wake", Path(__file__).with_name("owner_wake.py"))
    owner_wake = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(owner_wake)
    owner_wake = sys.modules.setdefault("chat_closeout_owner_wake", owner_wake)
    _spec = importlib.util.spec_from_file_location("chat_closeout_routing", Path(__file__).with_name("routing.py"))
    routing = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(routing)
    routing = sys.modules.setdefault("chat_closeout_routing", routing)

logger = logging.getLogger("chat_closeout")
SKIP_PLATFORMS = {"cron", "kanban", "webhook", "subagent"}
TICK_S = float(os.environ.get("CHAT_CLOSEOUT_TICK_S", "30"))
_state: dict[str, Any] = {}
_thread_lock = threading.Lock()
_waiting_lock = threading.Lock()
_upgrade_lock = threading.Lock()


# ---- environment -----------------------------------------------------------------------------------------------
def _off() -> bool:
    return bool(os.environ.get("CHAT_CLOSEOUT_OFF"))


def _kanban_root() -> Path:
    try:
        from hermes_cli import kanban_db
        return Path(kanban_db.kanban_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def ledger() -> "core.Ledger":
    path = os.environ.get("CHAT_CLOSEOUT_DB") or str(_kanban_root() / "cache" / "chat-closeout" / "ledger.db")
    if _state.get("ledger_path") != path:
        _state.update(ledger_path=path, ledger=core.Ledger(Path(path)))
    return _state["ledger"]


def board_paths() -> list[Path]:
    try:
        from hermes_cli import kanban_session_activity as ksa
        return list(ksa._board_db_paths())
    except Exception:
        root = _kanban_root()
        return [p for p in [root / "kanban.db", *sorted((root / "kanban" / "boards").glob("*/kanban.db"))]
                if p.is_file()]


# ---- cross-pass remote inventory gate (REMOTE-GATE.md) ---------------------------------------------------------
# Core keeps its links projection _LINKS_MAX_AGE_S (15 s), half this thread's 30 s tick, so every pass re-projected
# the origin ledger and hub replica (native replay: ~93 ms of a ~150 ms pass) for an unchanged answer. The gate
# reuses core's own last projection only while every input it read is unchanged and every clock verdict it took
# still holds now; it never extends a verdict past a cut-off and has no age-based reuse.
_REPLICA_FRESH_S = 180.0  # core project(): replica fresh / owner observation recent within 180 s
_GATE_NEEDS = ("remote_links_and_origin_rows", "_links_fingerprint", "_origin_scope", "_links_lock", "_links_cache",
               "one_annotation_snapshot", "_annotation_scope", "_hub_db_path", "_cache_lock", "_remote_last",
               "_remote_scope", "HUB_STALE_AFTER_S", "parked_waits")
_gate_lock = threading.Lock()
_gate: dict[str, Any] = {}  # "memo": the reusable projection and the inputs it was read under


def _gate_reset() -> None:
    with _gate_lock:
        _gate.clear()


def _gate_ready(ksa) -> bool:
    """Core has everything the gate keys on, and no outer ``one_annotation_snapshot`` pins a projection here."""
    return (all(hasattr(ksa, n) for n in _GATE_NEEDS)
            and getattr(ksa._annotation_scope, "reads", None) is None)


def _stat_key(path: Path) -> tuple:
    out = []
    for p in (path, Path(str(path) + "-wal")):
        try:
            st = p.stat()
            out.append((st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns))
        except OSError:
            out.append(None)
    return tuple(out)


def _replica_clock(ksa, now: float) -> Optional[tuple[tuple, Optional[float], Optional[float]]]:
    """``(verdict, last_ok, project_last_ok)`` from the hub replica's sync meta. ``verdict`` holds every clock
    judgement the projection takes from it (core project() freshness, replica staleness), judged at ``now`` on each
    call, never cached. ``last_ok`` as the replica read parses it, ``project_last_ok`` as project() does (the
    ``synced_at`` of origin rows). None when the meta cannot be read: then nothing is reused."""
    meta = _replica_meta(ksa)
    return None if meta is None else _judge_replica(ksa, meta, now)


def _replica_meta(ksa) -> Optional[Any]:
    """The replica sync meta the clock verdicts are judged from: ``"no-replica"``, or ``{last_ok, offline_since}``
    as stored. None when unreadable."""
    path = ksa._hub_db_path()
    if path is None or not Path(path).is_file():
        return "no-replica"
    try:
        conn = core.ro_connect(Path(path))
        try:
            return dict(conn.execute("SELECT k, v FROM meta WHERE k IN ('last_ok', 'offline_since')").fetchall())
        finally:
            conn.close()
    except Exception:
        return None


def _judge_replica(ksa, meta: Any, now: float) -> Optional[tuple[tuple, Optional[float], Optional[float]]]:
    """``_replica_clock`` of stored ``meta`` at ``now``: every verdict judged at ``now``."""
    if meta == "no-replica":
        return ("no-replica",), None, None
    try:
        raw = meta.get("last_ok")
        last_ok = float(raw) if raw not in (None, "") else None
        lo = float(raw or 0)
    except Exception:
        return None
    since = None if last_ok is None else now - last_ok
    verdict = (meta.get("offline_since"), last_ok is None, 0 <= now - lo, now - lo <= _REPLICA_FRESH_S,
               since is not None and since > float(ksa.HUB_STALE_AFTER_S))
    return verdict, last_ok, lo


def _clock_cutoffs(replica: Optional[Path], ledger: Path) -> Optional[tuple[float, ...]]:
    """Every wall time at which an unchanged replica and ledger could project differently: each owner collector's
    per-scope heartbeat and its end (``scope_window`` of that owner), each obligation's owner observation and its
    180 s end. A superset of what core judges (project() owner liveness, ``owner_attests_terminal``,
    ``_moved_evidence``). None when unreadable."""
    try:
        from hermes_cli.kanban_origin_ledger import _owner_evidence
        from hermes_cli.kanban_scope_heartbeat import scope_window
    except ImportError:
        return None
    out: set[float] = set()

    def add(at: Any, window: float) -> None:
        if isinstance(at, (int, float)) and math.isfinite(at):
            out.update((float(at), float(at) + window))

    try:
        if replica is not None and Path(replica).is_file():
            conn = core.ro_connect(Path(replica))
            try:
                infos = conn.execute("SELECT data FROM replica WHERE coll = 'gateway'").fetchall()
            finally:
                conn.close()
            for (raw,) in infos:
                try:
                    info = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                scopes = info.get("task_scopes") if isinstance(info, dict) else None
                if isinstance(scopes, list):
                    window = scope_window(info)
                    for scope in scopes:
                        if isinstance(scope, dict):
                            add(scope.get("observed_at"), window)
        conn = core.ro_connect(ledger)
        try:
            tasks = conn.execute("SELECT task_json FROM origin_obligations").fetchall()
        finally:
            conn.close()
        for (raw,) in tasks:
            try:
                task = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if isinstance(task, dict):
                add(_owner_evidence(task).get("observed_at"), _REPLICA_FRESH_S)
    except Exception:
        return None
    return tuple(sorted(out))


def _alias_sessions(legacy: Optional[list[dict]], rows: list[dict]) -> frozenset[str]:
    """Sessions whose compression tip the projection may resolve (a superset): core's ``superseded`` resolves tips
    only for a legacy card whose ``(key, origin gateway, origin profile)`` matches a receipt row that does not
    already name the card's session, and then only that card's session and the row's two aliases."""
    if not legacy:
        return frozenset()
    by_key: dict[tuple, list[dict]] = {}
    for r in rows:
        by_key.setdefault((r.get("key"), r.get("origin_gateway"), r.get("origin_profile")), []).append(r)
    out: set[str] = set()
    for c in legacy:
        for r in by_key.get((c.get("key"), c.get("origin_gateway"), c.get("origin_profile")), ()):
            if c.get("session") in (r.get("origin_session"), r.get("session")):
                continue
            out.update(str(s) for s in (c.get("session"), r.get("origin_session"), r.get("session")) if s)
    return frozenset(out)


def _alias_tips(sessions: frozenset[str]) -> Optional[tuple]:
    """``((session, compression tip), ...)`` exactly as core's projection resolves them; None when state.db cannot
    be opened. One short-lived connection, closed here (state.db is never observed, IDLE-READS.md)."""
    if not sessions:
        return ()
    try:
        from hermes_constants import get_hermes_home
        from hermes_cli.kanban_origin import _ro
        from hermes_cli.kanban_origin_adapter import _resolve_session
        state_db = get_hermes_home() / "state.db"
        conn = _ro(state_db)
    except Exception:
        return None
    try:
        out = []
        for sid in sorted(sessions):
            try:
                out.append((sid, _resolve_session(state_db, sid, conn)))
            except (OSError, sqlite3.Error, ValueError):
                out.append((sid, None))  # core's tip(): unresolvable is None
        return tuple(out)
    finally:
        conn.close()


def _gate_structure(ksa, ledger: Path) -> Optional[tuple]:
    """Every store the projection reads except state.db: core's own links fingerprint (hub replica task/gateway
    content, each board's content revision, ledger stat) plus the ledger's committed revision (retained observer)."""
    token = core.revision_token(ledger)
    if token is None:
        return None
    return ksa._origin_scope(), ksa._links_fingerprint(), token


def _restamp(memo: dict, last_ok: Optional[float], lo: Optional[float]):
    """``(cards, rows)`` of ``memo`` with ``synced_at`` as the current replica meta gives it, or None when that
    cannot be derived from the stored rows. Only ``synced_at`` follows ``last_ok`` (every hub pull) without a verdict
    changing: origin rows carry project()'s ``last_ok``; live replica cards ``min(owner seen, last_ok)``."""
    cards, rows = memo["cards"], memo["rows"]
    if last_ok == memo["last_ok"] and lo == memo["lo"]:
        return list(cards), list(rows)
    moved = {id(r): (r if r.get("synced_at") == lo else {**r, "synced_at": lo}) for r in rows}
    out = []
    for c in cards:
        if id(c) in moved:
            out.append(moved[id(c)])  # a card core took from the origin rows (the same object)
            continue
        if c.get("via") == "origin":
            return None  # an origin card that is not one of the rows: provenance unknown
        if c.get("stale") is not False:
            if memo["verdict"][-1]:
                return None  # stale replica: a live row is indistinguishable from a remembered one
            out.append(c)  # a remembered (stale) row keeps its stamp
            continue
        s = c.get("synced_at")
        if s is None or memo["last_ok"] is None or not s < memo["last_ok"] or not last_ok:
            return None  # owner seen unknown (the stamp was last_ok itself)
        new = min(s, last_ok)
        out.append(c if new == s else {**c, "synced_at": new})
    return out, list(moved.values())


def _gate_hit(ksa, ledger: Path, now: float):
    """``(cards, rows, core entry)`` when the stored projection is still exactly what core would project now."""
    with _gate_lock:
        memo = _gate.get("memo")
    if memo is None or ksa._links_cache is None:  # core reset its caches: so does the gate
        return None
    if now < memo["t0"]:
        return None
    cut = memo["cutoffs"]
    i = bisect.bisect_left(cut, memo["t0"])
    if i < len(cut) and cut[i] <= now:
        return None  # a heartbeat or observation window opened or closed since the projection
    clock = _replica_clock(ksa, now)
    if clock is None or clock[0] != memo["verdict"]:
        return None
    if _gate_structure(ksa, ledger) != memo["structure"] or _alias_tips(memo["sessions"]) != memo["tips"]:
        return None
    stamped = _restamp(memo, clock[1], clock[2])
    if stamped is None:
        return None
    entry = memo["entry"]
    # Core's own entry shape (expires, scope, fingerprint, cards, rows), restamped: what a pinned reader gets.
    return stamped[0], stamped[1], (entry[0], entry[1], entry[2], stamped[0], stamped[1])


def _gate_project(ksa, ledger: Path, now: float):
    """Core's projection; stored for reuse only when it was projected after the inputs were read, they are unchanged
    afterwards, and nothing in it came from a failed read."""
    scope = ksa._origin_scope()
    try:
        from hermes_constants import get_hermes_home
        state_db: Optional[Path] = get_hermes_home() / "state.db"
    except Exception:
        state_db = None
    # Holding core's projection lock while reading the inputs: no projection is running, so any entry published
    # after this block was projected from inputs at least this new.
    with ksa._links_lock:
        before = ksa._links_cache
        with ksa._cache_lock:
            legacy_before = ksa._remote_last
        clock = _replica_clock(ksa, now)
        structure = _gate_structure(ksa, ledger)
        state = _stat_key(state_db) if state_db is not None else None
    with ksa.one_annotation_snapshot():
        cards, rows = ksa.remote_links_and_origin_rows()
        entry = getattr(ksa._annotation_scope, "links", None)
    with _gate_lock:
        _gate.pop("memo", None)
    with ksa._cache_lock:
        legacy, legacy_scope = ksa._remote_last, ksa._remote_scope
    if (entry is None or entry is before or clock is None or structure is None or state is None
            or legacy is legacy_before or legacy_scope != scope):
        return cards, rows  # a core cache hit, unreadable input, or the replica read failed (stale fallback)
    if state_db is None or _stat_key(state_db) != state or _gate_structure(ksa, ledger) != structure:
        return cards, rows  # an input moved while projecting
    if _replica_clock(ksa, time.time()) != clock:
        return cards, rows  # a pull landed or a verdict flipped while projecting: the rows may mix both
    verdict, last_ok, lo = clock
    if any(r.get("synced_at") != lo for r in rows):
        return cards, rows  # project() read another sync stamp (or none): not this meta
    if verdict[3] and verdict[0] in (None, "") and rows and all(r.get("stale") is not False for r in rows):
        return cards, rows  # a fresh replica yet nothing current: project() may have swallowed a replica error
    sessions = _alias_sessions(legacy, rows)
    tips = _alias_tips(sessions)
    cutoffs = _clock_cutoffs(ksa._hub_db_path(), ledger)
    if tips is None or cutoffs is None:
        return cards, rows
    memo = {"t0": now, "cutoffs": cutoffs, "verdict": verdict, "last_ok": last_ok, "lo": lo,
            "structure": structure, "sessions": sessions, "tips": tips,
            "cards": list(cards), "rows": list(rows), "entry": entry}
    with _gate_lock:
        _gate["memo"] = memo
    return cards, rows


def _gated_inventory(ksa, ledger: Path) -> tuple[list[dict], list[dict]]:
    """``ksa.remote_links_and_origin_rows()``, reused across passes while unchanged (``_gate_hit``). A memo already
    validated by ``_gate_hit``/``_gate_project`` is reused without re-reading its inputs while their cheap content
    keys (``_gate_inputs``) are unchanged; every clock verdict and cut-off is still judged now (``_gate_fast``)."""
    if not _gate_ready(ksa):
        return ksa.remote_links_and_origin_rows()
    now = time.time()
    fast = _gate_fast(ksa, ledger, now)
    if fast is not None:
        return fast
    with _gate_lock:
        prior = _gate.get("memo")
    # Labelled with the inputs read BEFORE validating/projecting; kept only if nothing moved by the end.
    pre, meta = _gate_inputs(ksa, ledger), _replica_meta(ksa)
    hit = _gate_hit(ksa, ledger, now)
    if hit is not None:
        _gate_remember(ksa, ledger, prior, pre, meta, now)
        return hit[0], hit[1]
    out = _gate_project(ksa, ledger, now)
    with _gate_lock:
        stored = _gate.get("memo")
    if stored is not None and stored is not prior and stored["t0"] == now:
        _gate_remember(ksa, ledger, stored, pre, meta, now)
    return out


def _gate_inputs(ksa, ledger: Path) -> Optional[tuple[tuple, Optional[tuple]]]:
    """Cheap content keys covering everything ``_gate_hit`` re-reads, each at least as change-sensitive as the key
    the gate compares: ``(scope, hub replica stat, each core board's and the ledger's committed revision, ledger
    stat), state.db stat``. The hub stat also covers the replica meta (its ``last_ok``) and is the key core's own
    ``_hub_content_signature`` memo uses; boards and the ledger use the retained observers' ``data_version``
    (``core.revision_token``); state.db (alias tips) is only stat'ed, never opened. None: no shortcut."""
    try:
        if getattr(getattr(getattr(ksa, "_revalidate", None), "inputs_pin", None), "fingerprints", None) is not None:
            return None  # an outer pin decides core's fingerprints: no shortcut under it
        from hermes_constants import get_hermes_home
        hub = ksa._hub_db_path()
        boards = tuple(str(p) for p in ksa._board_db_paths())
        tokens = tuple(core.revision_token(Path(p), board=True) for p in boards)
        led = core.revision_token(ledger)
        if led is None or None in tokens:
            return None
        main = (ksa._origin_scope(), str(hub) if hub is not None else None,
                _stat_key(Path(hub)) if hub is not None else None, boards, tokens, led, _stat_key(ledger))
        return main, _stat_key(get_hermes_home() / "state.db")
    except Exception:
        logger.debug("chat-closeout: gate input keys unavailable", exc_info=True)
        return None


def _gate_remember(ksa, ledger: Path, memo: Optional[dict], pre, meta, at: float) -> None:
    """Record ``memo`` (just validated at ``at``, or stored) for ``_gate_fast`` under the input keys ``pre`` read
    before it, only when the keys are unchanged now and ``meta`` judges to the memo's verdict at ``at``."""
    if memo is None or pre is None or meta is None:
        return
    clock = _judge_replica(ksa, meta, at)
    if clock is None or clock[0] != memo["verdict"]:
        return
    if _gate_inputs(ksa, ledger) != pre:
        return  # an input moved while validating or projecting
    with _gate_lock:
        if _gate.get("memo") is memo:
            _gate["fast"] = {"memo": memo, "key": pre, "meta": meta}


def _gate_fast(ksa, ledger: Path, now: float):
    """``(cards, rows)`` of the validated memo while every input key is unchanged; the cut-offs and the replica
    verdicts are judged at ``now`` from the recorded meta (unchanged with the hub stat). None: full gate."""
    with _gate_lock:
        memo, fast = _gate.get("memo"), _gate.get("fast")
    if memo is None or fast is None or fast["memo"] is not memo or ksa._links_cache is None:
        return None
    if now < memo["t0"]:
        return None
    cut = memo["cutoffs"]
    i = bisect.bisect_left(cut, memo["t0"])
    if i < len(cut) and cut[i] <= now:
        return None
    clock = _judge_replica(ksa, fast["meta"], now)
    if clock is None or clock[0] != memo["verdict"]:
        return None
    key = _gate_inputs(ksa, ledger)
    if key is None or key[0] != fast["key"][0] or (memo["sessions"] and key[1] != fast["key"][1]):
        return None
    return _restamp(memo, clock[1], clock[2])


_PARKED_NEEDS = ("_board_db_paths", "_parked_pairs", "_local_wait_rows", "_describe_waits", "_board_slug",
                 "_remote_links", "_remote_wait")


class _Uncacheable(Exception):
    """A read core would swallow (and degrade) failed: answer it uncached, never store it."""


class _StrictEvents:
    """The board connection, except that a failed park-event query on a board that has ``task_events`` raises
    ``_Uncacheable`` instead of the ``sqlite3.Error`` core's ``_local_wait_rows`` swallows into status-only info:
    that degraded answer must not be kept until the next commit. A board without the table degrades as in core."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def execute(self, sql: str, *args: Any):
        try:
            return self._conn.execute(sql, *args)
        except sqlite3.Error as exc:
            if "task_events" in sql and self._conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'task_events'").fetchone():
                raise _Uncacheable(str(exc)) from exc
            raise

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def _board_parked_rows(ksa, conn: sqlite3.Connection, strict: bool = True) -> tuple[frozenset, tuple, dict[str, str]]:
    """Clock-free rows of one board's parked cards: ``(pairs, (parked, events), titles)``, exactly what core
    ``parked_waits`` reads per board before judging wait ages (``_describe_waits``). Pure in the board's content.
    ``strict``: a transient park-event failure raises (``_StrictEvents``) rather than return a degraded answer."""
    pairs = frozenset(ksa._parked_pairs(conn))
    if not pairs:
        return pairs, ({}, {}), {}
    rows = ksa._local_wait_rows(_StrictEvents(conn) if strict else conn, {tid for _, tid in pairs})
    titles: dict[str, str] = {}
    ids = sorted(rows[0])  # wait info is only ever given for parked rows
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        titles.update((str(t), str(n or "")) for t, n in conn.execute(
            f"SELECT id, title FROM tasks WHERE id IN ({','.join('?' for _ in chunk)})", chunk))
    return pairs, rows, titles


def _local_parked(ksa, now: Optional[float]) -> list[dict]:
    """The local half of core ``parked_waits``. Core opened a fresh connection per board per pass (native/proxy:
    the largest cost left in the owner wake); here each board's clock-free rows come from its registered retained
    observer and are reused while its ``data_version`` is unchanged. Wait ages are judged now, on every call."""
    out: list[dict] = []
    for path in ksa._board_db_paths():
        try:
            try:
                pairs, rows, titles = core._structural_reads.read(
                    path, ("parked-wait-rows",), lambda conn: _board_parked_rows(ksa, conn), board=True)
            except _Uncacheable:  # core's degraded answer for this pass, from a one-shot read, never stored
                conn = core.ro_connect(Path(path), board=True)
                try:
                    pairs, rows, titles = _board_parked_rows(ksa, conn, strict=False)
                finally:
                    conn.close()
            info = ksa._describe_waits(*rows, now=now) if pairs else {}
        except Exception as exc:  # core: a board read failure skips that board
            logger.debug("chat-closeout: parked waits read failed for %s: %s", path, exc)
            continue
        board = ksa._board_slug(path)
        for sid, tid in sorted(pairs):
            if tid in info:
                out.append({"session": sid, "id": tid, "board": board, "key": f"{board}/{tid}",
                            "title": titles.get(tid, ""), "remote": False, "gateway": None, "wait": info[tid]})
    return out


def _parked_waits(ksa, now: Optional[float]) -> list[dict]:
    """Core ``parked_waits``, composed from core's own helpers: local rows from retained board observers
    (``_local_parked``), remote cards from the gated projection (``_gated_inventory``). Wait info is computed at
    call time from the rows, never stored. Older cores: core's own ``parked_waits``."""
    if not all(hasattr(ksa, n) for n in _PARKED_NEEDS):
        return ksa.parked_waits(now=now)
    out = _local_parked(ksa, now)
    try:
        from hermes_constants import get_hermes_home
        ledger = get_hermes_home() / "kanban" / "origin-ledger.db"
        if ledger.exists() and _gate_ready(ksa):
            remote = _gated_inventory(ksa, ledger)[0]
        else:
            remote = ksa._remote_links()
    except Exception as exc:
        logger.warning("chat-closeout: parked waits: remote links unreadable, local cards only this pass: %s", exc)
        remote = []
    for card in remote:
        if card.get("stale"):
            continue
        wait = ksa._remote_wait(card)
        if wait is None:
            continue
        out.append({"session": str(card.get("session")), "id": str(card.get("id")), "board": card.get("board"),
                    "key": str(card.get("key")), "title": str(card.get("title") or ""), "remote": True,
                    "gateway": card.get("gateway"), "wait": wait})
    return out


def remote_cards() -> list[dict]:
    """Use core's authoritative projection, including released rows (session_tasks omits those).

    No replica/status-only fallback can prove a release. Older cores retain unknown
    remote batches rather than silently treating a missing card as completed.
    """
    from hermes_constants import get_hermes_home
    from hermes_cli import kanban_session_activity as ksa
    from hermes_cli.kanban_origin_adapter import _gateway
    from hermes_cli.kanban_origin_ledger import project
    from hermes_cli.profiles import get_active_profile_name

    path = get_hermes_home() / 'kanban' / 'origin-ledger.db'
    if not path.exists():
        return ksa._remote_links()
    shared = getattr(ksa, "remote_links_and_origin_rows", None)
    if shared is not None:
        # Newer cores keep the ledger rows of their cached projection: same rows, no second project() per pass.
        # Across passes the projection is reused only while every input it reads is unchanged (REMOTE-GATE.md).
        cards, rows = _gated_inventory(ksa, path)
        return [card for card in cards if card.get('via') != 'origin'] + rows
    replica = ksa._hub_db_path()
    rows = project(path, replica=replica,
                   gateway=_gateway(replica or path.with_name('absent-replica.db')),
                   profile=get_active_profile_name())
    # Core alone decides which historical associations the receipts supersede.
    # Its inventory also retains unsuperseded legacy obligations; don't lose those
    # when adding released rows (which the open inventory deliberately omits).
    return [card for card in ksa._remote_links() if card.get('via') != 'origin'] + rows


def hub_db_path() -> Optional[Path]:
    """This gateway's hermes-hub replica (core ``_hub_db_path``), or None."""
    try:
        from hermes_cli import kanban_session_activity as ksa
        return ksa._hub_db_path()
    except Exception:
        return None


def _hub_fresh() -> bool:
    """The hub replica synced recently and is not offline (core's ``HUB_STALE_AFTER_S`` rule). No replica, or an
    unreadable one, is not fresh: nothing then counts as a healthy observation of a missing entry."""
    try:
        from hermes_cli import kanban_session_activity as ksa
        path = ksa._hub_db_path()
        if path is None:
            return False
        conn = core.ro_connect(Path(path))
        try:
            meta = dict(conn.execute("SELECT k, v FROM meta").fetchall())
        finally:
            conn.close()
        last_ok = float(meta.get("last_ok") or 0)
        return not meta.get("offline_since") and 0 <= time.time() - last_ok <= float(ksa.HUB_STALE_AFTER_S)
    except Exception:
        logger.debug("chat-closeout: hub freshness unavailable", exc_info=True)
        return False


def _replica_completed_at(cards: list[dict], failed_out: Optional[list] = None) -> dict[int, float]:
    """``{id(card): completed_at}`` from the hub replica's task records (``[profile/]board/id`` under the owner
    gateway) for finished shepherd rows that carry no completion time. Read-only; an unreadable replica gives {}
    (and is noted in ``failed_out``: such a read must never be reused)."""
    path = hub_db_path()
    if path is None or not cards:
        return {}
    out: dict[int, float] = {}
    try:
        conn = core.ro_connect(Path(path))
    except Exception:
        if failed_out is not None:
            failed_out.append(path)
        return {}
    try:
        for card in cards:
            rk = "/".join(str(card[k]) for k in ("profile", "board", "id") if card.get(k))
            r = conn.execute("SELECT data FROM replica WHERE gw=? AND coll='tasks' AND key=?",
                             (card.get("gateway"), rk)).fetchone()
            if r is None:
                continue
            data = json.loads(r[0])
            if str(data.get("id")) == str(card["id"]) and data.get("status") in core.DONE_STATUSES \
                    and data.get("completed_at") is not None:
                out[id(card)] = float(data["completed_at"])
    except Exception:
        if failed_out is not None:
            failed_out.append(path)
        logger.debug("chat-closeout: replica completion times unavailable", exc_info=True)
    finally:
        conn.close()
    return out


_BOARD_SLUG = re.compile(r'[a-z0-9][a-z0-9_-]*')


def _read_shadow(board: str, ids: set[str]) -> tuple[bool, "core.ShadowIds"]:
    """``(fresh, ids present)`` from ``sync-shadow/<board>.db``; raises when it cannot be read. The ids compare as a
    set and carry each found task's ``core.SHADOW_FIELDS`` (``.rows``, shared: copy before use).

    Fresh = exactly one sync-state row naming this board, pulled within 120 s and not future-dated."""
    path = _kanban_root() / 'kanban' / 'sync-shadow' / f'{board}.db'
    # Rows are reused while the shadow's committed revision is unchanged; freshness is judged now, every call.
    state, found = core.shadow_rows(path, ids)
    fresh = len(state) == 1 and state[0][0] == board and state[0][1] is not None \
        and 0 <= time.time() - float(state[0][1]) <= 120
    return fresh, found


def _shadow_card(key: str, board: str, row: dict) -> dict:
    """A remote card read from a fresh full-board shadow (``via`` 'shadow'): a ledger key that neither a local board
    nor the origin/shepherd projection resolves, e.g. a hub card a hub session created that was never origin-bound
    to this chat. ``core.is_settled`` settles it when done/archived; otherwise it is working (or an ask)."""
    parts = key.split('/')
    row = {k: v for k, v in row.items() if k != 'created_on'}
    card = {**row, 'id': parts[-1], 'board': board, 'remote': True, 'stale': False, 'via': 'shadow',
            'unfinished': row.get('status') not in core.DONE_STATUSES, 'key': key}
    if len(parts) == 4:
        card.update(gateway=parts[0], profile=parts[1])
    elif len(parts) == 3:
        card['gateway'] = parts[0]
    if card.get('completed_at') is not None:
        try:
            card['completed_at'] = float(card['completed_at'])
        except (TypeError, ValueError):
            card['completed_at'] = None
    return card


def _shadow_owner_ok(key: str, row: dict) -> bool:
    """A key that names a gateway (three/four parts) resolves only from a shadow row that gateway created
    (``created_on``): the shadow is shared across gateways, and another owner's card of that id is evidence against
    absence, never permission to settle this entry. A bare id names no owner."""
    return '/' not in key or (row.get('created_on') is not None and str(row['created_on']) == key.split('/')[0])


def _board_absence_veto(keys: set[str]) -> set[str]:
    """``_board_coverage``'s veto alone (nothing resolved): the inventory-failure path."""
    return _board_coverage(keys, resolve=False)[1]


def _board_coverage(keys: set[str], exclude: frozenset = frozenset(),
                    resolve: bool = True) -> tuple[dict[str, dict], set[str]]:
    """``(resolved, veto)`` for keys no local board or projection row resolved.

    Unresolved keys need full-board coverage, not absence from a linked-card projection.
    A Board shadow is the whole board across gateways/profiles. Any matching id
    vetoes absence (even a different owner). Missing, stale or unreadable coverage
    also vetoes absence. One read per board per pass.

    A four-part key names its board. A bare id names none, and a three-part key's middle part meant profile or
    board, so both are checked against every board this host knows of beyond the local boards ``core.read_cards``
    already read: each ``sync-shadow/*.db`` and each link-only ``sync-links/*.db`` board. The id on any of them
    vetoes; so does any of them lacking a fresh, readable shadow. A host with neither keeps the earlier rule (local
    boards + projection are its whole coverage).

    ``resolve``: a key whose id is on a FRESH shadow with a readable status comes back as a ``_shadow_card``
    instead of a veto: a four-part key on the board it names; a bare / three-part key only when every known board
    is fresh and exactly one holds the id; a key naming a gateway only from a row that gateway created
    (``_shadow_owner_ok``). Such a bare / three-part key that a resolved four-part key of the same id
    and board also names gets that key as ``canonical_key`` (``core.reconcile`` collapses it). ``exclude`` (keys
    the read already found ambiguous) never resolves. Stale, missing or status-less coverage never resolves.
    """
    groups: dict[str, set[str]] = {}
    veto: set[str] = set()
    bare: set[str] = set()   # bare ids and three-part keys: no single board named
    for key in keys:
        parts = key.split('/')
        if len(parts) in (1, 3):
            bare.add(key)
            continue
        if len(parts) != 4:
            continue
        board = parts[2]
        if not _BOARD_SLUG.fullmatch(board):
            veto.add(key)
            continue
        groups.setdefault(board, set()).add(key)
    three = {k for k in bare if k.count('/') == 2}
    # Historical forms used BOTH gateway/profile/id and gateway/board/id. Without a projected canonical owner or a
    # single fresh shadow holding the id, no one board proves absence: always uncertain unless resolved below.
    veto.update(three)
    bare_boards: set[str] = set()
    if bare:
        try:
            kanban = _kanban_root() / 'kanban'
            for sub in ('sync-shadow', 'sync-links'):
                if (kanban / sub).is_dir():
                    bare_boards.update(p.stem for p in (kanban / sub).glob('*.db')
                                       if _BOARD_SLUG.fullmatch(p.stem))
        except Exception:
            logger.debug('chat-closeout: board list unavailable', exc_info=True)
            veto.update(bare)
            bare = set()
    tail = lambda key: key.rsplit('/', 1)[-1]
    bare_ids = {tail(k) for k in bare}
    gap = False
    resolved: dict[str, dict] = {}
    holders: dict[str, list[tuple[str, dict]]] = {}  # bare id -> [(board, row)] on fresh boards
    for board in sorted(set(groups) | bare_boards):
        wanted = groups.get(board, set())
        ids = {tail(key) for key in wanted} | (bare_ids if board in bare_boards else set())
        try:
            fresh, found = _read_shadow(board, ids)
        except Exception:
            logger.debug('chat-closeout: board absence coverage unavailable: %s', board, exc_info=True)
            fresh, found = False, core.ShadowIds()
        rows = getattr(found, 'rows', None) or {}
        if not fresh:
            veto.update(wanted)
            gap = gap or board in bare_boards
        for key in wanted:
            if tail(key) not in found:
                continue
            row = rows.get(tail(key))
            if resolve and fresh and key not in exclude and row and row.get('status') and _shadow_owner_ok(key, row):
                resolved[key] = _shadow_card(key, board, dict(row))
            else:
                veto.add(key)
        if board in bare_boards:
            for key in bare:
                if tail(key) in found:
                    veto.add(key)
            for cid in bare_ids & found:
                holders.setdefault(cid, []).append((board, dict(rows.get(cid) or {}) if fresh else {}))
    if gap:
        veto.update(bare)  # a board this host knows of has no fresh full read: no bare id is observed absent
    elif resolve:
        for key in sorted(bare - exclude):
            held = holders.get(tail(key)) or []
            if len(held) != 1 or not held[0][1].get('status') or not _shadow_owner_ok(key, held[0][1]):
                continue
            board, row = held[0]
            card = _shadow_card(key, board, row)
            sibling = sorted(k for k in resolved if k.count('/') == 3 and tail(k) == tail(key)
                             and k.split('/')[2] == board
                             and (key.count('/') == 0 or k.split('/')[0] == key.split('/')[0]))
            if len(sibling) == 1:
                card = {**resolved[sibling[0]], 'canonical_key': sibling[0]}
            resolved[key] = card
            veto.discard(key)
    return resolved, veto


def _read_key(ids: list[str], light: bool) -> Optional[tuple]:
    """Content key of ``read_cards``'s local half: the ids, the mode, the board list and each board's committed
    revision (retained observer ``data_version``). None when a board cannot be observed: no reuse."""
    boards = tuple(str(p) for p in board_paths())
    tokens = tuple(core.revision_token(Path(p), board=True) for p in boards)
    if None in tokens:
        return None
    return light, tuple(sorted(set(ids))), boards, tokens


def _same_rows(a: list[dict], b: list[dict]) -> bool:
    """The very same inventory row objects in the same order: the gate handed back its unchanged projection."""
    return len(a) == len(b) and all(x is y for x, y in zip(a, b))


def _fresh_card(card: dict) -> dict:
    out = dict(card)
    if isinstance(out.get("parents"), list):
        out["parents"] = list(out["parents"])
    return out


_read_memo_lock = threading.Lock()
_read_memo: dict[bool, dict] = {}  # light -> the last reusable read (``_reused_read``)


def _reused_read(ids: list[str], remote_inventory: Callable[[], list[dict]], light: bool,
                 key: Optional[tuple]) -> Optional[dict]:
    """The stored read of exactly these ids when nothing it was derived from changed: the same board revisions
    (``key``), the very same inventory rows (``_same_rows``) and, if the hub replica filled a completion time, the
    same replica stat. Absence coverage is time-judged and recomputed here. None: read afresh."""
    with _read_memo_lock:
        snap = _read_memo.get(light)
    if snap is None or key is None or snap["key"] != key:
        return None
    try:
        remote = remote_inventory()
    except Exception:
        return None  # the full read reports it (and stores nothing)
    if not _same_rows(remote, snap["rows"]):
        return None
    if snap["hub"] is not None:
        hub = hub_db_path()
        current = (str(hub), _stat_key(Path(hub))) if hub is not None else ("no-replica",)
        if current != snap["hub"]:
            return None
    want = set(ids)
    out = core.Cards({k: _fresh_card(c) for k, c in snap["cards"].items()})
    shadow, veto = _board_coverage(want - set(out), exclude=snap["uncertain"])
    out.update(shadow)
    out.absence_veto = frozenset(veto)
    out.uncertain = frozenset(snap["uncertain"] | out.absence_veto)
    return out


def read_cards(ids: list[str], remote_inventory: Optional[Callable[[], list[dict]]] = None,
               light: bool = False, reuse: bool = False) -> dict:
    """Local and remote cards by ledger key. ``light``: local cards carry only ``core.SETTLE_FIELDS``.

    Returns a ``core.Cards``: ``uncertain`` names keys that may exist but could not be resolved (never evidence
    they are gone), ``readable`` whether the remote inventory was read. A legacy bare / 3-part key with no local
    match that matches exactly one inventory owner key by card id comes back as that card with ``canonical_key``
    (``core.reconcile`` rewrites the ledger); several owners leave it unresolved and uncertain. A finished shepherd
    row with no completion time takes it from another inventory row of the same owner key, else the hub replica,
    so its transcript acknowledgement can be proven.

    ``reuse`` (a pass's union read, IDLE-READS.md): a successful read is kept and returned again (fresh copies) while
    its content keys are unchanged (``_reused_read``); a read with any failure is never kept."""
    remote_inventory = remote_inventory or remote_cards
    content = _read_key(ids, light) if reuse else None
    if content is not None:
        hit = _reused_read(ids, remote_inventory, light, content)
        if hit is not None:
            return hit
    uncertain: set[str] = set()
    failed: list = []
    want = set(ids)
    # Every key's card id is also looked up as a bare id on every local board: a missing 2/3/4-part key whose id
    # some local card carries is not "matches nothing" (never unlinked), and a 3-part legacy key with a local card
    # of that id never migrates to a remote owner.
    probe = want | {str(i).rsplit('/', 1)[-1] for i in want if '/' in str(i)}
    cards = core.read_cards(board_paths(), probe, light=light, uncertain_out=uncertain, failed_out=failed)
    local_ids = {str(k).rsplit('/', 1)[-1] for k in cards} | {str(k).rsplit('/', 1)[-1] for k in uncertain}
    cards = {k: v for k, v in cards.items() if k in want}
    uncertain &= want
    out = core.Cards()
    try:
        remote = remote_inventory()
    except Exception:
        # Qualified local cards remain usable. Old bare IDs have lost ownership:
        # without the remote inventory they cannot safely resolve to local done.
        logger.debug('chat-closeout: origin projection unavailable', exc_info=True)
        out.update({key: card for key, card in cards.items() if '/' in key})
        out.uncertain = frozenset(uncertain | {i for i in ids if i not in out})
        out.absence_veto = frozenset(_board_absence_veto(want - set(out)))
        out.readable = False
        return out
    # Every key shape ends in the card id except a legacy remote row's stored ``key`` (then card_key IS that key):
    # only cards that could hit ``want`` pay for card_key (the inventory holds thousands of rows; a pass wants a
    # few hundred ids).
    tails = {str(i).rsplit('/', 1)[-1] for i in want}
    merged: dict[str, dict] = {}
    times: dict[str, float] = {}
    by_id: dict[str, set[str]] = {}
    for card in remote:
        cid = str(card['id'])
        if cid in cards:
            cards.pop(cid)  # ambiguous historical bare-ID ledger
            uncertain.add(cid)
        if cid not in tails and card.get('key') not in want:
            continue
        key = core.card_key(card)
        by_id.setdefault(cid, set()).add(key)
        if card.get('completed_at') is not None:
            times.setdefault(key, float(card['completed_at']))
        previous = merged.get(key)
        # Several origin bindings can name one owner; no unsafe first match.
        if previous is None or not core.is_settled(card):
            merged[key] = card
    for key in want & set(merged):
        cards[key] = merged[key]
    for key in want - set(cards):
        parts = key.split('/')
        owners = by_id.get(parts[-1]) or set()
        if parts[-1] in local_ids:
            uncertain.add(key)  # a local card has this id: neither migrated nor ever unlinked
        elif len(parts) in (1, 3) and len(owners) == 1:
            canonical = next(iter(owners))
            cards[key] = {**merged[canonical], 'canonical_key': canonical}
        elif owners:
            uncertain.add(key)  # several owners, or another owner key than this one: not "matches nothing"
    fill = [c for c in cards.values() if c.get('remote') and c.get('via') == 'shepherd'
            and c.get('status') in core.DONE_STATUSES and c.get('completed_at') is None]
    hub_key = None
    if fill:
        ask = [c for c in fill if core.card_key(c) not in times]
        if ask:
            hub = hub_db_path()
            hub_key = (str(hub), _stat_key(Path(hub))) if hub is not None else ("no-replica",)
        replica = _replica_completed_at(ask, failed_out=failed)
        fill_ids = {id(c) for c in fill}
        for key, card in list(cards.items()):
            if id(card) in fill_ids:
                at = times.get(core.card_key(card), replica.get(id(card)))
                if at is not None:
                    cards[key] = {**card, 'completed_at': at}  # a copy: inventory rows are core's cache
    out.update(cards)
    # Time-judged (shadow freshness), so never part of the memo below: recomputed on every reuse too.
    shadow, veto = _board_coverage(want - set(cards), exclude=frozenset(uncertain & want))
    out.update(shadow)
    out.absence_veto = frozenset(veto)
    out.uncertain = frozenset((uncertain & want) | out.absence_veto)
    if content is not None and not failed and _read_key(ids, light) == content:
        # Labelled with the board revisions read BEFORE the read; nothing committed since.
        snap = {"key": content, "rows": list(remote), "hub": hub_key, "uncertain": frozenset(uncertain & want),
                "cards": {k: _fresh_card(c) for k, c in cards.items()}}
        with _read_memo_lock:
            _read_memo[light] = snap
    return out


def _pass_reader(light: bool = False) -> Callable[[list[str]], dict]:
    """``read_cards`` for one pass over the ledger: the remote inventory (a full hub-replica projection) and the
    local boards are read once and reused for every row, not once per pending row. A failed read fails every row
    the same way. ``light`` for a pass that only decides settlement (the sidebar's waiting source).

    ``read.healthy()``: the inventory was read, no replica row is stale, and the hub replica is fresh: only such a
    pass counts as observing that an entry is missing (``core.track_unlinked``)."""
    memo: list[tuple[Optional[list[dict]], Optional[BaseException]]] = []

    def inventory() -> list[dict]:
        if not memo:
            try:
                memo.append((remote_cards(), None))
            except Exception as exc:
                memo.append((None, exc))
        rows, exc = memo[0]
        if exc is not None:
            raise exc
        return rows

    # Boards are read once per pass too: the first call reads every id the ledger's pending rows hold (one open per
    # board) and later rows take their subset. core.read_cards judges each id on its own, so the subset equals a
    # per-row read; ids outside the pass's union (a row written mid-pass) are read directly.
    union: dict[str, Any] = {}

    def read(ids: list[str]) -> dict:
        if "ids" not in union:
            try:
                union["ids"] = {i for row in ledger().pending() for i in row["batch"]}
            except Exception:
                union["ids"] = set()
        want = set(ids)
        if not want or not want <= union["ids"]:
            return read_cards(ids, remote_inventory=inventory, light=light)
        if "cards" not in union:
            union["cards"] = read_cards(sorted(union["ids"]), remote_inventory=inventory, light=light, reuse=True)
        full = union["cards"]
        out = core.Cards({key: card for key, card in full.items() if key in want})
        out.uncertain = frozenset(set(getattr(full, "uncertain", ())) & want)
        out.absence_veto = frozenset(set(getattr(full, "absence_veto", ())) & want)
        out.readable = getattr(full, "readable", True)
        return out

    def healthy() -> bool:
        if "healthy" not in union:
            try:
                rows = inventory()
            except Exception:
                union["healthy"] = False
            else:
                union["healthy"] = (not any(r.get("stale") is not False and r.get("via") != "origin"
                                            for r in rows) and _hub_fresh())
        return union["healthy"]

    read.healthy = healthy  # type: ignore[attr-defined]
    return read


def ensure_upgraded(read: Optional[Callable[[list[str]], dict]] = None) -> bool:
    """Run the one-time shepherd upgrade (``core.upgrade_shepherd_rows``) before anything can wake or brief a chat.
    Every waking path calls this first. Durable marker in the ledger; cheap once done."""
    led = ledger()
    if _state.get("upgraded") == str(led.path):
        return True
    with _upgrade_lock:
        if _state.get("upgraded") == str(led.path):
            return True
        done = led.meta_get(core.UPGRADE_SHEPHERD_KEY) is not None
        if not done:
            done = core.upgrade_shepherd_rows(led, read or _pass_reader())
        if done:
            _state["upgraded"] = str(led.path)
        return done


def _upgraded(read: Optional[Callable[[list[str]], dict]] = None) -> None:
    try:
        ensure_upgraded(read)
    except Exception:
        logger.warning("chat-closeout: shepherd upgrade pass failed; retrying next pass", exc_info=True)


# One waiting pass (``_proof_pass``): its connections, SessionDB and lineage lookups, reused by every proof in it.
_pass_local = threading.local()


@contextlib.contextmanager
def _proof_pass():
    """Share reads across one ``pending_closeouts`` pass: ~50 proofs each opened a SessionDB (for the compression
    tip) and a connection per state.db, ~45 ms of the sidebar's waiting source. Closed when the pass ends."""
    if getattr(_pass_local, "res", None) is not None:
        yield
        return
    res: dict[str, Any] = {"pool": core.ReadPool(), "tips": {}, "db": None}
    _pass_local.res = res
    try:
        yield
    finally:
        _pass_local.res = None
        res["pool"].close()
        if res["db"] is not None:
            res["db"].close()


def state_dbs() -> list[Path]:
    res = getattr(_pass_local, "res", None)
    if res is not None and "state_dbs" in res:
        return res["state_dbs"]
    root = _kanban_root()
    out = [root / "state.db"]
    out += sorted((root / "profiles").glob("*/state.db")) if (root / "profiles").is_dir() else []
    out = [p for p in out if p.is_file()]
    if res is not None:
        res["state_dbs"] = out
    return out


def open_card_ids(session_id: str) -> list[str]:
    """Cards the chat created or shepherds that are not done/archived (queued, running, blocked, review)."""
    from hermes_cli import kanban_session_activity as ksa
    return sorted({core.card_key(t) for t in ksa.session_tasks([session_id], ksa.LISTED_STATUSES)})


_ACTIVE_FALLBACK = ("todo", "ready", "running", "scheduled", "review")


def subscribed_open_cards(session_id: str, db_paths: Optional[list[Path]] = None) -> list[str]:
    """Working cards created elsewhere that notify this chat (``kanban_notify_subs``): a worker's follow-up card, or
    a card the chat commented on. Their completion notice arrives here as a new turn, so they back a promise just
    like a card the chat created. ``session_tasks`` only sees cards whose creator/origin is the chat, so without
    this a chat waiting on four subscribed cards was told it had none. Same link rule as ask-gate's
    ``linked_cards``; a board without the subs table (older core) contributes nothing."""
    try:
        from hermes_cli import kanban_session_activity as ksa
        active = tuple(ksa.ACTIVE_STATUSES)
    except Exception:
        active = _ACTIVE_FALLBACK
    ids = [i for i in _lineage(session_id) if i]
    if not ids:
        return []
    found: set[str] = set()
    for path in db_paths if db_paths is not None else board_paths():
        try:
            conn = core.ro_connect(Path(path))
        except sqlite3.Error:
            continue
        try:
            rows = conn.execute(
                "SELECT DISTINCT t.id FROM tasks t JOIN kanban_notify_subs s ON s.task_id = t.id "
                f"WHERE s.chat_id IN ({','.join('?' for _ in ids)}) "
                f"AND t.status IN ({','.join('?' for _ in active)})", (*ids, *active)).fetchall()
            found.update(str(r[0]) for r in rows)
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return sorted(found)


def session_proof(sid: str, tid: str, card: dict) -> bool:
    """Transcript evidence that a finished card's final notice was delivered to the chat and answered. Inside a
    ``_proof_pass`` every card of one lineage is proven from one ``core.AckIndex`` per state.db (one transcript
    read per lineage, not one per card)."""
    try:
        at = card.get("completed_at")
        if at is None:
            return False
        res = getattr(_pass_local, "res", None)
        if res is None:
            return core.ack_proven(state_dbs(), _lineage(sid), tid, float(at))
        ids = [i for i in dict.fromkeys(_lineage(sid)) if i]
        if not ids or not tid:
            return False
        acks = res.setdefault("acks", {})
        for path in state_dbs():
            idx = acks.get((str(path), tuple(ids)))
            if idx is None:
                idx = acks[(str(path), tuple(ids))] = core.AckIndex(path, ids)
            if idx.proves(tid, float(at), res["pool"]):
                return True
        return False
    except Exception:
        logger.debug("chat-closeout: ack proof unavailable", exc_info=True)
        return False


def _reconcile_asks(sid: str, row: dict, cards: dict) -> dict:
    return core.reconcile_asks(row, cards, lambda _t, c: core.quoted_ask(
        state_dbs(), _lineage(sid), c, pool=_pass_pool()))


def _eligible(session_id: str, platform: str = "") -> bool:
    return bool(session_id) and not _off() and not os.environ.get("HERMES_KANBAN_TASK") \
        and (platform or "").lower() not in SKIP_PLATFORMS


# ---- hooks -----------------------------------------------------------------------------------------------------
def _turn_succeeded(conversation_history: Any) -> bool:
    """The turn ended in a successful final reply: its last assistant message has text, no tool calls, a final
    display kind and ``finish_reason`` stop/end_turn (the transcript ack rule, ``core.AckIndex``). No history (an
    older core that passes none) keeps the previous behaviour: post_llm_call only fires for an uninterrupted
    turn with a response."""
    if not isinstance(conversation_history, list):
        return True
    for msg in reversed(conversation_history):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        text = content if isinstance(content, str) else " ".join(
            str(p.get("text") or "") for p in content if isinstance(p, dict)) if isinstance(content, list) else ""
        return (bool(text.strip()) and not msg.get("tool_calls") and msg.get("finish_reason") in ("stop", "end_turn")
                and msg.get("display_kind") in (None, "", "final"))
    return False


def post_llm_call(session_id: str = "", platform: str = "", conversation_history: Any = None, **_: Any) -> None:
    """Waiting ledger: every turn end stamps ``waiting_on`` (the chat's open cards)."""
    if not _eligible(session_id, platform):
        return
    try:
        _upgraded()
        observed = _state.setdefault("observed", {}).pop(session_id, None)
        asked = _state.setdefault("observed_ask", {}).pop(session_id, None)
        out = core.record_turn_end(ledger(), session_id, open_card_ids(session_id), read_cards,
                                   observed_sig=observed, succeeded=_turn_succeeded(conversation_history))
        if asked:
            row = ledger().get(session_id) or {}
            batch = set(row.get("batch") or [])
            ids = {t for t, _ in asked if t in batch}
            ledger().write(session_id, covered=set(row.get("covered") or []) | ids,
                           relayed=set(row.get("relayed") or []) | {p for t, p in asked if t in ids})
        if out.get("open") or out.get("closeout"):
            logger.info("chat-closeout: %s waiting_on=%s%s", session_id, out.get("open"),
                        " (close-out turn: covered %s)" % sorted(out.get("covered") or [])
                        if out.get("closeout") else "")
    except Exception:
        logger.debug("chat-closeout post_llm_call failed", exc_info=True)


def pre_llm_call(session_id: str = "", platform: str = "", user_message: Any = None, **_: Any) -> Optional[dict]:
    """Conditional: only when this chat's whole batch has settled and no close-out turn has run for it (the live
    path: a kanban notice turn in an open chat). No tokens otherwise."""
    if not _eligible(session_id, platform):
        return None
    try:
        _state.setdefault("observed", {}).pop(session_id, None)
        _state.setdefault("observed_ask", {}).pop(session_id, None)
        _upgraded()
        row = ledger().get(session_id)
        if not row or not (row.get("batch") or core.unlinked_removed(row)):
            return None
        cards = read_cards(row["batch"])
        fix = _reconcile_asks(session_id, row, cards)
        fix.update(core.reconcile_done({**row, **fix}, cards, lambda t, c: session_proof(session_id, t, c)))
        if fix:
            ledger().write(session_id, **fix)
            row = {**row, **fix}
            if not row["batch"] and not core.unlinked_removed(row):
                return None
        key = core.pending_key(row, cards)
        if not key and row.get("closing") is None:
            cands = core.ask_candidates(row, cards) if core.ask_relay_key(row, cards) else []
            if cands:  # the user is here anyway: tell this turn what waits on them (covered at its end)
                _state.setdefault("observed_ask", {})[session_id] = cands
                return {"context": core.ask_relay_prompt(row, cards, [t for t, _ in cands], now=time.time())}
        if not key or row.get("closing") is not None:
            return None  # nothing new to report, or the waker already briefed this turn
        _state["observed"][session_id] = key
        prompt = core.closeout_prompt(row, cards, now=time.time())
        # the brief names each unlinked entry; this turn is its one mention
        ledger().write(session_id, closing=sorted(row["batch"]), unlinked=core.briefed(row.get("unlinked"), time.time()))
        return {"context": prompt}
    except Exception:
        logger.debug("chat-closeout pre_llm_call failed", exc_info=True)
        return None


def _backed(session_id: str) -> bool:
    try:
        if open_card_ids(session_id):
            from hermes_cli import kanban_session_activity as ksa
            if ksa.session_tasks([session_id], ksa.ACTIVE_STATUSES):
                return True
    except Exception:
        logger.debug("chat-closeout: card probe failed; letting the reply through", exc_info=True)
        return True  # every hook fails open: a broken probe never blocks a reply
    try:
        if subscribed_open_cards(session_id):
            return True
    except Exception:
        logger.debug("chat-closeout: subscription probe failed; letting the reply through", exc_info=True)
        return True
    try:
        from hermes_cli import process_session_activity as psa
        if psa.background_process_counts().get(session_id):
            return True
    except Exception:
        pass
    try:
        from hermes_cli import cron_session_activity as csa
        if session_id in csa.cron_owner_sessions():
            return True
    except Exception:
        pass
    return False


def pre_final_gate(session_id: str = "", platform: str = "", final_response: str = "",
                   turn_messages: list | None = None, attempt: int = 0, **_: Any) -> Optional[dict]:
    """Turn-end lint: a promise to report later must have a tracked thing behind it."""
    if not _eligible(session_id, platform) or not isinstance(final_response, str):
        return None
    try:
        promise = core.find_promise(final_response)
        if not promise or _backed(session_id):
            return None
        logger.info("chat-closeout: unbacked promise in %s (%r), attempt %d", session_id, promise, attempt)
        if attempt < 1:
            return {"hold": True, "nudge": core.LINT_NUDGE.format(tag=core.TAG, promise=promise)}
        return {"append": core.LINT_APPEND}
    except Exception:
        logger.debug("chat-closeout pre_final_gate failed", exc_info=True)
        return None


pre_final_gate.needs_buffer = True

# ---- sidebar: a settled batch stays "waiting" until its close-out ----------------------------------------------
def waiting_source() -> dict[str, list[str]]:
    """kanban_session_activity waiting source: chats whose cards all settled but whose close-out has not run yet.
    Cached for 3 s (the session list polls). Single-flight: polls that arrive while a pass runs wait for it and
    share its answer instead of each starting their own."""
    if _off():
        return {}
    with _waiting_lock:
        hit = _state.get("waiting_cache")
        if hit and time.monotonic() - hit[0] < 3.0:
            return hit[1]
        try:
            with _proof_pass():
                # Settlement only: the light read skips the per-card handoff/ask subqueries nobody reads here.
                reader = _pass_reader(light=True)
                _upgraded(reader)
                out = core.pending_closeouts(ledger(), reader, proof=session_proof)
        except Exception:
            logger.debug("chat-closeout waiting source failed", exc_info=True)
            out = {}
        _state["waiting_cache"] = (time.monotonic(), out)
        return out


def _register_waiting_source() -> None:
    try:
        from hermes_cli import kanban_session_activity as ksa
    except Exception:
        return
    reg = getattr(ksa, "register_waiting_source", None)
    if reg is not None:  # older cores lack the seam: the row just goes green a little early
        reg("chat-closeout", waiting_source)


# ---- the backend thread: close-out waker + watchdog -------------------------------------------------------------
def _server():
    """The tui_gateway server module when this process is a Desktop/TUI backend that can wake chats, else None."""
    mod = sys.modules.get("tui_gateway.server")
    return mod if mod is not None and hasattr(mod, "_wake_session_turn") else None


def _lineage(sid: str) -> list[str]:
    ids = [sid]
    try:
        tip = _compression_tip(sid)
        if tip and tip not in ids:
            ids.append(tip)
    except Exception:
        pass
    return ids


def _unseen_counts() -> Optional[dict[str, int]]:
    """Unseen kanban results per session, or None when unreadable: a failed read must never look like "all
    notices delivered" (that would wake a chat before its results landed).

    Reads only what that count is made of, core's local-board notify subscriptions (``core.unseen_counts`` over
    core's board list and event kinds), not ``_all_counts``: that whole projection's remote-links refresh was
    most of this thread's idle CPU. Cores without those names keep the old projection read."""
    try:
        from hermes_cli import kanban_session_activity as ksa
        kinds = getattr(ksa, "UNSEEN_KINDS", None)
        paths = getattr(ksa, "_board_db_paths", None)
        if kinds is None or paths is None:
            return dict(ksa._all_counts()[2])
        return core.unseen_counts(list(paths()), kinds)
    except Exception:
        logger.debug("chat-closeout: unseen counts unavailable", exc_info=True)
        return None


def wake_tick(*, now: Optional[float] = None, wake: Optional[Callable[..., str]] = None,
              unseen: Optional[dict[str, int]] = None, last_user: Optional[Callable[[list[str]], float]] = None,
              read: Optional[Callable[[list[str]], dict]] = None,
              proof: Optional[Callable[[str, str, dict], bool]] = None, mono: Optional[float] = None,
              healthy: Optional[Callable[[], bool]] = None) -> list[dict]:
    """One close-out pass over the ledger. Returns what it did (for logs/tests). One remote-inventory read per
    tick, shared by every pending row, and one ``_proof_pass`` (state.db connections, compression tips and
    transcript proofs shared by every row, as the sidebar's waiting pass does). ``healthy`` (default: the pass
    reader's ``healthy``) says whether this pass observes missing entries (``core.track_unlinked``)."""
    with _proof_pass():
        return _wake_tick(now=now, wake=wake, unseen=unseen, last_user=last_user, read=read, proof=proof,
                          mono=mono, healthy=healthy)


def _wake_tick(*, now, wake, unseen, last_user, read, proof, mono=None, healthy=None) -> list[dict]:
    now = time.time() if now is None else now
    mono = time.monotonic() if mono is None else mono
    read = read or _pass_reader()
    healthy = healthy or getattr(read, "healthy", None) or (lambda: False)
    if wake is None:
        srv = _server()
        if srv is None:
            return []
        wake = srv._wake_session_turn
    unseen = _unseen_counts() if unseen is None else unseen
    if unseen is None:
        return []
    if not ensure_upgraded(read):
        logger.debug("chat-closeout: shepherd upgrade incomplete (inventory unreadable or stale); retrying")
    last_user = last_user or (lambda ids: core.last_user_at(state_dbs(), ids, pool=_pass_pool()))
    led = ledger()
    done: list[dict] = []
    for row in led.pending():
        sid = row["session_id"]
        if _session_source(sid).lower() in SKIP_PLATFORMS:
            # A worker/cron transcript is not a chat: it cannot be resumed (resume is refused), so a ledger row for
            # it (stamped by a turn that ran without HERMES_KANBAN_TASK) would retry and raise ask-failed forever.
            led.write(sid, batch=[], open=[], failure_since=None, retry_after=0)
            logger.info("chat-closeout: dropped ledger row of non-chat session %s", sid)
            continue
        if _session_gone(sid):
            # A deleted chat (e.g. a worker's throwaway Desktop test chat) can never take a close-out turn, so its
            # row would sit pending and the watchdog would file a no-closeout card for nobody.
            led.write(sid, batch=[], open=[], failure_since=None, retry_after=0)
            logger.info("chat-closeout: dropped ledger row of deleted chat %s", sid)
            continue
        cards = core.canonical_view(read(row["batch"]))
        fix = core.reconcile(row, cards)
        fix.update(_reconcile_asks(sid, {**row, **fix}, cards))
        fix.update(core.track_unlinked({**row, **fix}, cards, healthy=healthy, now=now, mono=mono))
        fix.update(core.reconcile_done({**row, **fix}, cards,
                                       lambda t, c, sid=sid: (proof or session_proof)(sid, t, c)))
        if fix:
            led.write(sid, **fix)
            row = {**row, **fix}
            if not row["batch"] and not core.unlinked_removed(row):
                continue
        key = core.pending_key(row, cards)
        if key and key != row.get("settled_sig"):
            led.write(sid, settled_sig=key, settled_at=now)
        ids = _lineage(sid)
        if key and core.wakes_last_hour(row, now) >= core.MAX_WAKES_PER_HOUR:
            if _state.setdefault("capped", {}).get(sid) != key:
                _state["capped"][sid] = key
                logger.warning("chat-closeout: %s hit the %d wakes/hour cap; close-out deferred", sid,
                               core.MAX_WAKES_PER_HOUR)
        n_unseen, user_at = sum(unseen.get(i, 0) for i in ids), last_user(ids)
        due = core.closeout_due(row, cards, unseen=n_unseen, last_user_at=user_at, now=now)
        if not due:
            relay = core.ask_relay_due(row, cards, unseen=n_unseen, last_user_at=user_at, now=now)
            if relay and led.claim(sid, relay, now):
                done.append(_relay(led, sid, row, cards, relay, now, wake))
            continue
        if not led.claim(sid, due, now):
            continue
        new_ids = core.uncovered(row, cards)
        # Mark the batch covered and the turn as a close-out BEFORE it starts: the turn's own end (or a card it
        # creates) must never read as a fresh settled batch.
        wakes = [t for t in row.get("wakes") or [] if 0 <= now - float(t) < 3600] + [now]
        prompt = core.closeout_prompt(row, cards, now=now)  # names each unlinked entry: this turn is its mention
        led.write(sid, covered=set(row.get("covered") or []) | set(new_ids), closing=sorted(row["batch"]),
                  wakes=wakes, wake_count=int(row.get("wake_count") or 0) + 1,
                  unlinked=core.briefed(row.get("unlinked"), now))
        wake_error = None
        try:
            result = wake(sid, prompt, display_kind="kanban_notice",
                          display_metadata={"display_text": core.notice_text(row, cards), "closeout": True,
                                            "task_ids": sorted(row["batch"])},
                          label="chat-closeout")
        except Exception as exc:
            wake_error = exc
            logger.warning("chat-closeout: wake failed for %s", sid, exc_info=True)
            result = "error"
        if result == "started":
            led.write(sid, wake_result="started", failure_since=None, retry_after=0)
            logger.info("chat-closeout: woke %s for its close-out (%d cards settled, %d new; wake #%d)", sid,
                        len(row["batch"]), len(new_ids), int(row.get("wake_count") or 0) + 1)
        else:
            # busy/refused/error: undo the coverage + count; a later tick (or the owning backend) retries
            led.write(sid, covered=set(row.get("covered") or []), closing=None, wakes=row.get("wakes") or [],
                      wake_count=int(row.get("wake_count") or 0), unlinked=row.get("unlinked") or {})
            result, backoff = _failure_backoff(sid, result, now, error=wake_error)
            led.release(sid, due, result, now=now, backoff=backoff)
            logger.info("chat-closeout: close-out wake of %s deferred (%s)", sid, result)
        done.append({"session_id": sid, "result": result, "sig": due})
    return done


def _pass_pool() -> Optional["core.ReadPool"]:
    res = getattr(_pass_local, "res", None)
    return res["pool"] if res is not None else None


@contextlib.contextmanager
def _ro(path: Path):
    """A read-only connection: the running pass's pooled one (left open), else a fresh one closed after use."""
    pool = _pass_pool()
    if pool is not None:
        yield pool.get(path)
        return
    c = core.ro_connect(path)
    try:
        yield c
    finally:
        c.close()


def _session_scope(sid: str) -> tuple[Optional[Path], str]:
    for path in state_dbs():
        with _ro(path) as c:
            if c.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
                return path, path.parent.name if path.parent.parent.name == "profiles" else "default"
    return None, "default"


_HERMES_SID = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{6,}$")


def _session_gone(sid: str) -> bool:
    """True only on positive evidence the chat was deleted: a Hermes-format chat id that no state DB holds, every
    store read cleanly (a failed read is unknown, never gone). Non-Hermes ids (remote/legacy keys) never count."""
    if not _HERMES_SID.match(sid or ""):
        return False
    paths = state_dbs()
    if not paths:
        return False
    for path in paths:
        try:
            with _ro(path) as c:
                if c.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
                    return False
        except Exception:
            return False
    return True


def _session_source(sid: str) -> str:
    """state.db ``sessions.source`` of ``sid`` ('' when unknown)."""
    path, _ = _session_scope(sid)
    if path is None:
        return ""
    try:
        with _ro(path) as c:
            r = c.execute("SELECT source FROM sessions WHERE id=?", (sid,)).fetchone()
        return (r[0] or "") if r else ""
    except Exception:
        return ""


QUOTA_RECHECK_S = 3600  # a quota-walled chat is re-tried at most hourly (the user may switch its model meanwhile)


def _quota_wall(sid: str, now: Optional[float] = None) -> Optional[float]:
    """Reset time of a known quota wall on the chat's exact-pinned subscription route, else None.

    An exact pin is the user's own model choice: a wake turn may not leave it, so every wake fails at agent build
    until the pool resets (the core's automation replay already schedules one replay at that reset). That is a
    clock wait, not a broken delivery path: the relay backs off and the watchdog files no agent card for it.
    Only a known cooldown counts (``quota_unavailable``); any doubt (no row, other profile, unreadable pool) is
    None, so a real failure is still retried and flagged."""
    now = time.time() if now is None else now
    try:
        path, _ = _session_scope(sid)
        from hermes_constants import get_hermes_home
        if path is None or path != get_hermes_home() / "state.db":
            return None  # another profile's pool is not ours to load
        with _ro(path) as c:
            r = c.execute("SELECT model_config FROM sessions WHERE id=?", (sid,)).fetchone()
        routing = (json.loads(r[0] or "{}") if r else {}).get("model_routing") or {}
        if not routing.get("exact_pin") or routing.get("billing_group") != "subscription":
            return None
        from agent.credential_pool import load_pool
        from agent.model_routing_unavailable import quota_unavailable
        err = quota_unavailable(load_pool(routing["selected_provider"], allowed_auth_types={"oauth"}),
                                routing["selected_model"])
        reset = float(getattr(err, "resets_at", 0) or 0)
    except Exception:
        logger.debug("chat-closeout: quota-wall probe failed for %s", sid, exc_info=True)
        return None
    return reset if reset > now else None


def _wake_failure_reset(sid: str, now: float, error: Optional[Exception] = None) -> Optional[float]:
    """The build exception's clock, including failures on the automation-replay path.

    Core records exception.resets_at in agent_failure before returning an error from
    _wake_session_turn. A second pool probe can disagree with that actual failure.
    """
    import math
    try:
        if error is not None:
            from agent.model_routing_unavailable import SubscriptionQuotaUnavailable
            reset = getattr(error, "resets_at", None)
            if (isinstance(error, SubscriptionQuotaUnavailable) and isinstance(reset, (int, float))
                    and not isinstance(reset, bool) and math.isfinite(reset) and reset > now):
                return float(reset)
            return None
        srv = _server()
        if srv is None:
            return None
        live = srv._find_live_session_by_key(sid)
        if live is None:
            tip = _canonical_session(sid)
            live = srv._find_live_session_by_key(tip) if tip != sid else None
        session = live[1] if live else {}
        failure = session.get("agent_failure") or {}
        reset = failure.get("failure_resets_at") if session.get("agent_error") else None
        if isinstance(reset, (int, float)) and not isinstance(reset, bool) and math.isfinite(reset) and reset > now:
            return float(reset)
    except Exception:
        logger.debug("chat-closeout: build-failure clock unavailable for %s", sid, exc_info=True)
    return None


def _quota_wait(row: dict, now: float) -> bool:
    """Persisted quota clocks are authoritative across processes until retry is due."""
    return row.get("wake_result") == "quota" and (
        float(row.get("retry_after") or 0) > now or bool(_quota_wall(row["session_id"], now)))


def _failure_backoff(sid: str, result: str, now: float, *, error: Optional[Exception] = None) -> tuple[str, float]:
    """(result, backoff) for a failed wake: ``quota`` with an hourly-capped wait to the reset when the chat's exact
    pin is quota-walled, else the plain result with the claim TTL (0 for busy)."""
    if result == "busy":
        return result, 0
    if result == "error" and (reset := _wake_failure_reset(sid, now, error) or _quota_wall(sid, now)):
        return "quota", max(1.0, min(reset - now, QUOTA_RECHECK_S))
    return result, owner_wake.CLAIM_TTL_S


def _moved_owner(sid: str) -> Optional[str]:
    path, _ = _session_scope(sid)
    return routing.moved_owner(sid, db_path=path)


def _handoff_ask(led, sid: str, gateway: str, key: str, prompt: str, now: float) -> str:
    _, profile = _session_scope(sid)
    return routing.handoff(led, sid, gateway, key, prompt, now, profile=profile)


def _relay(led, sid: str, row: dict, cards: dict, key: str, now: float, wake: Callable[..., str]) -> dict:
    """Ask relay: ONE turn telling the chat a batch card waits on the user while other batch cards still work. The
    asked cards are covered and their premises recorded BEFORE the turn (it is not a close-out: no ``closing``, the
    working cards keep their own close-out); a failed wake undoes both."""
    cands = core.ask_candidates(row, cards)
    ids = [t for t, _ in cands]
    batch = set(row.get("batch") or [])
    before = {k: row.get(k) for k in ("covered", "relayed", "wakes", "wake_count")}
    wakes = [t for t in row.get("wakes") or [] if 0 <= now - float(t) < 3600] + [now]
    relayed = {p for p in row.get("relayed") or [] if p.rsplit("@", 1)[0] in batch} | {p for _, p in cands}
    led.write(sid, covered=set(row.get("covered") or []) | set(ids), relayed=relayed, wakes=wakes,
              wake_count=int(row.get("wake_count") or 0) + 1)
    wake_error = None
    try:
        gateway = _moved_owner(sid)
        prompt = core.ask_relay_prompt(row, cards, ids, now=now)
        for tid in ids:  # detection: the coach counts stale relays from these lines (self-heal agent-792a49aa96)
            age = core.ask_age_s(cards.get(tid) or {}, now)
            logger.info("chat-closeout: ask_relay aged=%s card=%s session=%s",
                        "unknown" if age is None else f"{age / 3600:.1f}", tid, sid)
        if gateway:
            result = _handoff_ask(led, sid, gateway, key, prompt, now)
        else:
            result = wake(sid, prompt, display_kind="kanban_notice",
                          display_metadata={"display_text": core.ask_notice_text(cards, ids), "ask_relay": True,
                                            "task_ids": ids}, label="chat-closeout")
    except Exception as exc:
        wake_error = exc
        logger.warning("chat-closeout: ask relay wake failed for %s", sid, exc_info=True)
        result = "error"
    if result in ("started", "forwarded"):
        led.write(sid, wake_result=result, failure_since=None, retry_after=0)
        logger.info("chat-closeout: relayed %d ask(s) to %s while %d card(s) still work (%s)", len(ids), sid,
                    sum(1 for t in batch if not core.is_settled(cards.get(t))), ", ".join(ids))
    else:
        led.write(sid, covered=set(before["covered"] or []), relayed=set(before["relayed"] or []),
                  wakes=before["wakes"] or [], wake_count=int(before["wake_count"] or 0))
        result, backoff = _failure_backoff(sid, result, now, error=wake_error)
        led.release(sid, key, result, now=now, backoff=backoff)
        logger.info("chat-closeout: ask relay to %s deferred (%s)", sid, result)
    return {"session_id": sid, "result": result, "sig": key, "ask_relay": ids}


def wake_log() -> "owner_wake.WakeLog":
    path = os.environ.get("CHAT_CLOSEOUT_OWNER_WAKE_DB") or str(_kanban_root() / "cache" / "chat-closeout" / "owner-wake.db")
    if _state.get("wake_log_path") != path:
        _state.update(wake_log_path=path, wake_log=owner_wake.WakeLog(Path(path)))
    return _state["wake_log"]


def _compression_tip(sid: str) -> Optional[str]:
    """Read-only and closed. The sidebar's waiting pass asks once per finished card, and a writable SessionDB()
    runs schema init plus a system-wide open-file scan (~60 ms CPU) and was never closed: 23 of them per sidebar
    poll held the Desktop backend near 100% CPU (core/backend-writable-open-guard.md)."""
    from hermes_state import SessionDB
    res = getattr(_pass_local, "res", None)
    if res is not None:  # one read-only SessionDB and one lookup per session for the whole waiting pass
        if sid not in res["tips"]:
            if res["db"] is None:
                res["db"] = SessionDB(read_only=True)
            res["tips"][sid] = res["db"].get_compression_tip(sid)
        return res["tips"][sid]
    db = SessionDB(read_only=True)
    try:
        return db.get_compression_tip(sid)
    finally:
        db.close()


def owner_wake_tick(*, now: Optional[float] = None, cards: Optional[list[dict]] = None,
                    wake: Optional[Callable[..., str]] = None,
                    chat_info: Optional[Callable[[str], Optional[dict]]] = None,
                    supervise: Optional[Callable[[list[dict], str, str], str]] = None,
                    last_user: Optional[Callable[[str], float]] = None,
                    dependents: Optional[Callable[[dict], list[str]]] = None) -> list[dict]:
    """Owner wake (owner_wake.py): a card parked for an agent-owned reason starts ONE supervise turn in the chat that
    owns it (archived/reaped/gone chat -> a board-supervisor card). Needs core ``parked_waits``; older cores: no-op."""
    try:
        from hermes_cli import kanban_session_activity as ksa
    except ImportError:
        ksa = None
    if cards is None:
        if ksa is None or not hasattr(ksa, "parked_waits"):
            return []
        cards = _parked_waits(ksa, now)
    if wake is None:
        srv = _server()
        wake = srv._wake_session_turn if srv is not None else None
    if dependents is None and ksa is not None and hasattr(ksa, "_board_db_paths"):
        kw: dict = {}
        if hasattr(ksa, "_open_ro"):
            kw["open_ro"] = ksa._open_ro  # survives a WAL db whose -wal/-shm were just removed
        try:
            from hermes_cli import kanban_wait_info as kwi
            kw["park_kinds"] = kwi.PARK_EVENT_KINDS
        except (ImportError, AttributeError):
            pass
        dependents = owner_wake.dependents_from(
            ksa._board_db_paths, getattr(ksa, "_hub_db_path", lambda: None), **kw)
    return owner_wake.tick(
        dependents=dependents,
        cards=cards, wake=wake, log=wake_log(),
        chat_info=chat_info or owner_wake.chat_info_from(state_dbs, _compression_tip),
        supervise=supervise or owner_wake.supervise_on_board,
        last_user=last_user or (lambda sid: core.last_user_at(state_dbs(), _lineage(sid))), now=now,
        canonical=_canonical_session)


def _canonical_session(sid: str) -> str:
    """The chat a session id belongs to now: its compression tip, else itself."""
    try:
        return _compression_tip(sid) or sid
    except Exception:
        return sid


MAX_POST_ATTEMPTS = 3  # failed card filings per incident key before one ERROR and no more retries


def _incident_session(key: str) -> Optional[str]:
    """The chat a watchdog incident key is about: named in the key, or (handoff keys) in its handoff row."""
    parts = key.split(":")
    if len(parts) == 4 and parts[1] in ("unseen", "no-closeout", "ask-failed", "stuck"):
        return parts[2] or None
    if len(parts) == 3 and parts[1] == "handoff":
        try:
            conn = routing._conn(ledger())
            try:
                row = conn.execute("SELECT session_id FROM handoffs WHERE id=?", (parts[2],)).fetchone()
            finally:
                conn.close()
            return (row[0] or None) if row else None
        except Exception:
            logger.debug("chat-closeout: handoff session lookup failed for %s", key, exc_info=True)
    return None


def _origin_ref(key: str) -> Optional[str]:
    """``@session:<profile>@<gateway>/<chat>`` for the chat ``key`` closes out, or None when this gateway's name or
    the chat is unknown. Without it the card's only link to its chat was its title, so every card its worker filed
    got ``creator_chats_unresolved`` and its result reached no chat (t_01a199c830931fc5). [daveey]"""
    sid = _incident_session(key)
    if not sid:
        return None
    try:
        from hermes_cli.kanban_origin import gateway_names
        from hermes_cli.profiles import get_active_profile_name
        gateway, profile = gateway_names()[0], get_active_profile_name() or "default"
    except Exception:
        logger.debug("chat-closeout: gateway/profile name unavailable", exc_info=True)
        return None
    if not gateway:
        logger.warning("chat-closeout: this gateway has no name, so card %s is not linked to chat %s", key, sid)
        return None
    return f"@session:{profile}@{gateway}/{_canonical_session(sid)}"


def _link_existing(conn, tid: str, ref: str) -> bool:
    """Bind an existing card to its chat with core's own creation-time path (``initialize_ownership``: created link,
    ``origin`` notify sub whose cursor starts at the card's latest event), then let core's creator-chat backfill
    re-resolve the children its worker already filed. True when a link was added."""
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_creator_chats import backfill, initialize_ownership
    from hermes_cli.kanban_origin import parse_origin
    origin = parse_origin(ref)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='task_chat_links'").fetchone():
        return False  # an unsynced board has no chat links to resolve children through
    if conn.execute("SELECT 1 FROM task_chat_links WHERE task_id=? AND chat_gateway=? AND chat_session=? "
                    "AND released=0", (tid, origin.gateway, origin.session)).fetchone():
        return False
    with kb.write_txn(conn):
        initialize_ownership(conn, tid, None, (), int(time.time()), ref)
    backfill(conn)
    return True


def relink_open_cards() -> list[str]:
    """Backfill: link each card this watchdog filed to the chat it closes out, when the card is still open or (done)
    still has an open card its worker filed that resolves to no chat. Leaves every other card alone."""
    from hermes_cli.kanban_db_connect import connect_closing
    linked: list[str] = []
    with connect_closing() as conn:
        rows = conn.execute(
            "SELECT p.id, p.idempotency_key FROM tasks p WHERE p.created_by='chat-closeout' "
            "AND p.idempotency_key IS NOT NULL AND (p.status NOT IN ('done','archived') OR EXISTS ("
            "  SELECT 1 FROM task_events e JOIN tasks c ON c.id=e.task_id WHERE e.kind='created' "
            "  AND json_valid(e.payload) AND json_extract(e.payload,'$.creator_task_id')=p.id "
            "  AND c.status NOT IN ('done','archived') AND NOT EXISTS (SELECT 1 FROM task_events r "
            "  WHERE r.task_id=c.id AND r.kind='creator_chats_resolved')))").fetchall()
        for tid, key in rows:
            # A resolved-and-refiled card's key carries ':resolved:<id>'; the incident is still its prefix.
            key = (key or "").split(":resolved:")[0]
            if not _WATCHDOG_KEY.match(key):
                continue
            ref = _origin_ref(key)
            if ref and _link_existing(conn, tid, ref):
                linked.append(tid)
    return linked


def file_card(title: str, body: str, key: str) -> str:
    """One Kanban card per stuck-chat incident on this gateway's default board (agent-fixable state belongs on the
    board, never the Inbox). ``key`` is the idempotency key, so a repeat of the same incident reuses the card."""
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing

    machine = owner_wake.placement_evidence.home_machine()  # discovery failure must not file unpinned
    runs_on = [f"machine:{machine}"] if machine else None
    assignee = "default"
    try:
        from hermes_cli.config import load_config_readonly
        assignee = (load_config_readonly().get("kanban") or {}).get("orchestrator_profile") or "default"
    except Exception:
        pass
    # A worker's cross_session send into a human chat is refused by core. The old text said "use cross_session
    # send into the chat" as if it delivered; the worker on t_01a18f20b8f29e9e followed it, was refused and parked
    # instead of verifying. The refusal only arms the card's FINAL SEND line (as ask-gate's chat_delivery says). [daveey]
    text = (f"{body}\n\nchat-closeout watchdog incident `{key}`.\n\nFix: first `cross_session read` the chat "
            "(retained compacted rows included) to check whether it already reported these cards' results; if it "
            "did, record that row as evidence. If not, inspect why the close-out waker / unseen-result sweeper did "
            "not deliver (gui.log, errors.log) and fix that path. To deliver text from a Kanban worker: a worker's "
            "cross_session send never reaches the chat (core refuses it); call it once only so the refusal "
            "subscribes the chat to your card, then kanban_complete with a line `FINAL SEND for <chat ref>: "
            "<text>`, which the chat receives when your card completes. "
            "This is agent work: do not block on the user to resume a chat. Complete this card once the "
            "chat has reported its cards' results; the watchdog also closes it when its condition clears.")
    text += "\n\n" + owner_wake.placement_evidence.home_pin_note(
        "the stuck chat's delivery and watchdog state must be repaired and checked on this gateway")
    ref = _origin_ref(key)
    with connect_closing() as conn:
        # Core create_task deduplicates even DONE rows. Preserve their incident key
        # in an archival namespace so a genuinely cleared/recurrent signature can file anew.
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET idempotency_key=idempotency_key || ':resolved:' || id "
                         "WHERE idempotency_key=? AND created_by='chat-closeout' "
                         "AND status IN ('done','archived')", (key,))
        existed = conn.execute("SELECT id FROM tasks WHERE idempotency_key=? AND status != 'archived'",
                               (key,)).fetchone()
        tid = kb.create_task(conn, title=f"chat-closeout: {title}"[:200], body=text, assignee=assignee,
                             created_by="chat-closeout", idempotency_key=key, chat_origin=ref, runs_on=runs_on)
        if existed and ref:
            _link_existing(conn, tid, ref)  # a reused pre-fix card gets the link it was filed without
        return tid


_WATCHDOG_KEY = re.compile(r"^chat-closeout:(?:unseen|no-closeout|ask-failed|stuck):[^:]+:[^:]+$"
                           r"|^chat-closeout:handoff:[^:]+$")


def _cleared_evidence(key: str, unseen: dict[str, int], led) -> Optional[str]:
    parts = key.split(":")
    if parts[1] in ("unseen", "no-closeout") and _session_gone(parts[2]):
        return f"chat {parts[2]} was deleted (absent from every state.db); nothing can hand it results"
    if parts[1] == "unseen":
        return (f"unseen count 0 for {parts[2]}" if unseen.get(parts[2], 0) == 0
                else f"unseen episode {parts[3]} ended; a different episode is now observed")
    if parts[1] == "no-closeout":
        row = led.get(parts[2])
        if row is None:
            return f"ledger row absent for {parts[2]}"
        if row.get("done_sig") == parts[3]:
            return f"done_sig matched {parts[3]}; done_at={row.get('done_at')}"
        if row.get("settled_sig") != parts[3]:
            return f"batch moved on; settled_sig={row.get('settled_sig')!r}, batch={row.get('batch')!r}"
        return None  # unobserved/paused is not proof that this batch cleared
    if parts[1] == "stuck":
        row = led.get(parts[2]) or {}
        if not row.get("batch"):
            return f"batch of {parts[2]} cleared (closed out or every entry resolved)"
        return (f"a found entry is working again or every entry resolved for {parts[2]}; "
                f"batch={sorted(row.get('batch') or [])!r}")
    if parts[1] == "handoff":
        conn = routing._conn(led)
        try:
            row = conn.execute('SELECT receipt FROM handoffs WHERE id=?', (parts[2],)).fetchone()
            return f"delivery receipt for {parts[2]}: {row[0]}" if row else f"handoff row {parts[2]} absent"
        finally:
            conn.close()
    row = led.get(parts[2])
    if row is None:
        return f"ask relay ledger row absent for {parts[2]}"
    since = row.get("failure_since")
    if since is None or str(int(since)) != parts[3]:
        return f"ask relay failure_since changed for {parts[2]}"
    return f"ask relay no longer pending for {parts[2]}"


def resolve_card(key: str, resolution: dict) -> bool:
    """Close only this watchdog's own incident; never take over an active worker.

    Claim ready/review cards by CAS before completing, so even a dispatcher claim
    whose worker PID has not yet been published cannot be closed underneath it.
    A failed resolution resumes only its own named claim on the next tick.
    """
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing

    summary = (f"Watchdog incident `{key}` cleared (first observed at Unix {resolution['at']}); "
               f"evidence: {resolution['evidence']}.")
    with connect_closing() as conn:
        row = conn.execute("SELECT id, status FROM tasks WHERE idempotency_key=? "
                           "AND created_by='chat-closeout' ORDER BY created_at DESC, id DESC LIMIT 1",
                           (key,)).fetchone()
        if row is None:
            return False  # unavailable/deleted card is not proof of a successful close
        tid, status = row['id'], row['status']
        if status in core.DONE_STATUSES:
            return True
        run_id = kb.live_run_id(conn, tid)
        task = kb.get_task(conn, tid)
        own_claim = task.claim_lock == 'chat-closeout:resolution'
        if run_id is not None and not own_claim:
            note = summary + " A run is live; leaving it untouched and retrying on the next watchdog tick."
            # add_comment's nested savepoint keeps check + insert atomic across backends.
            with kb.write_txn(conn):
                if not conn.execute("SELECT 1 FROM task_comments WHERE task_id=? AND author=? AND body=?",
                                    (tid, 'chat-closeout', note)).fetchone():
                    kb.add_comment(conn, tid, 'chat-closeout', note)
            return False
        if status in ('blocked', 'scheduled', 'triage'):
            if not kb.unblock_task(conn, tid):
                return False
        if not own_claim:
            task = kb.get_task(conn, tid)
            claim = kb.claim_review_task if task.status == 'review' else kb.claim_task
            claimed = claim(conn, tid, claimer='chat-closeout:resolution')
            if claimed is None:
                return False  # dispatcher won; never close its newly admitted run
            run_id = claimed.current_run_id
        return kb.complete_task(conn, tid, summary=summary, expected_run_id=run_id,
                                metadata={'watchdog_key': key, 'cleared_at': resolution['at'],
                                          'evidence': resolution['evidence']})


def watchdog_tick(*, now: Optional[float] = None, mono: Optional[float] = None,
                  unseen: Optional[dict[str, int]] = None, post: Optional[Callable[[str, str, str], Any]] = None,
                  force: bool = False, read: Optional[Callable[[list[str]], dict]] = None) -> list[str]:
    """Flag stuck pipelines (once per chat+state): unseen kanban results > 30 min, or a settled batch with no
    close-out > 30 min. Conditions are observed every tick; flagging runs in one process per 15 min.

    Ages: the persisted wall-clock stamp must be > 30 min AND this process must have seen the condition for
    > 30 min of monotonic time, so a lid sleep (monotonic clock stops) or a restart never fakes a stale pipeline.
    The flag is recorded only after the Kanban card was filed, so a failed filing retries next run, up to
    MAX_POST_ATTEMPTS per key; then one ERROR and no more retries until the condition changes.
    Returns the flag keys raised. One remote-inventory read per tick, shared by every pending row."""
    if post is None and not _state.get("relinked"):
        # Once per process on the real route: cards filed before they carried their chat link get it now.
        try:
            linked = relink_open_cards()
            _state["relinked"] = True
            if linked:
                logger.info("chat-closeout: linked %d open watchdog card(s) to their chats: %s", len(linked),
                            ", ".join(linked))
        except Exception:
            logger.warning("chat-closeout: relinking open watchdog cards failed; retrying next tick", exc_info=True)
    post = post or file_card
    now = time.time() if now is None else now
    mono = time.monotonic() if mono is None else mono
    read = read or _pass_reader()
    _upgraded(read)
    led = ledger()
    unseen = _unseen_counts() if unseen is None else unseen
    if unseen is None:
        return []
    seen_mono: dict[str, float] = _state.setdefault("mono_seen", {})
    candidates: list[tuple[str, str, str]] = []
    observed: set[str] = set()  # detection predicates, before sleep-safe age gates
    uncertain: set[str] = set()
    flagged = set(led.flags())
    pending = led.pending()  # one ledger read for every check below (none of them writes the waiting rows)
    for sid, since in led.unseen_since({s: n for s, n in unseen.items() if n > 0}, now).items():
        key = f"chat-closeout:unseen:{sid}:{int(since)}"
        if now - since >= core.STALE_S and _session_gone(sid):
            continue  # a deleted chat: no route can ever hand it these notices (checked only once stale: cheap idle)
        observed.add(key)
        first = seen_mono.setdefault(key, mono)
        if now - since < core.STALE_S or mono - first < core.STALE_S:
            continue
        msg = (f"Chat {sid} has had {unseen[sid]} kanban result(s) nobody handed to it for "
               f"{int((now - since) / 60)} min.")
        candidates.append((key, f"Kanban results stuck for chat {sid}", msg + " Check the "
                           "unloaded-session sweeper in gui.log and deliver the notices via an agent route."))
    for row in pending:
        sig, at = row.get("settled_sig"), row.get("settled_at")
        if not sig or not at or sig == row.get("done_sig"):
            continue
        key = f"chat-closeout:no-closeout:{row['session_id']}:{sig}"
        cards = read(row["batch"])
        if any(core.is_unknown(cards.get(t)) for t in row['batch']):
            uncertain.add(key)
            continue  # a failed inventory read cannot prove the incident cleared
        if row.get("closing") is None and core.closeout_key(row, cards) != sig:
            continue  # reported since, or the batch moved on (a woken turn still in flight stays watched)
        if now - float(at) >= core.STALE_S and _session_gone(row["session_id"]):
            continue  # positive deletion evidence wins even during a quota clock wait
        if _quota_wait(row, now):
            seen_mono.setdefault(key, mono)
            uncertain.add(key)
            continue  # paused, not cleared: retain the incident and its observation age
        observed.add(key)
        first = seen_mono.setdefault(key, mono)
        if now - float(at) < core.STALE_S or mono - first < core.STALE_S:
            continue
        msg = (f"Chat {row['session_id']}: its {len(row['batch'])} card(s) ({', '.join(row['batch'][:6])}) "
               f"settled {int((now - float(at)) / 60)} min ago and no close-out turn ran "
               f"(last wake: {row.get('wake_result') or 'none'}).")
        candidates.append((key, f"No close-out for chat {row['session_id']}", msg))
    for row in pending:
        since = row.get("failure_since")
        if since is None:
            continue
        sig = core.ask_relay_key(row, read(row["batch"]))
        if not sig:
            continue
        key = f"chat-closeout:ask-failed:{row['session_id']}:{int(since)}"
        if _quota_wait(row, now):
            seen_mono.setdefault(key, mono)
            uncertain.add(key)
            continue  # paused, not cleared
        observed.add(key)
        first = seen_mono.setdefault(key, mono)
        if now - since >= core.STALE_S and mono - first >= core.STALE_S:
            candidates.append((key, f"Ask relay stuck for chat {row['session_id']}",
                               f"Chat {row['session_id']}: ask relay has not been delivered for "
                               f"{int((now-since)/60)} min (last result: {row.get('wake_result')})."))
    # A row pending for a day with nothing found still working and something nobody can resolve (missing, or an
    # unknown/stale remote row): no close-out can ever run for it. One card per row episode (key = the persisted
    # first-seen time). Wall age >= STUCK_S plus STALE_S of this process's monotonic observation, so a lid sleep
    # alone never files it; a full day of uptime is not required (a backend restart would never let it fire).
    stuck: dict[str, list[str]] = {}
    for row in pending:
        cards = read(row["batch"])
        unknown = [t for t in row["batch"] if core.is_unresolved(cards.get(t))]
        working = [t for t in row["batch"] if core.is_working(cards.get(t))]
        if unknown and not working:
            stuck[row["session_id"]] = unknown
    for sid, since in led.stuck_since(stuck, now).items():
        key = f"chat-closeout:stuck:{sid}:{int(since)}"
        observed.add(key)
        first = seen_mono.setdefault(key, mono)
        if now - since < core.STUCK_S or mono - first < core.STALE_S:
            continue
        candidates.append((key, f"Chat {sid} waits on cards nothing can find",
                           f"Chat {sid} has waited {int((now - since) / 3600)} h on card(s) that no board or remote "
                           f"inventory resolves or settles ({', '.join(stuck[sid][:6])}: missing, stale, or done "
                           "without owner evidence such as an origin binding) and nothing it waits on is still "
                           "working, so no close-out can run. Check whether these cards moved, were deleted, "
                           "are on a gateway this one no longer syncs, or lack their owner's release; reconcile the "
                           "ledger row or restore the "
                           "link. Entries missing through 24 h of fresh inventory reads are unlinked automatically."))
    for handoff in routing.stalled(led, now, 0):
        key = f"chat-closeout:handoff:{handoff['id']}"
        observed.add(key)
        first = seen_mono.setdefault(key, mono)
        if now - handoff['created_at'] < core.STALE_S or mono - first < core.STALE_S:
            continue
        candidates.append((key, f"Ask handoff unconfirmed for chat {handoff['session_id']}",
                           f"Courier request {handoff['id']} for chat {handoff['session_id']} has no "
                           "successful delivery receipt. Inspect the target before retrying; do not resend "
                           "an uncertain send automatically."))
    failures: dict[str, int] = _state.setdefault("post_failures", {})
    for k in [k for k in seen_mono if k not in observed and k not in uncertain]:
        seen_mono.pop(k, None)
        failures.pop(k, None)
    for key in flagged:
        if key in uncertain or not _WATCHDOG_KEY.match(key):
            continue  # only this watchdog's own well-formed incidents are reconciled
        if key in observed:
            led.reset_resolution(key)
            continue
        try:
            evidence = _cleared_evidence(key, unseen, led)
            if evidence is None:
                led.reset_resolution(key)  # discard a prior, no-longer-valid positive observation
                continue
            resolution = led.resolution(key, now, evidence)
            if resolve_card(key, resolution):
                led.clear_flag(key)
        except Exception:
            logger.warning("chat-closeout: resolving watchdog incident %s failed; retrying next tick", key,
                           exc_info=True)
    candidates = [c for c in candidates if not led.has_flag(c[0]) and failures.get(c[0], 0) < MAX_POST_ATTEMPTS]
    if not candidates or (not force and not led.lease("watchdog", core.WATCHDOG_EVERY_S, now)):
        return []
    raised: list[str] = []
    for key, title, msg in candidates:
        logger.warning("chat-closeout watchdog: %s", msg)
        try:
            post(title, msg, key)
        except Exception:
            failures[key] = failures.get(key, 0) + 1
            if failures[key] >= MAX_POST_ATTEMPTS:
                logger.error("chat-closeout: filing the Kanban card for %s failed %d times; giving up until the "
                             "condition changes", key, failures[key], exc_info=True)
            else:
                logger.warning("chat-closeout: Kanban card filing failed for %s (retrying next run)", key,
                               exc_info=True)
            continue
        failures.pop(key, None)
        led.flag_once(key, now)
        raised.append(key)
    return raised


def _loop(stop: threading.Event) -> None:
    stop.wait(float(os.environ.get("CHAT_CLOSEOUT_FIRST_S", "20")))
    while not stop.is_set():
        if _server() is not None and not _off():
            for fn in (wake_tick, owner_wake_tick, watchdog_tick):
                try:
                    fn()
                except Exception:
                    logger.warning("chat-closeout %s failed", fn.__name__, exc_info=True)
        stop.wait(TICK_S)


def _stop_stale_threads() -> int:
    """A plugin reload re-executes this module with a fresh ``_state``, but the previous module's waker thread keeps
    running the OLD wake logic next to the new one. Stop every earlier chat-closeout thread in the process via its
    stop event (its first arg)."""
    stopped = 0
    for t in threading.enumerate():
        if t.name != "chat-closeout" or t is _state.get("thread") or not t.is_alive():
            continue
        ev = (getattr(t, "_args", None) or (None,))[0]
        if isinstance(ev, threading.Event):
            ev.set()
            stopped += 1
    if stopped:
        logger.info("chat-closeout: stopped %d waker thread(s) from a previous load", stopped)
    return stopped


def start_thread() -> Optional[threading.Event]:
    with _thread_lock:
        if _state.get("thread") or TICK_S <= 0:
            return None
        _stop_stale_threads()
        stop = threading.Event()
        t = threading.Thread(target=_loop, args=(stop,), daemon=True, name="chat-closeout")
        _state.update(thread=t, stop=stop)
        t.start()
        return stop


def register(ctx) -> None:
    logger.info("chat-closeout: loaded shepherd-settle-v7 module=%s core=%s", __file__, core.__file__)
    ctx.register_hook("post_llm_call", post_llm_call)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("pre_final_gate", pre_final_gate)
    if not os.environ.get("HERMES_KANBAN_TASK"):
        _register_waiting_source()
        start_thread()
