"""Token throttle: a 0-100 % level that caps this Hermes root's model calls by the fleet's in+out token rate.

* ``percent`` 0 = no cap, 100 = a cap of zero (every model call waits), between = ``(1 - percent/100) x reference``
  tokens/s. ``reference`` is the peak fleet in+out rate over the last 7 days (15-minute buckets), so 50 % always
  means "half of the recent peak".
* The level lives in ``<hermes root>/cache/throughput/throttle.json`` (written by ``PUT /v1/throttle``). It applies
  to every Hermes process on that root. It is NOT distributed to other machines: the Board has no field for it.
* Enforcement is an ``llm_execution`` middleware (``Gate.middleware``): while the fleet rate measured by the sampler
  is above the cap, a main-loop model call waits here and re-checks every ``POLL_S`` seconds. Auxiliary calls that
  do not go through core's execution middleware (titles, compression ...) are not held. Rates are sampled once a minute, so the
  control lags by about a minute and works as a duty cycle around the cap, not a per-token limiter.
* Fail open: an unreadable level, no reference, or no measured rate never holds a call. Only 100 % holds without a
  rate.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("hermes.plugins.throughput")

LEVEL_FILE = "throttle.json"
REFERENCE_WINDOW_S = 7 * 24 * 3600
REFERENCE_BUCKET_S = 900
RATE_MINUTES = 3  # the fleet rate is the mean of the sampled minutes among the last three
POLL_S = 5.0
STATE_TTL_S = 10.0
REFERENCE_TTL_S = 600.0  # the 7-day peak moves slowly and its scan is the expensive read


def level_path(root: Path) -> Path:
    return Path(root) / "cache" / "throughput" / LEVEL_FILE


def clamp(percent) -> float:
    """A finite number clamped to 0..100; anything else is 0 (no throttle)."""
    try:
        value = float(percent)
    except (TypeError, ValueError):
        return 0.0
    if value != value:  # NaN
        return 0.0
    return min(100.0, max(0.0, value))


def read_level(root: Path) -> float:
    try:
        data = json.loads(level_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0.0
    return clamp(data.get("percent")) if isinstance(data, dict) else 0.0


def write_level(root: Path, percent, now: Optional[float] = None) -> float:
    value = clamp(percent)
    path = level_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")  # unique per concurrent writer
    tmp.write_text(json.dumps({"percent": value, "set_at": time.time() if now is None else now}), encoding="utf-8")
    os.replace(tmp, path)
    return value


def cap(percent: float, reference: Optional[float]) -> Optional[float]:
    """Max fleet tokens/s at this level: None = no cap (0 %, or no reference to scale), 0.0 at 100 %."""
    if percent <= 0:
        return None
    if percent >= 100:
        return 0.0
    return None if reference is None else (1 - percent / 100.0) * reference


def should_hold(percent: float, rate: Optional[float], reference: Optional[float]) -> bool:
    if percent >= 100:
        return True
    limit = cap(percent, reference)
    return limit is not None and rate is not None and rate > limit


def _fleet_minutes(series_db: Path, since: int, until: int) -> dict[int, float]:
    """Fleet in+out tokens per sampled minute in ``[since, until)`` (read-only; a missing db has no minutes)."""
    if not Path(series_db).is_file():
        return {}
    from .sampler import ro_connect  # mode=rw + query_only: also opens a WAL db whose -wal/-shm are gone
    try:
        c = ro_connect(series_db)
    except sqlite3.Error:
        return {}
    try:
        rows = c.execute("SELECT minute, SUM(value) FROM minutes WHERE metric='tokens_io' AND minute>=? AND minute<? "
                         "AND value IS NOT NULL GROUP BY minute", (since, until)).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        c.close()
    return {int(m): float(v) for m, v in rows}


_reference_cache: dict[str, tuple[float, Optional[float]]] = {}
_reference_lock = threading.Lock()


def reference_rate(series_db: Path, now: float) -> Optional[float]:
    """Peak fleet in+out tokens/s over the last 7 days, in 15-minute buckets; None without any sampled minute.
    Cached per db for ``REFERENCE_TTL_S``: the scan reads a week of minutes and callers sit on the model-call path."""
    key = str(series_db)
    with _reference_lock:
        hit = _reference_cache.get(key)
    if hit is not None and 0 <= now - hit[0] < REFERENCE_TTL_S:
        return hit[1]
    value = _reference_rate(series_db, now)
    with _reference_lock:
        _reference_cache[key] = (now, value)
    return value


def _reference_rate(series_db: Path, now: float) -> Optional[float]:
    until = int(now) // 60 * 60
    minutes = _fleet_minutes(series_db, until - REFERENCE_WINDOW_S, until)
    if not minutes:
        return None
    buckets: dict[int, float] = {}
    for minute, value in minutes.items():
        buckets[minute // REFERENCE_BUCKET_S] = buckets.get(minute // REFERENCE_BUCKET_S, 0.0) + value
    return max(buckets.values()) / REFERENCE_BUCKET_S


def fleet_rate(series_db: Path, now: float) -> Optional[float]:
    """Fleet in+out tokens/s now: the mean of the sampled minutes among the last ``RATE_MINUTES``; None if none."""
    until = int(now) // 60 * 60
    minutes = _fleet_minutes(series_db, until - RATE_MINUTES * 60, until)
    return sum(minutes.values()) / (len(minutes) * 60.0) if minutes else None


def status(root: Path, series_db: Path, now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    percent = read_level(root)
    reference, rate = reference_rate(series_db, now), fleet_rate(series_db, now)
    return {"percent": percent, "reference_per_s": reference, "cap_per_s": cap(percent, reference),
            "rate_per_s": rate, "holding": should_hold(percent, rate, reference)}


class Gate:
    """The per-process hold: ``wait()`` returns once the level allows a model call."""

    def __init__(self, root: Path, series_db: Path, *, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time):
        self.root, self.series_db, self._sleep, self._clock = Path(root), Path(series_db), sleep, clock
        self._lock = threading.Lock()
        self._state: Optional[tuple[float, bool]] = None  # (taken at, holding)

    def holding(self) -> bool:
        now = self._clock()
        with self._lock:
            if self._state is not None and 0 <= now - self._state[0] < STATE_TTL_S:
                return self._state[1]
        try:
            percent = read_level(self.root)
            # Off and fully on need no rate: only a level in between reads the series db.
            held = percent >= 100 or (percent > 0 and status(self.root, self.series_db, now)["holding"])
        except Exception:  # fail open: a broken read must never stall a model call
            log.debug("throughput throttle: status read failed", exc_info=True)
            held = False
        with self._lock:
            self._state = (now, held)
        return held

    def wait(self) -> float:
        """Block while the throttle holds; returns the seconds waited."""
        if not self.holding():
            return 0.0
        started = self._clock()
        log.info("throughput throttle: holding a model call (level %.0f%%)", read_level(self.root))
        while True:
            self._sleep(POLL_S)
            with self._lock:
                self._state = None  # a waiting call re-reads every poll, so lowering the level releases it
            if not self.holding():
                waited = self._clock() - started
                log.info("throughput throttle: released after %.0fs", waited)
                return waited

    def middleware(self, *, next_call, request, **_context):
        self.wait()
        return next_call(request)
