"""How many card slots each gateway's share of the token throttle pays for, and how many card starts the
subscription pacing governor is holding.

The Runners panel crosses out a gateway's slots beyond ``allowed``.

* **Token cost per slot** (``tokens_per_slot_per_s``): a gateway's in+out tokens over the last
  ``throttle.SHARE_WINDOW_S`` divided by its busy slot-seconds in the same window. Busy slot-seconds are card runs
  in the Board replicas (``task_runs``), attributed to the gateway named by the run's ``claimed`` event. A gateway
  with under ``MIN_BUSY_S`` busy slot-seconds has no cost of its own and takes the median of the gateways that do
  (``cost_source: "fleet_median"``); with none measured the cost is unknown and the throttle sets no slot limit.
* **by_throttle**: ``floor(share x cap / cost per slot)``, with the share ``throttle.local_share`` gives that
  gateway. None when no cap is set or the cost is unknown; 0 at a cap of zero.
* **allowed**: ``by_throttle`` (None = the throttle limits nothing here).
* **Pacing is not turned into slots.** Core's governor limits concurrent runs per provider pool per gateway
  (``kanban_llm_pacing.admit_limit``) from numbers only that gateway holds, and a card's pool is chosen at
  admission. An estimate from the pool factors alone said 2 slots per machine on 2026-11-17 while the workers
  ran about 10 cards each, so no per-machine pacing limit is reported. What is reported is what the Board
  replicas record: ``pacing.held_starts``, the cards whose start the governor held in the last
  ``PACING_WINDOW_S`` (their ``llm_paced`` events), fleet-wide.

The throttle route reads only a fresh snapshot. All optional reads run in one owned,
killable worker per root; cold, expired or incomplete measurements return null rather
than presenting missing observations as zero usage or measured slot limits.
"""
from __future__ import annotations

import math
import sqlite3
import statistics
import time
from pathlib import Path
from typing import Iterable, Optional

from . import cards as cards_mod
from . import throttle
from .sampler import ro_connect

MIN_BUSY_S = 300.0  # under five busy slot-minutes in the window a gateway's own cost per slot is noise
CLAIM_SLACK_S = 5.0  # a run's ``claimed`` event is written just before the run row
CACHE_S = 30.0
PACING_WINDOW_S = 300.0

from . import advisory


def busy_seconds(replicas: Iterable[Path], since: float, until: float, boot_map: Optional[dict] = None,
                 known: Optional[set] = None, *, strict: bool = False) -> dict[str, float]:
    """Busy slot-seconds per gateway in ``[since, until)``: every card run overlapping the window, clipped to it.

    A run with no end counts only while its card is still running on that run (a dangling open run row would
    otherwise count as busy for ever)."""
    out: dict[str, float] = {}
    for db in replicas:
        try:
            c = ro_connect(db)
        except sqlite3.Error:
            if strict:
                raise
            continue
        try:
            rows = c.execute(
                "SELECT r.started_at, r.ended_at, "
                "(SELECT e.payload FROM task_events e WHERE e.task_id = r.task_id AND e.kind = 'claimed' "
                " AND e.created_at <= r.started_at + ? ORDER BY e.created_at DESC, e.id DESC LIMIT 1) "
                "FROM task_runs r JOIN tasks t ON t.id = r.task_id "
                "WHERE r.started_at < ? AND (r.ended_at > ? OR (r.ended_at IS NULL AND t.status = 'running' "
                "AND t.current_run_id = r.id))", (CLAIM_SLACK_S, until, since)).fetchall()
        except sqlite3.Error:
            if strict:
                raise
            rows = []
        finally:
            c.close()
        for started, ended, payload in rows:
            holder, lock = cards_mod.parse_claim(payload)
            if strict and not holder and cards_mod.boot_uuid(lock) and cards_mod.boot_uuid(lock) not in (boot_map or {}):
                raise ValueError("boot attribution unavailable")
            gateway = cards_mod.attribute(holder, lock, boot_map or {}, known)
            if gateway in (cards_mod.UNATTRIBUTED, cards_mod.EPHEMERAL):
                continue
            span = min(until, float(ended) if ended is not None else until) - max(since, float(started))
            if span > 0:
                out[gateway] = out.get(gateway, 0.0) + span
    return out


def pacing_holds(replicas: Iterable[Path], since: float, until: float, *, strict: bool = False) -> int:
    """Cards whose start the pacing governor held in ``[since, until)`` (distinct cards with an ``llm_paced`` event)."""
    held = set()
    for db in replicas:
        try:
            c = ro_connect(db)
        except sqlite3.Error:
            if strict:
                raise
            continue
        try:
            held.update((str(db), r[0]) for r in c.execute(
                "SELECT DISTINCT task_id FROM task_events WHERE kind = 'llm_paced' AND created_at >= ? "
                "AND created_at < ?", (since, until)))
        except sqlite3.Error:
            if strict:
                raise
            pass
        finally:
            c.close()
    return len(held)


def plan(cap_per_s: Optional[float], usage: dict[str, float], busy: dict[str, float],
         window_s: float = throttle.SHARE_WINDOW_S) -> dict[str, dict]:
    """Per gateway: share, cost per slot and the slots the token throttle pays for (see the module docstring)."""
    names = sorted(set(usage) | set(busy))
    cost = {g: usage.get(g, 0.0) / busy[g] for g in names if busy.get(g, 0.0) >= MIN_BUSY_S}
    measured = [v for v in cost.values() if v > 0]
    fallback = statistics.median(measured) if measured else None
    out = {}
    for g in names:
        own = cost.get(g)
        per_slot, source = (own, "measured") if own else (fallback, "fleet_median" if fallback else None)
        share = throttle.local_share(usage, g)
        avg_busy = busy.get(g, 0.0) / window_s
        if cap_per_s is None:
            by_throttle = None
        elif cap_per_s <= 0:
            by_throttle = 0
        else:
            by_throttle = math.floor(share * cap_per_s / per_slot) if per_slot else None
        out[g] = {
            "share": round(share, 4), "cap_per_s": None if cap_per_s is None else round(share * cap_per_s, 2),
            "rate_per_s": round(usage.get(g, 0.0) / window_s, 2), "avg_busy": round(avg_busy, 2),
            "tokens_per_slot_per_s": None if per_slot is None else round(per_slot, 2), "cost_source": source,
            "by_throttle": by_throttle, "allowed": by_throttle,
        }
    return out


def collect(request: dict) -> dict:
    """Read a complete measurement in the disposable worker; errors are not zeros."""
    now = request["now"]
    until = int(now) // 60 * 60
    since = until - throttle.SHARE_WINDOW_S
    paths = request["replicas"]
    if paths is None:
        paths = cards_mod.replica_paths(Path(request["root"]))
    if not paths:
        raise ValueError("no replica observations")
    busy = busy_seconds(paths, since, until, request["boot_map"],
                        None if request.get("known") is None else set(request["known"]), strict=True)
    held = pacing_holds(paths, now - PACING_WINDOW_S, now + 1, strict=True)
    conn = ro_connect(Path(request["series_db"]))
    try:
        rows = conn.execute("SELECT gateway, SUM(value) FROM minutes WHERE metric='tokens_io' AND minute>=? AND minute<? "
                            "AND value IS NOT NULL GROUP BY gateway", (since, until)).fetchall()
    finally:
        conn.close()
    usage = {str(g): float(v) for g, v in rows}
    if not usage or set(busy) - set(usage):
        raise ValueError("missing usage observations")
    return {"observed_at": now, "window_since": since, "window_until": until,
            "usage": usage, "busy": busy, "held": held}


def payload(root: Path, series_db: Path, cap_per_s: Optional[float], cards=None,
            now: Optional[float] = None, replicas: Optional[Iterable[Path]] = None) -> Optional[dict]:
    """Return only a fresh advisory snapshot; cold/expired/failed data is unavailable."""
    explicit_now = now
    now = time.time() if now is None else now
    requested_at = time.monotonic()
    until = int(now) // 60 * 60

    def fresh(hit):
        return (hit is not None and 0 <= time.monotonic() - hit[0] < CACHE_S
                and 0 <= now - hit[1]["observed_at"] < CACHE_S and hit[1]["window_until"] == until)

    paths = None if replicas is None else [str(p) for p in replicas]

    def prepare(initial=False):
        attribution = cards.advisory_snapshot() if cards is not None else None
        return {"root": str(root), "series_db": str(series_db),
                "now": (time.time() if explicit_now is None else
                        now if initial else now + time.monotonic() - requested_at),
                "replicas": paths, "boot_map": (attribution or {}).get("boot_map", {}),
                "known": (attribution or {}).get("known")}

    hit = advisory.read(root, prepare(initial=True), fresh, prepare)
    if not fresh(hit):
        return None
    data = hit[1]
    return {"window_s": throttle.SHARE_WINDOW_S, "gateways": plan(cap_per_s, data["usage"], data["busy"]),
            "observed_at": data["observed_at"], "window_since": data["window_since"],
            "window_until": data["window_until"], "valid": True, "stale": False,
            "pacing": {"held_starts": data["held"], "window_s": PACING_WINDOW_S,
                       "window_since": data["observed_at"] - PACING_WINDOW_S,
                       "window_until": data["observed_at"] + 1}}


def stop():
    advisory.stop()
