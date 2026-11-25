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
* Budget mode (``mode: "auto"``): on the host whose core config sets ``kanban.llm_pacing.budget_publish`` the
  sampler turns core's capacity model (``llm_pacing.json`` ``v2``) into the level once a minute
  (``publish_budget``): ``cap_per_s`` is the fleet budget (``global_rate_tokens_per_s``) and ``pool_caps`` each
  provider pool's (0 at reserve, None while its rate is unknown), stamped ``budget_at`` = ``v2.computed_at``. The
  slider's ``percent`` stays the user's and scales every budget cap: ``budget x (1 - percent/100)``. Model calls
  are paced per pool (``Gate.plan`` with the call's ``provider``): a pool cap of 0 holds, None or a provider without
  a pool cap passes. Card starts hold while the governed pools' rate is over the sum of their positive scaled caps
  (``governed_load``); the slider keeps a budget level a budget level (``set_level``). A budget older than
  ``BUDGET_STALE_S`` is stale (caps kept), older than ``BUDGET_EXPIRE_S`` expired: no cap from it (fail open). A
  level without ``mode`` is the slider level above, unchanged; ``mode: "manual"`` is a person's cap (``write_cap``)
  or, without ``pool_caps``, budget pacing switched off (below).
* Burst: a budget level may carry ``burst: {factor, until, set_at}`` (``1 < factor <= 4``, ``until`` at most 24 h
  after ``set_at``). While ``now < until`` every budget cap (global and each positive pool cap) is multiplied by
  ``factor`` on top of the slider's scaling; a pool cap of 0 (reserve) stays 0 and 100 % still holds everything.
  It rides on the level (``set_burst`` / ``clear_burst``, newest wins like the slider); ``set_level`` and the
  publisher keep it, a manual ``write_level`` drops it. An expired or malformed burst, or one on a manual level, is
  ignored everywhere: expiry needs no action.
* Budget pacing switch (``set_pacing``, the panel's On/Off): a ``mode: "manual"`` level without ``pool_caps`` means a
  person switched budget pacing off (``pacing_off``): the publisher writes nothing while it is the level in force,
  and it is otherwise an ordinary manual level (``cap_per_s`` = ``cap(percent, reference)``, no pool caps, no burst).
  A person's cap (``write_cap``) set while pacing is not off carries ``pool_caps`` (the scaled budget pools, or ``{}``)
  so the publisher still folds it in; set while pacing is off it carries none and pacing stays off. The hub link
  carries only ``percent, cap_per_s, set_at, mode, budget_at, pool_caps, burst``, so the marker has to be one of them.
  Turning it on writes a level without ``mode``, which the publisher folds into a budget level on its next pass.
  ``status()["pacing"]`` = ``{state: on|off|pending|unavailable, publisher, budget_seen}`` (``pacing_status``).
"""
from __future__ import annotations

import json
import logging
import os
import re
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
SLOT_GATE_REASON = "token_slots"  # held: this gateway runs every slot its share of the cap pays for
PACE_FILE = "throttle-pace.json"
EVEN_SHARE = 0.25  # this much of the cap is split evenly (so an idle gateway can start); the rest follows use
SHARE_WINDOW_S = 15 * 60  # shares and tokens-per-call are measured over the last 15 minutes
PLAN_TTL_S = 30.0  # a process re-reads its share and tokens-per-call this often
WAIT_KEEP_S = 3600.0  # held slot-seconds per card and minute are kept this long in the pace file
MANUAL = "manual"  # a level whose ``cap_per_s`` is a person's absolute cap (``write_cap``), not a slider's
USER_CAP_FILE = "user-cap.json"  # the budget-publish host's record of the newest person-set cap
DEFAULT_CALL_TOKENS = 20_000.0  # before this gateway has measured one paced call
MIN_CALL_TOKENS, MAX_CALL_TOKENS = 500.0, 2_000_000.0
MAX_INTERVAL_S = 600.0  # a gateway with a sliver of the cap still gets one call every ten minutes
AUX_HOOK_TIMEOUT_S = 600.0  # core's ceiling for one hook callback (``hermes_hook_timeout``)
AUX_HOLD_MAX_S = 540.0  # return before that ceiling: a timed-out callback is suppressed for a minute
AUX_NOT_HELD = frozenset({"hub_admission"})
REFERENCE_TTL_S = 600.0  # the 24-hour peak moves slowly and its scan is the expensive read
AUTO = "auto"  # a level whose caps are the published budget (``publish_budget``); anything else is the slider's
BUDGET_STALE_S = 20 * 60.0  # a budget computed longer ago than this is stale: its caps are kept, flagged
BUDGET_EXPIRE_S = 60 * 60.0  # ... and longer ago than this, expired: no cap from it (fail open)
BUDGET_REWRITE_S = 15 * 60.0  # the publisher rewrites an unchanged budget this often (under BUDGET_STALE_S)
BUDGET_MOVE = 0.05  # ... and at once when any cap moves by more than this fraction
BURST_MAX_FACTOR = 4.0  # a burst multiplies the budget caps by 1 < factor <= this
BURST_MAX_S = 24 * 3600.0  # ... for at most this long after it was set
PROVIDER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")  # a pool key: the hub client's rule too
PENDING_S = 180.0  # a level set to be folded in reads "pending" this long after it was set ...
PENDING_MAX_S = 600.0  # ... and up to this long while a budget was seen (link lag: up to ~4 min each way)
BUDGET_SEEN_S = 24 * 3600.0  # a budget level (or an Off) this recent means the fleet has a budget publisher


def level_path(root: Path) -> Path:
    return Path(root) / "cache" / "throughput" / LEVEL_FILE


def on_request_path(root: Path) -> Path:
    """Local only (never published): when this gateway last switched budget pacing on (``set_pacing(on=True)``)."""
    return Path(root) / "cache" / "throughput" / "pacing-on-request.json"


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


def provider_name(key) -> bool:
    """A pool key the throttle accepts: a short plain provider name (``anthropic``, ``openai-codex``)."""
    return isinstance(key, str) and PROVIDER_RE.fullmatch(key) is not None


def pool_caps_of(raw) -> dict[str, Optional[float]]:
    """``{provider: tokens/s or None}`` from a stored or published ``pool_caps``: entries with a bad key, or a value
    that is neither None nor a finite number >= 0, are dropped; anything but a map is ``{}``."""
    out: dict[str, Optional[float]] = {}
    for key, value in (raw.items() if isinstance(raw, dict) else ()):
        number = _number(value)
        if not provider_name(key) or (value is not None and (number is None or number < 0)):
            continue
        out[key] = number
    return out


def burst_of(raw) -> Optional[dict]:
    """``{factor, until, set_at}`` from a stored or published ``burst``; None unless ``factor`` is a number with
    ``1 < factor <= BURST_MAX_FACTOR`` and ``until`` lies after ``set_at`` by at most ``BURST_MAX_S``."""
    if not isinstance(raw, dict):
        return None
    factor, until, set_at = (_number(raw.get(k)) for k in ("factor", "until", "set_at"))
    if factor is None or until is None or set_at is None or not 1.0 < factor <= BURST_MAX_FACTOR:
        return None
    if not 0 < until - set_at <= BURST_MAX_S + 0.001:  # stamps round to 1 ms
        return None
    return {"factor": factor, "until": round(until, 3), "set_at": round(set_at, 3)}


def burst_active(level: dict, now: float) -> Optional[dict]:
    """The burst a level applies right now: a budget level's valid burst while ``now < until <= now +
    BURST_MAX_S``; else None."""
    burst = level.get("burst") if level.get("mode") == AUTO else None
    # ``burst_of`` bounds until - set_at; also bound until - now, so a burst stamped under a clock far behind (or
    # crafted with an old set_at) never runs for more than BURST_MAX_S from here; FUTURE_SLACK_S tolerates the same
    # clock skew a published level stamp does, so a full-length burst set on a host whose clock is ahead survives.
    return burst if isinstance(burst, dict) and 0 < burst["until"] - now <= BURST_MAX_S + FUTURE_SLACK_S else None


def burst_status(level: dict, now: float) -> Optional[dict]:
    """``{factor, until, set_at, remaining_s, active}`` for the payload; None when the level carries no (valid) burst."""
    burst = level.get("burst") if level.get("mode") == AUTO else None
    if not isinstance(burst, dict):
        return None
    active = burst_active(level, now) is not None
    return {"factor": burst["factor"], "until": burst["until"], "set_at": burst["set_at"],
            "remaining_s": max(0.0, burst["until"] - now) if active else 0.0, "active": active}


def _record(data) -> Optional[dict]:
    """``{percent, cap_per_s, set_at}`` from a stored or published level (plus ``mode``, ``budget_at`` and
    ``pool_caps`` for a budget level, ``mode: "auto"``, and its ``burst`` when valid); None when it is not one."""
    if not isinstance(data, dict) or _number(data.get("percent")) is None:
        return None
    limit = _number(data.get("cap_per_s"))
    rec = {"percent": clamp(data.get("percent")), "cap_per_s": None if limit is None else max(0.0, limit),
           "set_at": stamp(data.get("set_at"))}
    if data.get("mode") == AUTO:
        rec.update(mode=AUTO, budget_at=_number(data.get("budget_at")), pool_caps=pool_caps_of(data.get("pool_caps")))
        burst = burst_of(data.get("burst"))
        if burst is not None:
            rec["burst"] = burst
    elif data.get("mode") == MANUAL:
        rec["mode"] = MANUAL
        if isinstance(data.get("pool_caps"), dict):  # carried from the budget in force when the cap was set
            rec.update(budget_at=_number(data.get("budget_at")), pool_caps=pool_caps_of(data.get("pool_caps")))
    return rec


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


def pacing_off(level: dict) -> bool:
    """Whether a level (a ``_record``) is budget pacing switched off: ``mode: "manual"`` present and no ``pool_caps``.
    A legacy level without ``mode`` is not, and neither is a person's cap that carries ``pool_caps`` (``write_cap``
    while pacing was not off): the publisher folds both in."""
    return level.get("mode") == MANUAL and "pool_caps" not in level


def _budget_expired(level: dict, now: float) -> bool:
    budget_at = level.get("budget_at")
    return not isinstance(budget_at, (int, float)) or now - budget_at > BUDGET_EXPIRE_S


def _write_auto(root: Path, level: dict, percent: float, set_at: float, burst: Optional[dict]) -> None:
    data = {"mode": AUTO, "percent": percent, "cap_per_s": level.get("cap_per_s"),
            "pool_caps": dict(level.get("pool_caps") or {}), "budget_at": level.get("budget_at"), "set_at": stamp(set_at)}
    if burst is not None:
        data["burst"] = dict(burst)
    _write_json(level_path(root), data)


def cap_value(cap_per_s) -> Optional[float]:
    """A person's cap in tokens/s: a finite number >= 0, or None (no cap of their own: the budget alone, or no
    throttle without one). Raises ValueError on anything else."""
    if cap_per_s is None:
        return None
    number = _number(cap_per_s)
    if number is None or number < 0:
        raise ValueError("cap must be a number of tokens >= 0, or null for no cap")
    return number


MANUAL_MIN_PERCENT = 0.001  # a manual cap above 0 is never stored at 0 %: older code reads 0 % as "no throttle"


def write_cap(root: Path, cap_per_s, now: float, level: Optional[dict] = None) -> Optional[float]:
    """A person sets the fleet's token cap (tokens/s, None = no cap of their own): a ``mode: "manual"`` level. Every
    gateway obeys it as an absolute cap; the budget-publish host folds it into its budget level as the lower of the
    two (``publish_budget``).

    Read by older code too: a cap of 0 is stored at 100 % (every call held), any other cap at a percent strictly
    between 0 and 100 (older code applies a stored ``cap_per_s`` only there). When ``level`` (the one in force) is a
    live budget level, its ``pool_caps`` and ``budget_at`` are carried, scaled down to the person's cap, so reserve
    holds stay in force until the publisher folds the cap in; the percent is then the share of the budget it cuts
    (``percent_for``), which an older publisher keeps.

    Budget pacing (``pacing_off``): the record carries ``pool_caps`` (``{}`` when there is no live budget to carry)
    unless ``level`` has budget pacing off, so a cap set while pacing is on is folded in by the publisher and one set
    while it is off keeps it off. ``level`` None (unknown) counts as not off."""
    value = cap_value(cap_per_s)
    record: dict = {"mode": MANUAL, "set_at": stamp(now)}
    budget_total = None
    if (level is not None and level.get("mode") == AUTO and level.get("percent", 0) < 100
            and budget(level, now)["state"] != "expired"):
        budget_total = level.get("cap_per_s")
        scale = 1.0 if value is None or not budget_total else min(1.0, value / budget_total)
        record["pool_caps"] = {p: None if v is None else v * scale for p, v in (level.get("pool_caps") or {}).items()}
        record["budget_at"] = level.get("budget_at")
    elif level is None or not pacing_off(level):
        record["pool_caps"] = {}  # no budget to carry; marks the cap as one for the publisher to fold in
    if value is None:
        record["percent"] = 0.0
    elif value == 0:
        record["percent"] = 100.0
    else:
        record["percent"] = min(99.999, max(MANUAL_MIN_PERCENT, percent_for(value, budget_total)))
    if value is not None:
        record["cap_per_s"] = value
    _write_json(level_path(root), record)
    return value


def set_level(root: Path, level: dict, percent, now: float, reference: Optional[float] = None) -> float:
    """The slider: store ``percent`` at ``now``. When ``level`` (the one in force) is a budget level, the new level
    keeps its ``mode``, ``cap_per_s``, ``pool_caps``, ``budget_at`` and ``burst`` and changes only ``percent`` and
    ``set_at``:
    a manual level there would drop every pool cap, reserve holds included, fleet-wide until the publisher folds it
    back in. Otherwise, or when that budget level has expired (no publisher is refreshing it, e.g.
    ``budget_publish`` was turned off), it is a slider level against ``reference`` (``write_level``): the slider is
    the way back to manual mode. On a level with budget pacing off (``pacing_off``) the new level keeps ``mode:
    "manual"`` (and no pool caps), so moving the slider never turns pacing back on."""
    if pacing_off(level):
        value = clamp(percent)
        _write_json(level_path(root), {"mode": MANUAL, "percent": value, "cap_per_s": cap(value, reference),
                                       "set_at": stamp(now)})
        return value
    if level.get("mode") != AUTO or _budget_expired(level, now):
        return write_level(root, percent, now, reference)
    value = clamp(percent)
    _write_auto(root, level, value, now, level.get("burst"))
    return value


def _off_cap(root: Path, level: dict, now: float) -> tuple[float, Optional[float]]:
    """``(percent, cap_per_s)`` for an Off set from the live budget level ``level``: the person's absolute cap in force.
    On the publish host, when ``level`` is its own last write, the cap it recorded (``user-cap.json``, which also
    keeps a cap above the budget); elsewhere the budget scaled by the level's percent, without any burst
    (``person_cap``). Stored like ``write_cap`` stores a cap, so older code enforces it too: None (no cap) at 0 %, 0 at
    100 %, any other cap at a percent strictly between 0 and 100."""
    recorded = read_user_cap(root)
    value = (recorded["cap_per_s"] if recorded["published_set_at"] is not None
             and recorded["published_set_at"] == level["set_at"] else person_cap(level, now))
    if level["percent"] >= 100:
        value = 0.0
    if value is None:
        return 0.0, None
    if value <= 0:
        return 100.0, 0.0
    return min(99.999, max(MANUAL_MIN_PERCENT, percent_for(value, level.get("cap_per_s")))), value


def set_pacing(root: Path, level: dict, on, now: float, reference: Optional[float] = None) -> Optional[float]:
    """The budget pacing switch. ``level`` is the one in force; the new level is stamped like the slider's,
    ``max(now, level set_at + 2 ms)``, so it wins fleet-wide. Returns that stamp, or None when nothing was written.

    * ``on=False``: ``{"mode": "manual", "percent", "cap_per_s", "set_at"}``; any burst and pool caps are dropped.
      The publisher writes nothing while it is in force. Nothing is written when pacing is already off. From a live
      budget level the absolute cap in force is kept (``_off_cap``): a budget level's percent is a person's cap folded
      in by the publisher, relative to the budget, and re-deriving it against this gateway's 24-hour reference would
      loosen (or tighten) it. Otherwise (an expired budget, a level to fold) ``cap_per_s = cap(percent,
      reference)`` with the current percent; ``limit_of`` falls back to that when it is None.
    * ``on=True``: ``{"percent", "cap_per_s", "set_at"}`` without ``mode``, which the publisher folds into a budget
      level on its next pass (the percent then holds back that share of the budget). Written only when pacing is off:
      any other level is already one the publisher folds in (or its budget), and rewriting a live budget level as a
      plain one would drop its pool caps fleet-wide until the next fold.

    ``ValueError`` unless ``on`` is a bool."""
    if not isinstance(on, bool):
        raise ValueError("on must be true or false")
    if pacing_off(level) != on:
        return None  # already in the asked state (on: pacing is not switched off)
    percent = clamp(level.get("percent"))
    set_at = stamp(max(now, (_number(level.get("set_at")) or 0.0) + 0.002))  # 2 ms: stamps round to 1 ms
    data = {"percent": percent, "cap_per_s": cap(percent, reference), "set_at": set_at}
    if not on:
        if level.get("mode") == AUTO and not _budget_expired(level, now):
            data["percent"], data["cap_per_s"] = _off_cap(root, level, now)
        data = {"mode": MANUAL, **data}
    _write_json(level_path(root), data)
    if on:
        # An On after more than BUDGET_SEEN_S off finds no recent budget record anywhere, so without this the
        # panel that was just clicked would read "unavailable" and hide the switch until the publisher's fold arrives.
        _write_json(on_request_path(root), {"at": set_at})
    return set_at


class BurstRefused(ValueError):
    """A burst asked of a level that cannot carry one (a manual level, or an expired budget)."""


def set_burst(root: Path, level: dict, factor, duration_s, now: float) -> dict:
    """Store ``level`` (the budget level in force) with ``burst = {factor, until: now + duration_s, set_at: now}`` at
    ``now``; percent and caps unchanged. ``ValueError`` for a factor outside ``(1, 4]`` or a duration outside
    ``(0, 24 h]``; ``BurstRefused`` when ``level`` is not a live budget level (a burst scales budget caps only)."""
    burst = burst_of({"factor": factor, "until": stamp(now) + (_number(duration_s) or 0.0), "set_at": stamp(now)})
    if burst is None:
        raise ValueError("factor must be a number above 1 and at most 4, and hours above 0 and at most 24")
    if level.get("mode") != AUTO:
        raise BurstRefused("a burst multiplies the budget caps: the level in force is manual (no budget to burst)")
    if _budget_expired(level, now):
        raise BurstRefused("a burst multiplies the budget caps: the budget in force has expired")
    _write_auto(root, level, level["percent"], now, burst)
    return burst


def clear_burst(root: Path, level: dict, now: float) -> bool:
    """End a burst now: store ``level`` without it at ``now``. False (nothing written) when it carries none."""
    if level.get("mode") != AUTO or "burst" not in level:
        return False
    _write_auto(root, level, level["percent"], now, None)
    return True


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


def budget(level: dict, now: float) -> Optional[dict]:
    """What a budget level (``mode: "auto"``) caps right now; None for a slider level. ``state`` is ``fresh``,
    ``stale`` (older than ``BUDGET_STALE_S``: the caps are kept) or ``expired`` (older than ``BUDGET_EXPIRE_S``, or
    no ``budget_at``: no cap, fail open). ``cap_per_s`` and ``pool_caps`` are scaled by the slider and an active
    burst, ``budget x (1 - percent/100) x factor``; ``budget_per_s`` and ``pool_budgets`` are the unscaled budgets,
    ``burst_factor`` the factor in force (1.0 without a burst)."""
    if level.get("mode") != AUTO:
        return None
    at = level.get("budget_at")
    age = None if at is None else now - at
    state = "expired" if age is None or age > BUDGET_EXPIRE_S else "stale" if age > BUDGET_STALE_S else "fresh"
    burst = burst_active(level, now)
    out = {"state": state, "budget_at": at, "age_s": age, "budget_per_s": level.get("cap_per_s"),
           "pool_budgets": dict(level.get("pool_caps") or {}), "cap_per_s": None, "pool_caps": {},
           "burst_factor": burst["factor"] if burst else 1.0}
    if state == "expired":
        return out
    # The burst multiplies on top of the slider; a reserve cap of 0 stays 0 and 100 % stays 0.
    scale = (1.0 - clamp(level["percent"]) / 100.0) * (burst["factor"] if burst else 1.0)
    if level.get("cap_per_s") is not None:
        out["cap_per_s"] = level["cap_per_s"] * scale
    out["pool_caps"] = {p: None if v is None else v * scale for p, v in (level.get("pool_caps") or {}).items()}
    return out


def limit_of(level: dict, reference: Optional[float], now: Optional[float] = None) -> Optional[float]:
    """The cap in tokens/s a level means: the one stored with it, else (a level stored without one) the cap against
    this gateway's own reference (a manual level too: an Off stored where no reference was measured yet). A budget
    level: its scaled global budget (None when expired or unknown), and 0 at 100 %."""
    percent = level["percent"]
    if level.get("mode") == AUTO:
        if percent >= 100:
            return 0.0
        return budget(level, time.time() if now is None else now)["cap_per_s"]
    if level.get("mode") == MANUAL:
        if percent >= 100:
            return 0.0
        # stored without a cap (an Off set where no reference was measured yet): this gateway's own, as below
        return level["cap_per_s"] if level.get("cap_per_s") is not None else cap(percent, reference)
    if 0 < percent < 100 and level.get("cap_per_s") is not None:
        return level["cap_per_s"]
    return cap(percent, reference)


_PROVIDER_PREFIXES = (("claude", "anthropic"), ("gpt-", "openai-codex"), ("codex", "openai-codex"),
                      ("o1", "openai-codex"), ("o3", "openai-codex"), ("o4", "openai-codex"), ("grok", "xai-oauth"))


def provider_of_model(model) -> str:
    """The subscription pool a model name belongs to: core's ``machine_throughput_metrics.provider_for_model``
    (the rule core's capacity model attributes registry ``tokens_by_model`` with), else the same prefix table."""
    try:
        from hermes_cli.machine_throughput_metrics import provider_for_model
        return str(provider_for_model(model))
    except Exception:  # outside a Hermes runtime, or an older core
        name = str(model or "").strip().lower().rsplit("/", 1)[-1]
        return next((provider for prefix, provider in _PROVIDER_PREFIXES if name.startswith(prefix)), "unknown")


def provider_rates(series_db: Path, now: float) -> Optional[dict[str, float]]:
    """Fleet in+out tokens/s per provider now: the series' per-model rows (``tokens_io:<model>``, every gateway)
    over the sampled minutes among the last ``RATE_MINUTES``, attributed by ``provider_of_model``. ``unreported``
    and ``other`` tokens belong to no provider. None when no minute in the window was sampled by model."""
    if not Path(series_db).is_file():
        return None
    from .sampler import ro_connect
    until = int(now) // 60 * 60
    try:
        c = ro_connect(series_db)
    except sqlite3.Error:
        return None
    try:
        rows = c.execute("SELECT minute, substr(metric, 11), SUM(value) FROM minutes WHERE substr(metric, 1, 10) = "
                         "'tokens_io:' AND minute>=? AND minute<? AND value IS NOT NULL GROUP BY minute, metric",
                         (until - RATE_MINUTES * 60, until)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        c.close()
    minutes = {int(m) for m, _, _ in rows}
    if not minutes:
        return None
    totals: dict[str, float] = {}
    for _, model, value in rows:
        if model in ("unreported", "other"):
            continue
        provider = provider_of_model(model)
        totals[provider] = totals.get(provider, 0.0) + float(value)
    return {p: v / (len(minutes) * 60.0) for p, v in totals.items()}


def governed_load(level: dict, series_db: Path, now: float) -> tuple[Optional[float], Optional[float]]:
    """A budget level's start-gate comparison: ``(rate, cap)`` over the pools whose scaled cap is a positive
    number, the rate being their providers' fleet tokens/s only (chats on ungoverned providers, ungoverned
    providers, pools at reserve and pools of unknown rate do not count). ``(None, None)`` when no pool qualifies;
    rate None when the series has no per-model minutes."""
    caps = {p: v for p, v in budget(level, now)["pool_caps"].items() if v is not None and v > 0}
    if not caps:
        return None, None
    rates = provider_rates(series_db, now)
    rate = None if rates is None else sum(rates.get(p, 0.0) for p in caps)
    return rate, sum(caps.values())


def pacing_status(root: Path, level: dict, records: dict, now: float, me: Optional[str] = None) -> dict:
    """``{state, publisher}`` for the panel's budget pacing switch, from the levels already read (no extra query):

    * ``on``: the level in force is a budget level whose budget has not expired (fresh or stale).
    * ``off``: it is ``pacing_off`` (an explicit manual level without pool caps).
    * ``pending``: it is a level the publisher folds in (no ``mode``, or a person's cap carrying ``pool_caps``) set
      under ``PENDING_S`` ago, or under ``PENDING_MAX_S`` ago while ``budget_seen`` (the hub link takes up to about
      four minutes each way, so a fold can take 8-9 minutes to come back).
    * ``unavailable``: anything else: an expired budget level, or a level to fold that no budget level replaced in
      time. No publisher is running (or reaching this gateway).

    ``budget_seen``: a publisher existed within ``BUDGET_SEEN_S``: some level read here (this gateway's own or a
    published one) is a budget level with ``budget_at`` in that window, expired or not (after a long Off the
    publisher's last budget level has expired but is still there), or an explicit Off set in that window (someone
    switched pacing off, so there was a budget to switch off; this also covers an Off set on the publisher host
    itself, which replaced its budget level). The panel hides the switch when it is false and the state is
    ``unavailable``: that fleet has no budget publisher.

    ``publisher`` is the gateway whose budget level (``mode: "auto"``, ``budget_at`` under ``BUDGET_EXPIRE_S`` old) is
    the newest by ``budget_at`` then ``set_at`` among this gateway's own level (named ``me``) and every published
    one; None when there is none. A slider or burst set on another gateway copies the budget level there, so that
    gateway can be named until the publisher's next write (about a minute)."""
    best, best_key, seen = None, None, False
    asked = _number((_read_json(on_request_path(root)) or {}).get("at"))
    # Local newest-wins stamps and millisecond rounding can be just ahead of
    # this read. Apply the same bounded clock slack as the level records below.
    if asked is not None and -FUTURE_SLACK_S <= now - asked <= PENDING_MAX_S:
        seen = True  # this gateway switched pacing on moments ago: a publisher is expected to answer
    candidates = [(me, read_record(root))] + [(gw, _record(e.get("throttle"))) for gw, e in sorted(records.items())]
    for gw, rec in candidates:
        if rec is None or rec["set_at"] > now + FUTURE_SLACK_S:
            continue
        if pacing_off(rec) and now - rec["set_at"] <= BUDGET_SEEN_S:
            seen = True
        at = rec.get("budget_at") if rec.get("mode") == AUTO else None
        if isinstance(at, (int, float)) and now - at <= BUDGET_SEEN_S:
            seen = True
        if rec.get("mode") != AUTO or _budget_expired(rec, now):
            continue
        key = (rec["budget_at"], rec["set_at"])
        if best_key is None or key > best_key:
            best, best_key = gw, key
    publisher = best if best_key is not None else None
    if level.get("mode") == AUTO:
        state = "unavailable" if _budget_expired(level, now) else "on"
    elif pacing_off(level):
        state = "off"
    else:
        age = now - level["set_at"]
        state = "pending" if age < PENDING_S or (seen and age < PENDING_MAX_S) else "unavailable"
    return {"state": state, "publisher": publisher, "budget_seen": seen}


def status(root: Path, series_db: Path, now: Optional[float] = None, hub_db: Optional[Path] = None,
           me: Optional[str] = None) -> dict:
    """``holding`` is what the start gate obeys. A slider level: the fleet rate over its cap (or 100 %). A budget
    level: 100 %, or the governed pools' rate over the sum of their positive scaled caps (``governed_load``); no
    such pool, or a sum of 0, never holds a start (reserve holds stay per pool, in ``Gate.plan`` and core).
    ``pacing`` is ``pacing_status`` (``me`` names this gateway there; default: the series' own name)."""
    now = time.time() if now is None else now
    records = {} if hub_db is None else published(hub_db)
    level = effective(root, hub_db, now, records)
    percent = level["percent"]
    reference, rate = reference_rate(series_db, now), fleet_rate(series_db, now)
    limit = limit_of(level, reference, now)
    b = budget(level, now)
    if b is None:
        holding = percent >= 100 or (limit is not None and rate is not None and rate > limit)
    else:
        governed_rate, governed_cap = governed_load(level, series_db, now)
        b.update(governed_rate_per_s=governed_rate, governed_cap_per_s=governed_cap)
        holding = percent >= 100 or (governed_cap is not None and governed_cap > 0 and governed_rate is not None
                                     and governed_rate > governed_cap)
    return {"percent": percent, "reference_per_s": reference, "cap_per_s": limit, "rate_per_s": rate,
            "holding": holding, "set_at": level["set_at"] or None, "set_by": level["source"],
            "mode": level.get("mode") or "manual", "budget": b, "burst": burst_status(level, now),
            "user_cap_per_s": shown_user_cap(root, level, now),
            "pacing": pacing_status(root, level, records, now, me or local_name(series_db) or "this gateway")}


def shown_user_cap(root: Path, level: dict, now: float) -> Optional[float]:
    """The cap a person set, as far as this gateway can tell: a ``manual`` level's own; on a budget level the
    publish host's record when this is that host, else the budget scaled by its percent when the person's cap is
    the lower one (None: no cap of theirs, or theirs is above the budget)."""
    if level.get("mode") == MANUAL:
        return limit_of(level, None, now)
    if level.get("mode") != AUTO:
        return person_cap(level, now)
    recorded = read_user_cap(root)
    if recorded["set_at"] > 0:
        return recorded["cap_per_s"]
    return person_cap(level, now)


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

    @staticmethod
    def _slot(state: dict, pool: Optional[str]) -> dict:
        """Where a call's start is stamped: the file itself, or a budget pool's own entry under ``pools`` (each
        pool is paced at its own cap; an older process ignores the key)."""
        if pool is None:
            return state
        pools = state.get("pools")
        if not isinstance(pools, dict):
            pools = state["pools"] = {}
        slot = pools.get(pool)
        if not isinstance(slot, dict):
            slot = pools[pool] = {}
        return slot

    def try_take(self, now: float, interval: float, pool: Optional[str] = None) -> float:
        """Start a call now if ``interval`` has passed since the last start (of ``pool``'s calls, when given): 0.0
        (granted; the start is stamped and counted in the minute of ``now``), else the seconds left. Nothing is
        booked while a call waits, so a waiter that gives up, fails or dies leaves no slot behind, and a shorter
        interval applies at once. ``next_at`` is still written for an older process sharing the file: it books its
        slot after this start."""
        def take(state):
            slot = self._slot(state, pool)
            self.clamp_stamps(slot, now, interval)
            ready = self.ready_at(slot, now, interval)
            if now < ready:
                return ready - now
            slot["last_start"] = now
            slot["next_at"] = slot["granted_next_at"] = now + max(0.0, interval)
            self._count(state, now, now)
            return 0.0
        return self._locked(take)

    def note(self, now: float) -> None:
        """Count a call that needed no slot (the throttle is off), so tokens-per-call is already known when the
        throttle is engaged."""
        self._locked(lambda state: self._count(state, now, now))

    def note_wait(self, now: float, task: str, seconds: float) -> None:
        """Count ``seconds`` a card's model call spent held by the throttle, in the minute of ``now``. Per card
        and minute the total is capped at 60 s, so parallel calls of one card waiting together count one slot.
        Kept for ``WAIT_KEEP_S``: the slot estimate (slotgate.py) subtracts held slot-seconds from busy ones."""
        if not task or seconds <= 0:
            return

        def add(state):
            floor = int(now - WAIT_KEEP_S) // 60
            waits = {str(m): {str(t): float(s) for t, s in (v or {}).items() if _number(s) is not None}
                     for m, v in (state.get("waits") or {}).items()
                     if str(m).lstrip("-").isdigit() and int(m) >= floor and isinstance(v, dict)}
            minute = waits.setdefault(str(int(now) // 60), {})
            minute[task] = min(60.0, minute.get(task, 0.0) + seconds)
            state["waits"] = waits
        self._locked(add)

    def waited(self, minute_start: int) -> float:
        """Held slot-seconds counted in the minute starting at epoch second ``minute_start`` (reads without the
        lock; a missing or odd file is 0)."""
        state = _read_json(self.path)
        waits = state.get("waits") if isinstance(state, dict) else None
        cell = waits.get(str(int(minute_start) // 60)) if isinstance(waits, dict) else None
        if not isinstance(cell, dict):
            return 0.0
        return sum(min(60.0, float(s)) for s in cell.values() if _number(s) is not None and s > 0)

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
                 beat: Optional[Callable[[float], None]] = None,
                 running: Optional[Callable[[], Optional[int]]] = None):
        self.root, self.series_db, self._sleep, self._clock = Path(root), Path(series_db), sleep, clock
        self.hub_db, self._beat = (None if hub_db is None else Path(hub_db)), beat
        self._lock = threading.Lock()
        self._pacer = Pacer(self.root)
        self._rate: Optional[tuple[float, float, float]] = None  # (taken at, share, tokens per call)
        self._start: Optional[dict] = None  # the start gate's last read (``_read_start``)
        if running is None:
            from . import slotgate
            running = slotgate.active_runs
        self._running = running

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

    def plan(self, now: float, provider: Optional[str] = None) -> tuple:
        """``("pass",)``, ``("hold",)`` (100 %, or a cap of zero) or ``("pace", seconds between calls, set_at)``
        (``("pace", seconds, set_at, pool)`` for a budget pool). Off and fully on read only the level; a level in
        between reads the series. Any failed read passes. A budget level paces the call's ``provider`` pool: cap 0
        holds, None (rate unknown), no pool cap for it, or an expired budget passes."""
        try:
            level = effective(self.root, self.hub_db, now)
            percent = level["percent"]
            if level.get("mode") == AUTO:
                return self._budget_plan(level, now, provider)
            if level.get("mode") == MANUAL:
                limit = limit_of(level, None if level.get("cap_per_s") is not None or not 0 < percent < 100
                                 else reference_rate(self.series_db, now), now)
                if limit is not None and limit <= 0:
                    return ("hold",)
                carried_at = level.get("budget_at")
                live = isinstance(carried_at, (int, float)) and now - carried_at <= BUDGET_EXPIRE_S
                pools = (level.get("pool_caps") or {}) if live else {}  # an old budget's holds expire, as a budget's do
                if isinstance(provider, str) and provider in pools and pools[provider] is not None:
                    pool = pools[provider]  # the budget's pool cap, carried and scaled when the cap was set
                    if pool <= 0:
                        return ("hold",)  # that pool is at reserve
                    share, tokens = self._share_and_tokens(now)
                    return ("pace", min(MAX_INTERVAL_S, tokens / (share * pool)), level["set_at"], provider)
                if limit is None:
                    return ("pass",)  # a person removed their cap and no budget host folds it: no throttle
                share, tokens = self._share_and_tokens(now)
                return ("pace", min(MAX_INTERVAL_S, tokens / (share * limit)), level["set_at"])
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

    def _budget_plan(self, level: dict, now: float, provider: Optional[str]) -> tuple:
        if level["percent"] >= 100:
            return ("hold",)
        caps = budget(level, now)["pool_caps"]
        limit = caps.get(provider) if isinstance(provider, str) else None
        if limit is None:
            return ("pass",)  # an ungoverned provider, a pool whose rate is unknown, or no budget: fail open
        if limit <= 0:
            return ("hold",)  # the pool is at reserve (or the slider takes it all)
        share, tokens = self._share_and_tokens(now)
        allowed = share * limit
        if allowed <= 0:
            return ("hold",)
        return ("pace", min(MAX_INTERVAL_S, tokens / allowed), level["set_at"], provider)

    def _note_wait(self, now: float, seconds: float) -> None:
        """Count a card's held seconds for the slot estimate (best effort; cron and chats are not card slots)."""
        task = next((os.environ.get(name) for name in CARD_ENV if os.environ.get(name)), None)
        if not task:
            return
        try:
            self._pacer.note_wait(now, task, seconds)
        except Exception:  # counting is best effort
            log.debug("throughput throttle: wait count failed", exc_info=True)

    def holding(self) -> bool:
        """True while every call waits (100 %); a paced level is not a hold."""
        return self.plan(self._clock())[0] == "hold"

    def wait(self, *, beat: Optional[Callable[[float], None]] = None,
             stop: Optional[Callable[[], bool]] = None, max_s: Optional[float] = None,
             provider: Optional[str] = None) -> float:
        """Block until this call may start; returns the seconds waited. ``beat(waited)`` runs every poll (the
        caller's liveness report), ``stop()`` true or ``max_s`` reached ends the wait early. A waiting call re-reads
        the level every poll: lowering it releases the call. Grant-at-start: a paced call asks ``Pacer.try_take``
        each poll and is stamped only when it starts, so a wait holds no slot and a level change books nothing."""
        started = self._clock()
        announced = False
        held = [0.0]  # held seconds not yet counted (+ when): written to the pace file once a minute
        try:
            return self._wait(started, announced, held, beat, stop, max_s, provider)
        finally:
            self._held_flush(held)

    def _held_add(self, minute, held: list, now: float, seconds: float):
        """Add ``seconds`` held at ``now``; count the pending ones in the pace file when the minute turns, so a
        waiting call takes the lock once a minute, not every poll."""
        this = int(now) // 60
        if minute is not None and this != minute:
            self._held_flush(held)
        held[0] += max(0.0, seconds)
        held.append(now) if len(held) == 1 else held.__setitem__(1, now)
        return this

    def _held_flush(self, held: list) -> None:
        if held[0] > 0 and len(held) > 1:
            self._note_wait(held[1], held[0])
        held[0] = 0.0

    def _wait(self, started, announced, held, beat, stop, max_s, provider) -> float:
        held_minute = None
        while True:
            now = self._clock()
            plan = self.plan(now) if provider is None else self.plan(now, provider)
            if plan[0] == "pass":
                try:
                    self._pacer.note(now)  # it starts now: counted, so tokens-per-call stays measured
                except Exception:  # counting is best effort
                    log.debug("throughput throttle: call count failed", exc_info=True)
                break
            if plan[0] == "pace":
                try:
                    left = (self._pacer.try_take(now, plan[1]) if len(plan) < 4
                            else self._pacer.try_take(now, plan[1], plan[3]))
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
            before = self._clock()
            self._sleep(nap)
            after = self._clock()
            held_minute = self._held_add(held_minute, held, after, after - before)
            waited = after - started
            if (max_s is not None and waited >= max_s) or (stop is not None and _true(stop)):
                log.info("throughput throttle: wait ended after %.0fs before its start", waited)
                return waited
        waited = self._clock() - started
        if announced:
            log.info("throughput throttle: released after %.0fs", waited)
        return waited

    def _read_start(self, now: float) -> dict:
        """What the start gate decides from for ``START_GATE_TTL_S``: this gateway's slot limit (slotgate.py) and
        running card count, or, when either is unknown, the fleet rate over the cap (the rule before slots)."""
        from . import slotgate
        st = status(self.root, self.series_db, now, self.hub_db)
        slots = slotgate.local(self.root, self.series_db, st["cap_per_s"], now)
        running = self._running()
        return {"at": now, "allowed": slots["allowed"], "running": running, "granted": set(),
                "rate_hold": bool(st["holding"])}

    def start_gate(self, task_id=None, **_card) -> Optional[dict]:
        """``kanban_start_gate``: hold a card start while this gateway already runs the card slots its share of the
        token cap pays for (``slotgate``): its running cards plus the starts let through since the last read reach
        the allowed count. Without a slot limit (no cap) or a card count, the older rule holds: the fleet's token
        rate over the cap (or 100 %). Read every ``START_GATE_TTL_S``; any failed read starts the card."""
        now = self._clock()
        with self._lock:
            hit = self._start
        if hit is None or not 0 <= now - hit["at"] < START_GATE_TTL_S:
            try:
                hit = self._read_start(now)
            except Exception:  # fail open: a broken read must never stop the dispatcher starting cards
                log.debug("throughput throttle: start gate read failed", exc_info=True)
                hit = {"at": now, "allowed": None, "running": None, "granted": set(), "rate_hold": False}
            with self._lock:
                self._start = hit
        with self._lock:
            if hit["allowed"] is None or hit["running"] is None:
                return {"hold": True, "reason": START_GATE_REASON} if hit["rate_hold"] else None
            # Grants are counted per card: core asks about the same card more than once per tick (pickup's
            # would-it-start probe, then the claim), and a card asked again must not take a second slot.
            key = task_id if task_id is not None else object()
            if key in hit["granted"]:
                return None
            if hit["running"] + len(hit["granted"]) >= hit["allowed"]:
                return {"hold": True, "reason": SLOT_GATE_REASON}
            hit["granted"].add(key)  # counted until the next read sees it running
            return None

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
        provider = context.get("provider")
        if agent is None:
            self.wait(beat=_card_heartbeat, provider=provider)
        else:
            self.wait(beat=lambda waited: agent._touch_activity(f"token throttle: waiting ({waited:.0f}s)"),
                      stop=lambda: bool(getattr(agent, "_interrupt_requested", False)), provider=provider)
        return next_call(request)

    def aux_hold(self, aux_task=None, platform=None, **_payload) -> None:
        """``pre_auxiliary_call``: a card's or cron's auxiliary call waits too, for at most ``AUX_HOLD_MAX_S``.
        ``platform`` is core's parent-turn platform (empty for a call outside a turn), so outside a turn only the
        card environment decides."""
        if str(aux_task or "") in AUX_NOT_HELD or self._exempt(platform):
            return
        self.wait(beat=_card_heartbeat, max_s=AUX_HOLD_MAX_S, provider=_payload.get("provider"))

    aux_hold.hermes_hook_timeout = AUX_HOOK_TIMEOUT_S


# --- the budget publisher: core's capacity model -> the fleet level (one host, the sampler, once a minute) -----

def budget_publisher() -> bool:
    """Whether this host publishes the budget: core config ``kanban.llm_pacing.budget_publish`` is true (read the
    way core's dispatcher reads its pacing section). False outside a Hermes runtime or on any unreadable config."""
    try:
        from hermes_cli.kanban_model_tier import load_tier_config
        section = load_tier_config()
    except Exception:  # an older core without the tier loader
        try:
            from hermes_cli.config import load_config
            section = (load_config() or {}).get("kanban")
        except Exception:
            return False
    pacing = section.get("llm_pacing") if isinstance(section, dict) else None
    return isinstance(pacing, dict) and pacing.get("budget_publish") is True


def budget_of_v2(v2) -> Optional[dict]:
    """``{cap_per_s, pool_caps, budget_at}`` from core's ``llm_pacing.json`` ``v2``; None when it is missing or
    malformed (no finite ``computed_at``, ``pools`` not a map, a global rate that is neither None nor a number >= 0).
    A pool at reserve caps at 0; a pool whose rate is unknown (or unreadable) is None; a bad pool key is skipped."""
    if not isinstance(v2, dict):
        return None
    at, pools, total = _number(v2.get("computed_at")), v2.get("pools"), v2.get("global_rate_tokens_per_s")
    if at is None or at <= 0 or not isinstance(pools, dict):
        return None
    if total is not None and (_number(total) is None or total < 0):
        return None
    caps: dict[str, Optional[float]] = {}
    for provider, entry in pools.items():
        if not provider_name(provider) or not isinstance(entry, dict):
            continue
        rate = _number(entry.get("rate_tokens_per_s"))
        caps[provider] = 0.0 if entry.get("at_reserve") is True else (None if rate is None or rate < 0 else rate)
    return {"cap_per_s": None if total is None else float(total), "pool_caps": caps, "budget_at": at}


def _moved(old: Optional[float], new: Optional[float]) -> bool:
    if old is None or new is None:
        return (old is None) != (new is None)
    return abs(new - old) > BUDGET_MOVE * abs(old) if old else new != 0


def user_cap_path(root: Path) -> Path:
    return Path(root) / "cache" / "throughput" / USER_CAP_FILE


def read_user_cap(root: Path) -> dict:
    """``{cap_per_s, set_at}``: the newest cap a person set, as the budget-publish host recorded it (no cap, never
    set, when the file is missing or unreadable)."""
    data = _read_json(user_cap_path(root))
    if not isinstance(data, dict):
        return {"cap_per_s": None, "set_at": 0.0, "published_set_at": None, "published_percent": None}
    try:
        value = cap_value(data.get("cap_per_s"))
    except ValueError:
        value = None
    published = _number(data.get("published_set_at"))
    return {"cap_per_s": value, "set_at": stamp(data.get("set_at")),
            "published_set_at": None if published is None else stamp(published),
            "published_percent": _number(data.get("published_percent"))}


def person_cap(level: dict, now: float, budget_per_s: Optional[float] = None) -> Optional[float]:
    """The cap (tokens/s) a level a person set means: a ``manual`` level's own cap; an older panel's slider on a
    budget level, its percent against that level's budget; a plain slider level, its percent against
    ``budget_per_s`` (what the slider showed on a budget fleet), else its stored cap. 0 % = no cap of their own."""
    if level.get("mode") == MANUAL:
        return limit_of(level, None, now)
    percent = level["percent"]
    if percent <= 0:
        return None
    if percent >= 100:
        return 0.0
    if level.get("mode") == AUTO:  # the budget it was set against, without any burst factor
        base = level.get("cap_per_s")
        return None if base is None else base * (1.0 - percent / 100.0)
    if budget_per_s is not None:
        return budget_per_s * (1.0 - percent / 100.0)
    return limit_of(level, None, now)


def percent_for(user: Optional[float], budget_per_s: Optional[float]) -> float:
    """The budget level's ``percent`` that makes its cap the lower of a person's cap and the budget: every gateway
    (older ones too) scales the budget and each pool cap by ``1 - percent/100``."""
    if user is None:
        return 0.0
    if user <= 0:
        return 100.0
    if budget_per_s is None or budget_per_s <= 0 or user >= budget_per_s:
        return 0.0
    return round(100.0 * (1.0 - user / budget_per_s), 4)


def publish_budget(root: Path, hub_db: Optional[Path], pacing_file: Path, now: Optional[float] = None) -> bool:
    """Write the budget into this host's level file (``mode: "auto"``); True when it wrote. A level a person set
    since the last fold (``write_cap`` anywhere, or a slider) becomes the recorded person cap (``user-cap.json``),
    and the written ``percent`` makes the cap the lower of that and the budget (``percent_for``). Rewrites only
    when the level it obeys is not its own budget, the percent or any cap moved by more than ``BUDGET_MOVE``, or
    ``BUDGET_REWRITE_S`` passed since the last write. A missing or malformed ``v2`` writes nothing. ``set_at`` is
    the obeyed level's plus 1 ms, not the clock: a newer setting on another gateway then still outranks this write
    until it is folded in. An active ``burst`` on the obeyed budget level is kept; an expired one is dropped. While
    the obeyed level has budget pacing off (``pacing_off``: a ``set_pacing(on=False)`` level) it writes nothing at
    all, so the switch sticks; a level without ``mode`` is folded in as before."""
    now = time.time() if now is None else now
    data = _read_json(pacing_file)
    new = budget_of_v2(data.get("v2") if isinstance(data, dict) else None)
    if new is None:
        return False
    level = effective(root, hub_db, now)
    if pacing_off(level):
        return False  # a person switched budget pacing off (``set_pacing``): no budget level, no fold
    user = read_user_cap(root)
    own = level["source"] is None and level.get("mode") == AUTO and level["set_at"] == user.get("published_set_at")
    # A budget level written elsewhere with the percent last published (a burst set or ended, a republish) moves
    # no cap: only a changed percent (an older panel's slider) or a manual level is a person's setting.
    same_percent = (level.get("mode") == AUTO and user.get("published_percent") is not None
                    and abs(level["percent"] - user["published_percent"]) < 1e-3)
    if not own and not same_percent and level["set_at"] > user["set_at"]:
        user = {"cap_per_s": person_cap(level, now, new["cap_per_s"]), "set_at": level["set_at"]}
        _write_json(user_cap_path(root), user)
    percent = percent_for(user["cap_per_s"], new["cap_per_s"])
    path = level_path(root)
    if level["source"] is None and level.get("mode") == AUTO:
        try:
            age = now - path.stat().st_mtime
        except OSError:
            age = BUDGET_REWRITE_S
        old = level.get("pool_caps") or {}
        moved = (_moved(level.get("cap_per_s"), new["cap_per_s"]) or set(old) != set(new["pool_caps"])
                 or any(_moved(old[p], v) for p, v in new["pool_caps"].items())
                 or abs(level["percent"] - percent) > 0.5)
        if not moved and age < BUDGET_REWRITE_S:
            return False
    set_at = stamp(level["set_at"] + 0.001) if level["set_at"] > 0 else stamp(now)
    data = {"mode": AUTO, "percent": percent, "cap_per_s": new["cap_per_s"],
            "pool_caps": new["pool_caps"], "budget_at": round(new["budget_at"], 3), "set_at": set_at}
    if burst_active(level, now) is not None:  # a running burst rides on: only its expiry ends it
        data["burst"] = dict(level["burst"])
    _write_json(path, data)
    _write_json(user_cap_path(root), {"cap_per_s": user["cap_per_s"], "set_at": user["set_at"],
                                      "published_set_at": set_at, "published_percent": percent})
    return True


def publish_budget_if_enabled(root: Path, hub_db: Optional[Path], now: Optional[float] = None) -> bool:
    """The sampler's once-a-minute step: publish only on the budget-publish host."""
    if not budget_publisher():
        return False
    from .runway import pacing_path
    return publish_budget(root, hub_db, pacing_path(root), now)


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
