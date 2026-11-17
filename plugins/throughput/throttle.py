"""Token throttle: one 0-100 % level for the whole fleet that caps model calls by the fleet's in+out token rate.

* ``percent`` 0 = no cap, 100 = a cap of zero (every model call waits), between = ``(1 - percent/100) x reference``
  tokens/s. ``reference`` is the peak fleet in+out rate over the last 24 hours (15-minute buckets) on the gateway
  that takes the setting; the resulting cap is stored in tokens/s with the level, so every gateway enforces the
  same number and the cap does not drift as the peak moves.
* The level is written to ``<hermes root>/cache/throughput/throttle.json`` on the gateway whose panel set it
  (``PUT /v1/throttle``). The hermes-hub link publishes that file in the gateway's own ``gateway/info`` record and
  every gateway keeps a replica of the others' records (``~/.local/state/hermes-hub/hub.db``), so each gateway
  reads every published level and obeys the newest one (``effective``). It arrives within the link's sync period
  (about a minute each way). A gateway without the link, or without this plugin, is not throttled.
* Each gateway acknowledges the level it obeys in ``throttle-seen.json`` (``note_seen``, once a minute from the
  sampler); the link publishes that too, so the panel can list which gateways have the current level.
* Enforcement is an ``llm_execution`` middleware (``Gate.middleware``): while the fleet rate measured by this
  gateway's sampler is above the cap, a model call waits here and re-checks every ``POLL_S`` seconds. Rates are
  sampled once a minute, so the control lags by about a minute and works as a duty cycle around the cap, not a
  per-token limiter.
* A waiting turn stays alive: core gives middleware no activity API, so the gate finds the agent behind the
  call it wraps (``agent_of``) and stamps its activity clock every poll, the way core's own offline wait does.
  That also renews a Kanban card's claim. A stop request from the user ends the wait at the next poll.
* Auxiliary calls (titles, compression, vision ...) do not pass the execution middleware. In card runs and
  other short-lived agent processes they wait in the ``pre_auxiliary_call`` hook instead (``Gate.aux_hold``),
  which core bounds: a held auxiliary call goes ahead after ``AUX_HOLD_MAX_S`` at the latest, and core runs only
  one bounded callback per turn at a time, so parallel auxiliary calls of one turn pass while one waits. The
  hook is not registered in long-lived server processes (gateway, Desktop serve, dashboard): core fires it
  synchronously for async callers too, so a hold there would freeze the server's event loop, including the
  panel route that lowers the level. The dispatcher's admission judge (``AUX_NOT_HELD``) is never held.
* Fail open: an unreadable level, no cap, or no measured rate never holds a call. Only 100 % holds without a
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
SEEN_FILE = "throttle-seen.json"
FUTURE_SLACK_S = 300.0  # a published level stamped further ahead than this (a wrong clock) is ignored
LIVE_S = 1800.0  # a gateway whose link record is older than this is not counted as running (a full link
# sweep is every 3 minutes; an idle host republishes less often)
REFERENCE_WINDOW_S = 24 * 3600
REFERENCE_BUCKET_S = 900
RATE_MINUTES = 3  # the fleet rate is the mean of the sampled minutes among the last three
POLL_S = 5.0
STATE_TTL_S = 10.0
AUX_HOOK_TIMEOUT_S = 600.0  # core's ceiling for one hook callback (``hermes_hook_timeout``)
AUX_HOLD_MAX_S = 540.0  # return before that ceiling: a timed-out callback is suppressed for a minute
AUX_NOT_HELD = frozenset({"hub_admission"})
REFERENCE_TTL_S = 600.0  # the 24-hour peak moves slowly and its scan is the expensive read


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


def _number(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (
            float("inf"), float("-inf")):
        return None
    return float(value)


def _record(data) -> Optional[dict]:
    """``{percent, cap_per_s, set_at}`` from a stored or published level; None when it is not one."""
    if not isinstance(data, dict) or _number(data.get("percent")) is None:
        return None
    limit = _number(data.get("cap_per_s"))
    return {"percent": clamp(data.get("percent")), "cap_per_s": None if limit is None else max(0.0, limit),
            "set_at": _number(data.get("set_at")) or 0.0}


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")  # unique per concurrent writer
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def read_record(root: Path) -> dict:
    """The level this gateway stored itself; off (and older than any real setting) when it stored none."""
    return _record(_read_json(level_path(root))) or {"percent": 0.0, "cap_per_s": None, "set_at": 0.0}


def read_level(root: Path) -> float:
    return read_record(root)["percent"]


def write_level(root: Path, percent, now: Optional[float] = None, reference: Optional[float] = None) -> float:
    """Store ``percent`` and the cap it means against ``reference`` right now (None = no cap can be scaled yet)."""
    value = clamp(percent)
    _write_json(level_path(root), {"percent": value, "cap_per_s": cap(value, reference),
                                   "set_at": time.time() if now is None else now})
    return value


# --- the fleet's level: every gateway's published setting, newest wins ---------------------------------------

def hub_db_path(env=None) -> Path:
    """The hermes-hub link's local state (its replica of every other gateway's records)."""
    env = os.environ if env is None else env
    return Path(env.get("HERMES_HUB_STATE") or Path.home() / ".local" / "state" / "hermes-hub") / "hub.db"


def published(hub_db: Path) -> dict[str, dict]:
    """``{gateway: {"throttle": block or None, "updated_at": epoch s}}`` from the link's replica of each gateway's
    ``gateway/info`` record, without this gateway's own row (its level is the local file; the replica's copy of
    it can be older). ``{}`` when this host has no link, or its replica cannot be read: the gateway then obeys
    its own stored level alone."""
    if not Path(hub_db).is_file():
        return {}
    from .sampler import ro_connect  # mode=rw + query_only: never creates, never writes
    try:
        c = ro_connect(hub_db)
    except sqlite3.Error:
        return {}
    try:
        rows = c.execute("SELECT gw, data, updated_at FROM replica WHERE coll='gateway' AND key='info'").fetchall()
        try:
            own = c.execute("SELECT v FROM meta WHERE k='gateway'").fetchone()
        except sqlite3.Error:  # a replica without the link's meta table
            own = None
    except sqlite3.Error:
        return {}
    finally:
        c.close()
    out = {}
    for gw, data, updated_at in rows:
        if own is not None and gw == own[0]:
            continue
        try:
            info = json.loads(data or "null")
        except ValueError:
            continue
        block = info.get("throttle") if isinstance(info, dict) else None
        out[str(gw)] = {"throttle": block if isinstance(block, dict) else None,
                        "updated_at": (_number(updated_at) or 0.0) / 1000.0}
    return out


def effective(root: Path, hub_db: Optional[Path], now: float, records: Optional[dict] = None) -> dict:
    """The level this gateway obeys: the newest of its own stored level and every level another gateway published.
    ``source`` names the gateway that set it (None = this one). A level stamped in the future is ignored."""
    best = {**read_record(root), "source": None}
    if hub_db is None:
        return best
    for gw, entry in sorted((published(hub_db) if records is None else records).items()):
        rec = _record(entry.get("throttle"))
        if rec is not None and best["set_at"] < rec["set_at"] <= now + FUTURE_SLACK_S:
            best = {**rec, "source": gw}
    return best


def seen_path(root: Path) -> Path:
    return Path(root) / "cache" / "throughput" / SEEN_FILE


def note_seen(root: Path, hub_db: Optional[Path], now: Optional[float] = None) -> dict:
    """Record which level this gateway obeys now (for the link to publish). Rewrites the file only on a change."""
    now = time.time() if now is None else now
    level = effective(root, hub_db, now)
    want = {"percent": level["percent"], "set_at": level["set_at"]}
    have = _read_json(seen_path(root))
    if not isinstance(have, dict) or {k: have.get(k) for k in want} != want:
        _write_json(seen_path(root), {**want, "at": now})
    return level


def gateways(root: Path, hub_db: Optional[Path], now: float, me: Optional[str] = None) -> list[dict]:
    """Which running gateways have the current level: ``[{name, seen}]``, this gateway first. Another gateway runs
    when its link record is under ``LIVE_S`` old; it has the level when it acknowledged this ``set_at``."""
    records = {} if hub_db is None else published(hub_db)
    level = effective(root, hub_db, now, records)
    own = _read_json(seen_path(root))
    out = [{"name": me or "this gateway", "seen": isinstance(own, dict) and own.get("set_at") == level["set_at"]}]
    for gw, entry in sorted(records.items()):
        if gw == me or now - entry["updated_at"] > LIVE_S:
            continue
        block = entry.get("throttle") or {}
        out.append({"name": gw, "seen": _number(block.get("seen_set_at")) == level["set_at"]})
    return out


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
    """Peak fleet in+out tokens/s over the last 24 hours, in 15-minute buckets; None without any sampled minute.
    Cached per db for ``REFERENCE_TTL_S``: the scan reads a day of minutes and callers sit on the model-call path."""
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


def limit_of(level: dict, reference: Optional[float]) -> Optional[float]:
    """The cap in tokens/s a level means: the one stored with it, else (a level stored without one) the cap against
    this gateway's own reference."""
    percent = level["percent"]
    if 0 < percent < 100 and level.get("cap_per_s") is not None:
        return level["cap_per_s"]
    return cap(percent, reference)


def status(root: Path, series_db: Path, now: Optional[float] = None, hub_db: Optional[Path] = None) -> dict:
    now = time.time() if now is None else now
    level = effective(root, hub_db, now)
    percent = level["percent"]
    reference, rate = reference_rate(series_db, now), fleet_rate(series_db, now)
    limit = limit_of(level, reference)
    holding = percent >= 100 or (limit is not None and rate is not None and rate > limit)
    return {"percent": percent, "reference_per_s": reference, "cap_per_s": limit, "rate_per_s": rate,
            "holding": holding, "set_at": level["set_at"] or None, "set_by": level["source"]}


class Gate:
    """The per-process hold: ``wait()`` returns once the level allows a model call."""

    def __init__(self, root: Path, series_db: Path, *, hub_db: Optional[Path] = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time,
                 beat: Optional[Callable[[float], None]] = None):
        self.root, self.series_db, self._sleep, self._clock = Path(root), Path(series_db), sleep, clock
        self.hub_db, self._beat = (None if hub_db is None else Path(hub_db)), beat
        self._lock = threading.Lock()
        self._state: Optional[tuple[float, bool]] = None  # (taken at, holding)

    def holding(self) -> bool:
        now = self._clock()
        with self._lock:
            if self._state is not None and 0 <= now - self._state[0] < STATE_TTL_S:
                return self._state[1]
        try:
            percent = effective(self.root, self.hub_db, now)["percent"]
            # Off and fully on need no rate: only a level in between reads the series db.
            held = percent >= 100 or (percent > 0 and status(self.root, self.series_db, now, self.hub_db)["holding"])
        except Exception:  # fail open: a broken read must never stall a model call
            log.debug("throughput throttle: status read failed", exc_info=True)
            held = False
        with self._lock:
            self._state = (now, held)
        return held

    def wait(self, *, beat: Optional[Callable[[float], None]] = None,
             stop: Optional[Callable[[], bool]] = None, max_s: Optional[float] = None) -> float:
        """Block while the throttle holds; returns the seconds waited. ``beat(waited)`` runs every poll (the
        caller's liveness report), ``stop()`` true or ``max_s`` reached ends the wait early."""
        if not self.holding():
            return 0.0
        started = self._clock()
        log.info("throughput throttle: holding a model call")
        while True:
            for report in (beat, self._beat):
                if report is not None:
                    try:
                        report(self._clock() - started)  # tell the host this turn is waiting, not dead
                    except Exception:  # a liveness report must never fail the call it protects
                        log.debug("throughput throttle: liveness report failed", exc_info=True)
            self._sleep(POLL_S)
            waited = self._clock() - started
            with self._lock:
                self._state = None  # a waiting call re-reads every poll, so lowering the level releases it
            if not self.holding():
                log.info("throughput throttle: released after %.0fs", waited)
                return waited
            if (max_s is not None and waited >= max_s) or (stop is not None and _true(stop)):
                log.info("throughput throttle: wait ended after %.0fs while still holding", waited)
                return waited

    def middleware(self, *, next_call, request, **_context):
        try:
            agent = agent_of(next_call)
        except Exception:  # an odd callable: wait without liveness reports rather than skip the hold
            agent = None
        if agent is None:
            self.wait(beat=_card_heartbeat)
        else:
            self.wait(beat=lambda waited: agent._touch_activity(f"token throttle: waiting ({waited:.0f}s)"),
                      stop=lambda: bool(getattr(agent, "_interrupt_requested", False)))
        return next_call(request)

    def aux_hold(self, aux_task=None, **_payload) -> None:
        """``pre_auxiliary_call``: an auxiliary call waits too, for at most ``AUX_HOLD_MAX_S``."""
        if str(aux_task or "") not in AUX_NOT_HELD:
            self.wait(beat=_card_heartbeat, max_s=AUX_HOLD_MAX_S)

    aux_hold.hermes_hook_timeout = AUX_HOOK_TIMEOUT_S


def _true(check: Callable[[], bool]) -> bool:
    try:
        return bool(check())
    except Exception:
        return False


def _card_heartbeat(_waited: float) -> None:
    """Renew this process's Kanban claim, when it is a card run (core rate-limits it to once a minute)."""
    if not os.environ.get("HERMES_KANBAN_TASK"):
        return
    from tools.kanban_tools import heartbeat_current_worker_from_env
    heartbeat_current_worker_from_env()


def agent_of(call, depth: int = 4):
    """The agent whose model call ``call`` performs: the first object with ``_touch_activity`` reachable through
    the callable's closure cells, bound ``self``, ``functools.partial`` target or ``__wrapped__``. None when the
    call carries none (another core version, a test double): the wait then runs without liveness reports."""
    seen, queue = set(), [(call, 0)]
    while queue:
        obj, level = queue.pop(0)
        if obj is None or id(obj) in seen:
            continue
        seen.add(id(obj))
        if callable(getattr(obj, "_touch_activity", None)):
            return obj
        if level >= depth or not callable(obj):
            continue
        nxt = [getattr(obj, "__self__", None), getattr(obj, "func", None), getattr(obj, "__wrapped__", None)]
        for cell in getattr(obj, "__closure__", None) or ():
            try:
                nxt.append(cell.cell_contents)
            except ValueError:  # an empty cell
                continue
        queue.extend((n, level + 1) for n in nxt)
    return None
