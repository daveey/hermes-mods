"""How many card slots each gateway can use under the token throttle and the subscription pacing governor.

A worker with 16 slots can look idle while only two of them can start a card (2026-11-17: worker-a1 ran 2 of 16
under ``llm_pacing ... running=1 limit=1``). The Runners panel draws the difference: slots beyond ``allowed`` are
crossed out.

* **Token cost per slot** (``tokens_per_slot_per_s``): a gateway's in+out tokens over the last
  ``throttle.SHARE_WINDOW_S`` divided by its busy slot-seconds in the same window. Busy slot-seconds are card runs
  in the Board replicas (``task_runs``), attributed to the gateway named by the run's ``claimed`` event. A gateway
  with under ``MIN_BUSY_S`` busy slot-seconds has no cost of its own and takes the median of the gateways that do
  (``cost_source: "fleet_median"``); with none measured the cost is unknown and the throttle sets no slot limit.
* **by_throttle**: ``floor(share x cap / cost per slot)``, with the share ``throttle.local_share`` gives that
  gateway. None when no cap is set or the cost is unknown; 0 at a cap of zero.
* **by_pacing**: an estimate of core's ``kanban_llm_pacing.admit_limit`` summed over the governed pools that are
  not open: a paced pool allows ``max(1, floor(average busy slots x factor))``, a held pool 0. Core applies the
  factor to the gateway's average concurrency on that one provider, which only that gateway knows; this uses its
  average busy slots on all providers, so it can read high, never low by more than the one-run floor. None when
  no pool is paced or held.
* **allowed**: the smaller of the two that are known; None when neither limits anything.

Everything here is read-only and fails open: an unreadable replica contributes no busy time, and a gateway
without data gets no limit rather than a wrong one.
"""
from __future__ import annotations

import math
import sqlite3
import statistics
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

from . import cards as cards_mod
from . import throttle
from .sampler import ro_connect

MIN_BUSY_S = 300.0  # under five busy slot-minutes in the window a gateway's own cost per slot is noise
CLAIM_SLACK_S = 5.0  # a run's ``claimed`` event is written just before the run row
CACHE_S = 30.0
LIMITING = ("paced", "held")

_cache: dict = {}
_lock = threading.Lock()


def busy_seconds(replicas: Iterable[Path], since: float, until: float, boot_map: Optional[dict] = None,
                 known: Optional[set] = None) -> dict[str, float]:
    """Busy slot-seconds per gateway in ``[since, until)``: every card run overlapping the window, clipped to it.

    A run with no end counts only while its card is still running on that run (a dangling open run row would
    otherwise count as busy for ever)."""
    out: dict[str, float] = {}
    for db in replicas:
        try:
            c = ro_connect(db)
        except sqlite3.Error:
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
            rows = []
        finally:
            c.close()
        for started, ended, payload in rows:
            holder, lock = cards_mod.parse_claim(payload)
            gateway = cards_mod.attribute(holder, lock, boot_map or {}, known)
            if gateway in (cards_mod.UNATTRIBUTED, cards_mod.EPHEMERAL):
                continue
            span = min(until, float(ended) if ended is not None else until) - max(since, float(started))
            if span > 0:
                out[gateway] = out.get(gateway, 0.0) + span
    return out


def pacing_limit(avg_busy: float, pools: Iterable[dict]) -> Optional[int]:
    """Slots the pacing governor leaves this gateway, estimated; None when no pool is paced or held."""
    limit, limiting = 0, False
    for pool in pools or ():
        state = pool.get("pacing")
        if state not in LIMITING:
            continue
        limiting = True
        if state == "held":
            continue
        factor = pool.get("pacing_factor")
        factor = float(factor) if isinstance(factor, (int, float)) and math.isfinite(factor) and factor > 0 else 0.0
        limit += max(1, math.floor(max(0.0, avg_busy) * factor))
    return limit if limiting else None


def plan(cap_per_s: Optional[float], usage: dict[str, float], busy: dict[str, float], pools: Iterable[dict],
         window_s: float = throttle.SHARE_WINDOW_S) -> dict[str, dict]:
    """Per gateway: share, cost per slot and the slots each limit allows (see the module docstring)."""
    pools = list(pools or ())
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
        by_pacing = pacing_limit(avg_busy, pools)
        known = [n for n in (by_throttle, by_pacing) if n is not None]
        out[g] = {
            "share": round(share, 4), "cap_per_s": None if cap_per_s is None else round(share * cap_per_s, 2),
            "rate_per_s": round(usage.get(g, 0.0) / window_s, 2), "avg_busy": round(avg_busy, 2),
            "tokens_per_slot_per_s": None if per_slot is None else round(per_slot, 2), "cost_source": source,
            "by_throttle": by_throttle, "by_pacing": by_pacing, "allowed": min(known) if known else None,
        }
    return out


def payload(root: Path, series_db: Path, cap_per_s: Optional[float], pools: Iterable[dict], cards=None,
            now: Optional[float] = None, replicas: Optional[Iterable[Path]] = None) -> dict:
    """``{"window_s", "gateways": {name: plan row}}`` for the throttle API. Busy time is cached ``CACHE_S``."""
    now = time.time() if now is None else now
    until = int(now) // 60 * 60
    since = until - throttle.SHARE_WINDOW_S
    key = (str(root), until)
    with _lock:
        hit = _cache.get("busy")
        if hit and hit[0] == key and time.monotonic() - hit[1] < CACHE_S:
            busy = hit[2]
        else:
            boot_map, known = {}, None
            if cards is not None:
                try:
                    boot_map, known = cards.events().get("boot_map") or {}, cards.known()
                except Exception:  # advisory attribution only: never fail the throttle route
                    boot_map, known = {}, None
            paths = list(replicas) if replicas is not None else cards_mod.replica_paths(Path(root))
            busy = busy_seconds(paths, since, until, boot_map, known)
            _cache["busy"] = (key, time.monotonic(), busy)
    usage = throttle.usage_by_gateway(series_db, now)
    return {"window_s": throttle.SHARE_WINDOW_S, "gateways": plan(cap_per_s, usage, busy, pools)}
