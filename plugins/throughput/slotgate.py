"""Slot gate: open only as many card slots on this gateway as its share of the token cap pays for.

The throttle (throttle.py) caps the fleet's tokens per second. Pacing model calls under that cap is not enough:
the dispatcher keeps starting cards while slots are free, so on 2026-11-20 41 cards ran on a 365 tokens/s budget
and 40 of them sat in "token throttle: waiting" for up to 70 minutes. This module turns the cap into a slot count.

* **Busy and held slot-seconds** (the sampler, once a minute, ``sample``): the gateway's running card count
  (core's ``machine_metrics.active_card_runs``) is stored as ``slot:busy`` for the minute, and the seconds its
  cards' model calls were held by the throttle (``Pacer.note_wait``, per card and minute, at most 60) as
  ``slot:wait``. Metric names with a colon stay out of the throughput charts.
* **Tokens per slot** (``raw_estimate``): this gateway's in+out tokens over the last ``WINDOW_S`` divided by its
  busy slot-seconds minus held ones. Held time is left out on purpose: a throttled slot burns few tokens, and
  counting it would make slots look cheap, which would let the gate open as many slots as are already running,
  so the count could never come down. Under ``MIN_ACTIVE_S`` of unheld slot time there is no new measurement.
* **Running estimate** (``update``): an exponential average of those measurements with a ``HALF_LIFE_S``
  half-life, stored in ``cache/throughput/slot-estimate.json``. An estimate older than ``EXPIRE_S`` is dropped.
* **Allowed slots** (``allowance``): ``floor(share x cap / tokens per slot)``, at least 1 while the cap is above
  0. ``share`` is the same share of the cap this gateway's model calls are paced at (``throttle.local_share``),
  so the slots it opens are the ones its share pays for, and the shares of all gateways sum to the fleet cap.
  No cap: no limit. A cap of 0: no slots. A gateway that has never measured itself opens ``UNMEASURED_SLOTS``.
* **Fleet limit file** (``write_fleet_limit``, the sampler once a minute): the cap in force, the fleet token rate and
  whether the throttle is holding, written to ``cache/throughput/fleet-limit.json`` for the hub's worker autoscaler
  (private state repo, ``hermes_worker_scaler.py``). The autoscaler turns it into a fleet-wide slot allowance with
  the pool's running card counts, so it launches a machine only when the token budget can feed one.
* **The gate** (``Gate.start_gate`` in throttle.py) holds a card start while this gateway's running cards plus
  the starts it let through since its last read reach the allowed count. Running cards are never stopped: the
  count comes down as they finish. Chats never take a slot.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Optional

from . import throttle

ESTIMATE_FILE = "slot-estimate.json"
FLEET_FILE = "fleet-limit.json"
BUSY_METRIC, WAIT_METRIC = "slot:busy", "slot:wait"
WINDOW_S = 15 * 60  # the measurement window (the throttle's share window)
MIN_ACTIVE_S = 300.0  # under five unheld slot-minutes in the window a measurement is noise
HALF_LIFE_S = 3600.0  # the running estimate's half-life
EXPIRE_S = 6 * 3600.0  # an estimate not refreshed for this long is dropped (the work mix has changed)
UNMEASURED_SLOTS = 2  # a gateway with no estimate of its own (new, or idle for hours) starts this many cards
WAIT_BACKFILL_MIN = 3  # each sample rewrites the held seconds of the last few complete minutes


def estimate_path(root: Path) -> Path:
    return Path(root) / "cache" / "throughput" / ESTIMATE_FILE


def active_runs() -> Optional[int]:
    """This machine's running card count, as core's dispatcher counts it; None outside a Hermes runtime or on a
    failed read."""
    try:
        from hermes_cli.machine_metrics import active_card_runs
        value = active_card_runs()
    except Exception:
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def window_totals(series_db: Path, me: str, now: float) -> Optional[tuple[float, float, float]]:
    """``(tokens, busy slot-seconds, held slot-seconds)`` of gateway ``me`` over the last ``WINDOW_S`` complete
    minutes; None when the series cannot be read."""
    if not Path(series_db).is_file():
        return None
    import sqlite3
    from .sampler import ro_connect
    until = int(now) // 60 * 60
    since = until - WINDOW_S
    try:
        c = ro_connect(series_db)
    except sqlite3.Error:
        return None
    try:
        rows = dict(c.execute(
            "SELECT metric, SUM(value) FROM minutes WHERE gateway=? AND metric IN ('tokens_io', ?, ?) AND minute>=? "
            "AND minute<? AND value IS NOT NULL GROUP BY metric", (me, BUSY_METRIC, WAIT_METRIC, since, until)))
    except sqlite3.Error:
        return None
    finally:
        c.close()
    return (float(rows.get("tokens_io") or 0.0), float(rows.get(BUSY_METRIC) or 0.0) * 60.0,
            float(rows.get(WAIT_METRIC) or 0.0))


def raw_estimate(tokens: float, busy_s: float, held_s: float) -> Optional[float]:
    """Tokens per second of one unheld card slot; None under ``MIN_ACTIVE_S`` of unheld slot time."""
    active = busy_s - min(held_s, busy_s)
    if active < MIN_ACTIVE_S or tokens <= 0:
        return None
    return tokens / active


def read_estimate(root: Path, now: float) -> Optional[dict]:
    """``{per_s, at, raw, active_s}`` when a fresh estimate is stored; None when none is, or it expired."""
    try:
        data = json.loads(estimate_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    per_s, at = throttle._number(data.get("per_s")), throttle._number(data.get("at"))
    if per_s is None or per_s <= 0 or at is None or not 0 <= now - at <= EXPIRE_S:
        return None
    return {**data, "per_s": per_s, "at": at}


def blend(old: Optional[float], old_at: Optional[float], new: float, now: float) -> float:
    """The running estimate after a measurement ``new`` at ``now``: an exponential average with half-life
    ``HALF_LIFE_S`` (the first measurement is taken as is)."""
    if old is None or old_at is None:
        return new
    weight = 1.0 - 0.5 ** (max(0.0, now - old_at) / HALF_LIFE_S)
    return old + (new - old) * weight


def update(root: Path, series_db: Path, me: str, now: float) -> Optional[dict]:
    """Fold this minute's measurement into the stored estimate; returns the estimate in force (None if none)."""
    totals = window_totals(series_db, me, now)
    current = read_estimate(root, now)
    raw = None if totals is None else raw_estimate(*totals)
    if raw is None:
        return current
    per_s = blend(None if current is None else current["per_s"], None if current is None else current["at"],
                  raw, now)
    out = {"per_s": per_s, "at": now, "raw": raw, "active_s": totals[1] - min(totals[2], totals[1]),
           "tokens": totals[0], "busy_s": totals[1], "held_s": totals[2]}
    throttle._write_json(estimate_path(root), out)
    return out


def sample(root: Path, store, me: str, now: float, pacer: Optional["throttle.Pacer"] = None,
           running: Optional[int] = None) -> Optional[dict]:
    """The sampler's once-a-minute step: store this minute's busy slots and the last minutes' held seconds, then
    update the estimate. ``running`` None reads core's count; without a count nothing is stored."""
    minute = int(now) // 60 * 60
    busy = active_runs() if running is None else running
    if busy is None:
        return None  # no card count here (not a Hermes runtime, or core could not count): nothing to measure
    store.put(me, BUSY_METRIC, [(minute, float(busy))])
    pacer = throttle.Pacer(root) if pacer is None else pacer
    store.put(me, WAIT_METRIC, [(m, pacer.waited(m)) for m in
                                 (minute - 60 * k for k in range(1, WAIT_BACKFILL_MIN + 1))])
    return update(root, store.path, me, now)


def allowance(cap_per_s: Optional[float], share: float, per_slot: Optional[float]) -> Optional[int]:
    """Card slots this gateway may run: None = no limit (no cap); 0 at a cap of 0; ``UNMEASURED_SLOTS`` without an
    estimate; else ``floor(share x cap / per_slot)``, at least 1."""
    if cap_per_s is None:
        return None
    if cap_per_s <= 0:
        return 0
    if per_slot is None or per_slot <= 0:
        return UNMEASURED_SLOTS
    return max(1, math.floor(share * cap_per_s / per_slot))


def local(root: Path, series_db: Path, cap_per_s: Optional[float], now: Optional[float] = None) -> dict:
    """This gateway's slot limit: ``{allowed, share, per_slot_per_s, source, estimate_at}`` (``source`` is
    ``measured`` or ``unmeasured``)."""
    now = time.time() if now is None else now
    me = throttle.local_name(series_db)
    share = throttle.local_share(throttle.usage_by_gateway(series_db, now), me)
    est = read_estimate(root, now)
    per_slot = None if est is None else est["per_s"]
    return {"allowed": allowance(cap_per_s, share, per_slot), "share": round(share, 4),
            "per_slot_per_s": None if per_slot is None else round(per_slot, 3),
            "source": "measured" if per_slot is not None else "unmeasured",
            "estimate_at": None if est is None else est["at"], "cap_per_s": cap_per_s}


def fleet_limit_path(root: Path) -> Path:
    return Path(root) / "cache" / "throughput" / FLEET_FILE


def write_fleet_limit(root: Path, series_db: Path, hub_db: Optional[Path], now: Optional[float] = None) -> dict:
    """Write the throttle facts the worker autoscaler reads: ``{version, at, cap_per_s, rate_per_s, holding, mode,
    pacing}`` (``cap_per_s`` None = no cap; ``pacing`` is ``status()["pacing"]["state"]``). Returns what it wrote."""
    now = time.time() if now is None else now
    st = throttle.status(root, series_db, now, hub_db)
    out = {"version": 1, "at": now, "cap_per_s": st["cap_per_s"], "rate_per_s": st["rate_per_s"],
           "holding": bool(st["holding"]), "mode": st["mode"], "pacing": (st.get("pacing") or {}).get("state")}
    throttle._write_json(fleet_limit_path(root), out)
    return out
