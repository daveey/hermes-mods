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
* Enforcement is an ``llm_execution`` middleware (``Gate.middleware``) that paces model calls. Each gateway takes
  a share of the cap (``local_share``: a quarter split evenly between the gateways that sampled tokens in the
  last 15 minutes, the rest in proportion to what each used) and spaces its calls ``call_tokens / (share x cap)`` seconds
  apart, where ``call_tokens`` is this gateway's measured in+out tokens per model call (every call is counted,
  throttled or not, so the figure is ready when the slider moves). Calls on one gateway share
  ``throttle-pace.json`` (a file lock orders every Hermes process on the root): a call starts only once
  ``interval`` has passed since the last start (grant-at-start, ``Pacer.try_take``), so nothing is released in a
  herd. Nothing is booked while a call waits: the earlier reserve-before-wait kept the slots of waits that ended
  early and re-booked on every level change, so the queue ratcheted past the 600 s cap (waits of 763-966 s).
* Only card runs (``HERMES_KANBAN_TASK`` / ``HERMES_KANBAN_WORKER_TASK`` set) and cron calls (``platform`` cron)
  are throttled (``throttled``); an interactive chat's calls are counted but never wait. An earlier version held every call while the fleet rate was over the cap and released
  them all when it dipped under: 2026-11-17, cap 76k tokens/min, the fleet averaged 116k with bursts to 794k.
* Card starts follow the same cap: the dispatcher asks the ``kanban_start_gate`` hook before each claim
  (``Gate.start_gate``) and a card stays ready while the fleet's measured rate is over the cap, so worker slots
  are not filled with runs whose calls only queue. A core without that hook starts cards as before.
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
START_GATE_TTL_S = 15.0  # the dispatcher asks per candidate card, every tick: answer from a cached read
START_GATE_REASON = "token_throttle"
PACE_FILE = "throttle-pace.json"
EVEN_SHARE = 0.25  # this much of the cap is split evenly (so an idle gateway can start); the rest follows use
SHARE_WINDOW_S = 15 * 60  # shares and tokens-per-call are measured over the last 15 minutes
PLAN_TTL_S = 30.0  # a process re-reads its share and tokens-per-call this often
DEFAULT_CALL_TOKENS = 20_000.0  # before this gateway has measured one paced call
MIN_CALL_TOKENS, MAX_CALL_TOKENS = 500.0, 2_000_000.0
MAX_INTERVAL_S = 600.0  # a gateway with a sliver of the cap still gets one call every ten minutes
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


def stamp(value) -> float:
    """A ``set_at`` at millisecond precision. The hub link publishes floats rounded to 3 decimals, so stamps are
    stored, published and compared at that precision: a level and its acknowledgement then match on every
    gateway, including the one that set it."""
    number = _number(value)
    return 0.0 if number is None else round(number, 3)


def _record(data) -> Optional[dict]:
    """``{percent, cap_per_s, set_at}`` from a stored or published level; None when it is not one."""
    if not isinstance(data, dict) or _number(data.get("percent")) is None:
        return None
    limit = _number(data.get("cap_per_s"))
    return {"percent": clamp(data.get("percent")), "cap_per_s": None if limit is None else max(0.0, limit),
            "set_at": stamp(data.get("set_at"))}


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
                                   "set_at": stamp(time.time() if now is None else now)})
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
    if not isinstance(have, dict) or have.get("percent") != want["percent"] or stamp(have.get("set_at")) != want["set_at"]:
        _write_json(seen_path(root), {**want, "at": now})
    return level


def gateways(root: Path, hub_db: Optional[Path], now: float, me: Optional[str] = None) -> list[dict]:
    """Which running gateways have the current level: ``[{name, seen}]``, this gateway first. Another gateway runs
    when its link record is under ``LIVE_S`` old; it has the level when it acknowledged this ``set_at``."""
    records = {} if hub_db is None else published(hub_db)
    level = effective(root, hub_db, now, records)
    own = _read_json(seen_path(root))
    out = [{"name": me or "this gateway", "seen": isinstance(own, dict) and stamp(own.get("set_at")) == level["set_at"]}]
    for gw, entry in sorted(records.items()):
        if gw == me or now - entry["updated_at"] > LIVE_S:
            continue
        block = entry.get("throttle") or {}
        out.append({"name": gw, "seen": block.get("seen_set_at") is not None and stamp(block["seen_set_at"]) == level["set_at"]})
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


def pace_path(root: Path) -> Path:
    return Path(root) / "cache" / "throughput" / PACE_FILE


def local_name(series_db: Path) -> Optional[str]:
    """The name this gateway's sampler stores its own minutes under (``meta.me``); None before it has run."""
    if not Path(series_db).is_file():
        return None
    from .sampler import ro_connect
    try:
        c = ro_connect(series_db)
    except sqlite3.Error:
        return None
    try:
        row = c.execute("SELECT value FROM meta WHERE key='me'").fetchone()
    except sqlite3.Error:
        return None
    finally:
        c.close()
    return str(row[0]) if row and row[0] else None


def usage_by_gateway(series_db: Path, now: float) -> dict[str, float]:
    """In+out tokens each gateway used in the last ``SHARE_WINDOW_S`` (gateways with a sampled minute only)."""
    if not Path(series_db).is_file():
        return {}
    from .sampler import ro_connect
    until = int(now) // 60 * 60
    try:
        c = ro_connect(series_db)
    except sqlite3.Error:
        return {}
    try:
        rows = c.execute("SELECT gateway, SUM(value) FROM minutes WHERE metric='tokens_io' AND minute>=? AND minute<? "
                         "AND value IS NOT NULL GROUP BY gateway", (until - SHARE_WINDOW_S, until)).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        c.close()
    return {str(g): float(v or 0.0) for g, v in rows}


def local_share(usage: dict[str, float], me: Optional[str]) -> float:
    """This gateway's share of the cap, 0..1: ``EVEN_SHARE`` of it split evenly between the gateways in ``usage``
    (and this one), the rest in proportion to use (an even half left the fleet at half its cap on 2026-11-17: idle
    gateways held shares they did not use). Every gateway computes the same shares from the same series, so they sum to 1; a
    gateway that used nothing keeps its even half, so it can start."""
    names = set(usage) | ({me} if me else set())
    if not names:
        return 1.0
    even = 1.0 / len(names)
    total = sum(usage.values())
    mine = usage.get(me, 0.0) if me else 0.0
    return even if total <= 0 else EVEN_SHARE * even + (1 - EVEN_SHARE) * mine / total


class Pacer:
    """Slots for one gateway's model calls, shared by every Hermes process on the root through a locked file."""

    def __init__(self, root: Path):
        self.path = pace_path(root)

    def _locked(self, fn):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            state = _read_json(self.path)
            state = state if isinstance(state, dict) else {}
            before = json.dumps(state, sort_keys=True)
            out = fn(state)
            if json.dumps(state, sort_keys=True) != before:  # a waiter's poll changes nothing: no rewrite
                _write_json(self.path, state)
            return out
        finally:
            os.close(fd)  # releases the lock

    @staticmethod
    def _count(state: dict, at: float, now: float) -> None:
        """Count one call in the minute of ``at``; forget minutes older than the window."""
        floor = int(now - SHARE_WINDOW_S) // 60
        calls = {str(m): int(n) for m, n in (state.get("calls") or {}).items()
                 if str(m).lstrip("-").isdigit() and int(m) >= floor and _number(n) is not None}
        key = str(int(at) // 60)
        calls[key] = calls.get(key, 0) + 1
        state["calls"] = calls

    @staticmethod
    def clamp_stamps(state: dict, now: float, interval: float) -> None:
        """Bring stored stamps back to ``now`` when read: a ``last_start`` ahead of now (an odd clock, or a file
        from elsewhere), and a ``next_at`` an older process booked (reserve-before-wait; ours equals
        ``granted_next_at``) more than one interval ahead. So no stored state delays a call by more than one
        interval."""
        last = _number(state.get("last_start"))
        if last is not None and last > now:
            state["last_start"] = now
        booked = _number(state.get("next_at"))
        if booked is not None and booked != _number(state.get("granted_next_at")) and booked > now + max(0.0, interval):
            state["next_at"] = now

    @staticmethod
    def ready_at(state: dict, now: float, interval: float) -> float:
        """When the next call may start (after ``clamp_stamps``): ``interval`` after the last call that started
        (now when none has), and not before an older process's booked ``next_at``."""
        last = _number(state.get("last_start"))
        ready = now if last is None else last + max(0.0, interval)
        booked = _number(state.get("next_at"))
        if booked is not None and booked != _number(state.get("granted_next_at")):
            ready = max(ready, booked)
        return ready

    def try_take(self, now: float, interval: float) -> float:
        """Start a call now if ``interval`` has passed since the last start: 0.0 (granted; the start is stamped and
        counted in the minute of ``now``), else the seconds left. Nothing is booked while a call waits, so a waiter
        that gives up, fails or dies leaves no slot behind, and a shorter interval applies at once.
        ``next_at`` is still written for an older process sharing the file: it books its slot after this start."""
        def take(state):
            self.clamp_stamps(state, now, interval)
            ready = self.ready_at(state, now, interval)
            if now < ready:
                return ready - now
            state["last_start"] = now
            state["next_at"] = state["granted_next_at"] = now + max(0.0, interval)
            self._count(state, now, now)
            return 0.0
        return self._locked(take)

    def note(self, now: float) -> None:
        """Count a call that needed no slot (the throttle is off), so tokens-per-call is already known when the
        throttle is engaged."""
        self._locked(lambda state: self._count(state, now, now))

    def passes(self, now: float) -> int:
        """Calls counted in the minutes of the last ``SHARE_WINDOW_S`` up to now (reads without the lock)."""
        state = _read_json(self.path)
        calls = state.get("calls") if isinstance(state, dict) else None
        if not isinstance(calls, dict):
            return 0
        first, last = int(now - SHARE_WINDOW_S) // 60, int(now) // 60
        return sum(int(n) for m, n in calls.items()
                   if str(m).lstrip("-").isdigit() and first <= int(m) <= last and _number(n) is not None)


def call_tokens(local_usage: float, passes: int) -> float:
    """Measured in+out tokens per model call on this gateway; a default until both numbers exist."""
    if local_usage <= 0 or passes <= 0:
        return DEFAULT_CALL_TOKENS
    return min(MAX_CALL_TOKENS, max(MIN_CALL_TOKENS, local_usage / passes))


class Gate:
    """The per-process pacer: ``wait()`` returns when this gateway's next slot comes up."""

    def __init__(self, root: Path, series_db: Path, *, hub_db: Optional[Path] = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time,
                 beat: Optional[Callable[[float], None]] = None):
        self.root, self.series_db, self._sleep, self._clock = Path(root), Path(series_db), sleep, clock
        self.hub_db, self._beat = (None if hub_db is None else Path(hub_db)), beat
        self._lock = threading.Lock()
        self._pacer = Pacer(self.root)
        self._rate: Optional[tuple[float, float, float]] = None  # (taken at, share, tokens per call)
        self._start: Optional[tuple[float, bool]] = None  # (taken at, holding card starts)

    def _share_and_tokens(self, now: float) -> tuple[float, float]:
        with self._lock:
            if self._rate is not None and 0 <= now - self._rate[0] < PLAN_TTL_S:
                return self._rate[1], self._rate[2]
        me = local_name(self.series_db)
        usage = usage_by_gateway(self.series_db, now)
        share = local_share(usage, me)
        tokens = call_tokens(usage.get(me, 0.0) if me else 0.0, self._pacer.passes(now))
        with self._lock:
            self._rate = (now, share, tokens)
        return share, tokens

    def plan(self, now: float) -> tuple:
        """``("pass",)``, ``("hold",)`` (100 %, or a cap of zero) or ``("pace", seconds between calls, set_at)``.
        Off and fully on read only the level; a level in between reads the series. Any failed read passes."""
        try:
            level = effective(self.root, self.hub_db, now)
            percent = level["percent"]
            if percent <= 0:
                return ("pass",)
            if percent >= 100:
                return ("hold",)
            limit = limit_of(level, None if level.get("cap_per_s") is not None else reference_rate(self.series_db, now))
            if limit is None:
                return ("pass",)  # nothing to scale against yet
            share, tokens = self._share_and_tokens(now)
            allowed = share * limit
            if allowed <= 0:
                return ("hold",)
            return ("pace", min(MAX_INTERVAL_S, tokens / allowed), level["set_at"])
        except Exception:  # fail open: a broken read must never stall a model call
            log.debug("throughput throttle: plan failed", exc_info=True)
            return ("pass",)

    def holding(self) -> bool:
        """True while every call waits (100 %); a paced level is not a hold."""
        return self.plan(self._clock())[0] == "hold"

    def wait(self, *, beat: Optional[Callable[[float], None]] = None,
             stop: Optional[Callable[[], bool]] = None, max_s: Optional[float] = None) -> float:
        """Block until this call may start; returns the seconds waited. ``beat(waited)`` runs every poll (the
        caller's liveness report), ``stop()`` true or ``max_s`` reached ends the wait early. A waiting call re-reads
        the level every poll: lowering it releases the call. Grant-at-start: a paced call asks ``Pacer.try_take``
        each poll and is stamped only when it starts, so a wait holds no slot and a level change books nothing."""
        started = self._clock()
        announced = False
        while True:
            now = self._clock()
            plan = self.plan(now)
            if plan[0] == "pass":
                try:
                    self._pacer.note(now)  # it starts now: counted, so tokens-per-call stays measured
                except Exception:  # counting is best effort
                    log.debug("throughput throttle: call count failed", exc_info=True)
                break
            if plan[0] == "pace":
                try:
                    left = self._pacer.try_take(now, plan[1])
                except Exception:  # fail open: an unwritable pace file must never stall a model call
                    log.debug("throughput throttle: pace file unusable", exc_info=True)
                    break
                if left <= 0:
                    break
                nap = min(POLL_S, left)
            else:
                left, nap = None, POLL_S
            if not announced:
                announced = True
                log.info("throughput throttle: holding a model call (%s)",
                         "level 100%" if left is None else "next start in %.0fs" % left)
            for report in (beat, self._beat):
                if report is not None:
                    try:
                        report(now - started)  # tell the host this turn is waiting, not dead
                    except Exception:  # a liveness report must never fail the call it protects
                        log.debug("throughput throttle: liveness report failed", exc_info=True)
            self._sleep(nap)
            waited = self._clock() - started
            if (max_s is not None and waited >= max_s) or (stop is not None and _true(stop)):
                log.info("throughput throttle: wait ended after %.0fs before its start", waited)
                return waited
        waited = self._clock() - started
        if announced:
            log.info("throughput throttle: released after %.0fs", waited)
        return waited

    def start_gate(self, **_card) -> Optional[dict]:
        """``kanban_start_gate``: hold a card start while the fleet's token rate is over the cap (or the level is
        100 %). Pacing slows the model calls of running cards; without this the dispatcher keeps starting cards
        whose calls then only queue, filling worker slots with runs that wait. Cached for ``START_GATE_TTL_S``;
        any failed read starts the card."""
        now = self._clock()
        with self._lock:
            hit = self._start
        if hit is None or not 0 <= now - hit[0] < START_GATE_TTL_S:
            try:
                held = bool(status(self.root, self.series_db, now, self.hub_db)["holding"])
            except Exception:  # fail open: a broken read must never stop the dispatcher starting cards
                log.debug("throughput throttle: start gate read failed", exc_info=True)
                held = False
            hit = (now, held)
            with self._lock:
                self._start = hit
        return {"hold": True, "reason": START_GATE_REASON} if hit[1] else None

    def _exempt(self, platform) -> bool:
        """A chat call is never held: it is counted (so it still shows in tokens-per-call) and goes ahead without
        reading or stamping the pace. Only card and cron calls are throttled."""
        if throttled(platform):
            return False
        try:
            self._pacer.note(self._clock())
        except Exception:  # counting is best effort
            log.debug("throughput throttle: call count failed", exc_info=True)
        return True

    def middleware(self, *, next_call, request, **context):
        if self._exempt(context.get("platform")):
            return next_call(request)
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

    def aux_hold(self, aux_task=None, platform=None, **_payload) -> None:
        """``pre_auxiliary_call``: a card's or cron's auxiliary call waits too, for at most ``AUX_HOLD_MAX_S``.
        ``platform`` is core's parent-turn platform (empty for a call outside a turn), so outside a turn only the
        card environment decides."""
        if str(aux_task or "") in AUX_NOT_HELD or self._exempt(platform):
            return
        self.wait(beat=_card_heartbeat, max_s=AUX_HOLD_MAX_S)

    aux_hold.hermes_hook_timeout = AUX_HOOK_TIMEOUT_S


CARD_ENV = ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK")


def throttled(platform, env=None) -> bool:
    """Whether a model call is throttled: a Kanban card run (either card variable set) or a cron job
    (``platform == "cron"``). Everything else is an interactive chat, which the throttle never holds."""
    env = os.environ if env is None else env
    return any(env.get(name) for name in CARD_ENV) or str(platform or "") == "cron"


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
