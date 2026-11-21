"""GET /api/plugins/throughput/v1/series?range=1h|6h|24h|7d[&by=gateway|model] (see ../series.py for the payload).
GET / PUT /api/plugins/throughput/v1/throttle: the fleet's token throttle level (../throttle.py) and the
subscription pools' runway (../runway.py). PUT body ``{"percent": 0..100}``.
PUT / DELETE /api/plugins/throughput/v1/throttle/burst: start a burst on the budget level (body ``{"factor": 1 < f <= 4,
"hours": 0 < h <= 24}``; 409 on a manual or expired level) or end it now.
PUT /api/plugins/throughput/v1/throttle/pacing: the budget pacing switch (body ``{"on": true|false}``, anything else a
400); answers with the GET /v1/throttle payload, whose ``pacing`` is ``{state, publisher, budget_seen}``.
GET /api/plugins/throughput/v1/budget[?factor=&hours=[&projection_only=1]]: this host's capacity model, the measured
spend per provider over 7 days (cached a minute per process) and the current burst, plus the cost of the given burst
(../budget_view.py); ``projection_only`` returns just the burst and its cost.

POST /api/plugins/throughput/v1/watch (body ``{"series": [{"range": "6h", "by": "gateway"}, ...], "advisory": bool}``):
register interest (5 min) and catch up: answers the current snapshot (below). GET /v1/throttle and /v1/series count as
interest too. Only GET /v1/throttle and a watch with ``advisory: true`` keep the per-minute slots advisory computing.
Push: ``plugin.throughput.snapshot`` ``{feed, epoch, seq, at, minute, interval_s, throttle, series, series_keys}`` once a
minute (right after the minute's advisory flight, advisory.py; at the latest ``PUSH_DEADLINE_S`` into the minute) and
after an advisory flight that changed it, while a client showed interest in the last ``INTEREST_S``. ``throttle`` is
the GET /v1/throttle payload, ``series`` the GET /v1/series payloads of the (range, by) views read or watched lately
(at most ``MAX_SERIES_KEYS``). JSON-safe; over ``PUSH_MAX_BYTES`` the series are sent by name only (``series_keys``).
``feed`` (host:pid) is also on the GET /v1/throttle payload, so a client can tell which connection a push is from.

Loaded by path by the dashboard, outside its package: the plugin package is imported under a private name.
Single-flight per (range, by) for 15 s; the slow work (sqlite reads, registry fetch) runs in a worker thread.
Requesting the series also starts the sampler in this process if no other process on this Hermes root runs it
(a file lock keeps it to one).
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException, Query

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app):
    import importlib
    advisory = importlib.import_module(pkg().__name__ + ".advisory")
    advisory.start()
    try:
        yield
    finally:
        stop_pusher()
        await asyncio.to_thread(advisory.stop)


router = APIRouter(lifespan=lifespan)
CACHE_S = 15
_lock = threading.Lock()
_range_locks: Dict[str, threading.Lock] = {}
_cache: Dict[str, tuple] = {}
_state: Dict[str, Any] = {}


def pkg():
    """The plugin package (``__init__`` + submodules) under a private name."""
    name = "hermes_throughput_plugin"
    with _lock:
        mod = sys.modules.get(name)
        if mod is None:
            root = Path(__file__).resolve().parents[1]
            spec = importlib.util.spec_from_file_location(name, root / "__init__.py",
                                                          submodule_search_locations=[str(root)])
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod = sys.modules.setdefault(name, mod)
        return mod


def _services():
    p = pkg()
    import importlib
    sampler = importlib.import_module(p.__name__ + ".sampler")
    cards = importlib.import_module(p.__name__ + ".cards")
    series = importlib.import_module(p.__name__ + ".series")
    store_mod = importlib.import_module(p.__name__ + ".store")
    with _lock:
        if "store" not in _state:
            root = sampler.hermes_root()
            _state.update(root=root, store=store_mod.Store(sampler.series_path(root)),
                          cards=cards.Cards(root, known=_known_machines))
    return sampler, series


def _registry():
    try:
        from hermes_cli import kanban_worker_node
        return kanban_worker_node.registry()
    except Exception:
        return None


def _known_machines():
    reg = _registry()
    names = set(((reg or {}).get("machines") or {}).keys())
    return names or None


BYS = ("gateway", "model")


def build(range_key: str, by: str = "gateway") -> Dict[str, Any]:
    sampler, series = _services()
    try:
        sampler.start(_state["root"], replace_stale=False)  # no-op when a sampler thread already runs here
    except Exception:
        pass
    reg = _registry()
    machines = list(((reg or {}).get("machines") or {}).keys())
    local = (reg or {}).get("me") or _state["store"].get_meta("me") or "local"
    if by != "gateway":
        return series.build(_state["store"], _state["cards"].events(), machines, local, range_key, by=by)
    return series.build(_state["store"], _state["cards"].events(), machines, local, range_key)


def cached(range_key: str, by: str = "gateway", now: float | None = None) -> Dict[str, Any]:
    key = range_key if by == "gateway" else f"{range_key}|{by}"  # the default keeps its plain range key
    with _lock:
        rlock = _range_locks.setdefault(key, threading.Lock())
    with rlock:  # single flight: concurrent requests for one (range, by) wait for the same build
        now = time.monotonic() if now is None else now
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_S:
            return hit[1]
        payload = build(range_key) if by == "gateway" else build(range_key, by)
        _cache[key] = (time.monotonic(), payload)
        return payload


@router.get("/ping")
async def ping() -> Dict[str, Any]:
    return {"ok": True, "api": 1}


@router.get("/v1/series")
async def get_series(range: str = Query("6h"), by: str = Query("gateway")) -> Dict[str, Any]:  # noqa: A002
    if range not in ("1h", "6h", "24h", "7d"):
        raise HTTPException(status_code=400, detail="range must be 1h, 6h, 24h or 7d")
    if by not in BYS:
        raise HTTPException(status_code=400, detail="by must be gateway or model")
    note_interest([(range, by)])
    return await asyncio.to_thread(cached, range, by)


def throttle_payload(now: float | None = None, interest: bool = True) -> Dict[str, Any]:
    """Level, cap and measured rate (tokens/s, in+out) the fleet obeys, plus each pool's runway. ``fleet`` is false
    on a host without the hermes-hub link (the level then applies to ``scope``, this gateway, alone);
    ``gateways`` lists this and every running gateway with whether it has acknowledged the current level."""
    sampler, _ = _services()
    import importlib
    throttle = importlib.import_module(pkg().__name__ + ".throttle")
    runway = importlib.import_module(pkg().__name__ + ".runway")
    root = _state["root"]
    now = time.time() if now is None else now
    hub_db = throttle.hub_db_path()
    reg = _registry()
    scope = (reg or {}).get("me") or _state["store"].get_meta("me") or "local"
    out = throttle.status(root, sampler.series_path(root), now, hub_db, me=scope)
    out["scope"] = scope
    out["fleet"] = hub_db.is_file()
    out["gateways"] = throttle.gateways(root, hub_db, now, out["scope"])
    out["pools"] = runway.pools(runway.pacing_path(root), now)
    try:  # advisory (the Runners panel crosses out slots a limit will not let run): never fail the route
        slots = importlib.import_module(pkg().__name__ + ".slots")
        out["slots"] = slots.payload(root, sampler.series_path(root), out["cap_per_s"], cards=_state["cards"],
                                     now=now, interest=interest)
    except Exception:
        out["slots"] = None
    try:  # this gateway's enforced slot limit (slotgate.py): what its start gate obeys
        slotgate = importlib.import_module(pkg().__name__ + ".slotgate")
        gate = slotgate.local(root, sampler.series_path(root), out["cap_per_s"], now)
        gate["running"] = slotgate.active_runs()
        out["slot_gate"] = gate
        # This gateway's crossed-out slots are the ones its gate enforces; other rows stay advisory estimates
        # (their held time is not published, so their cost per slot counts throttled slots too).
        for name, row in ((out.get("slots") or {}).get("gateways") or {}).items():
            row["enforced"] = name == out["scope"]
            if row["enforced"]:
                row.update(allowed=gate["allowed"], tokens_per_slot_per_s=gate["per_slot_per_s"],
                           cost_source=gate["source"])
    except Exception:
        out["slot_gate"] = None
    out["now"] = now
    out["feed"] = FEED_ID
    return out


def set_cap(cap_per_min: Any) -> Dict[str, Any]:
    """A person sets the fleet's token cap in tokens per minute (None = no cap of their own; the budget alone
    then caps on a budget fleet). Stored as a ``manual`` level stamped after the level in force."""
    if cap_per_min is not None and (isinstance(cap_per_min, bool) or not isinstance(cap_per_min, (int, float))
                                    or cap_per_min != cap_per_min or not 0 <= cap_per_min < 1e12):
        raise ValueError("cap_per_min must be a number of tokens per minute >= 0, or null for no cap")
    sampler, _ = _services()
    import importlib
    throttle = importlib.import_module(pkg().__name__ + ".throttle")
    root, hub_db, now = _state["root"], throttle.hub_db_path(), time.time()
    level = throttle.effective(root, hub_db, now)
    stamp = max(now, level["set_at"] + 0.002)  # 2 ms: stamps round to 1 ms
    throttle.write_cap(root, None if cap_per_min is None else cap_per_min / 60.0, stamp, level)
    throttle.note_seen(root, hub_db, stamp)
    return throttle_payload()


def set_throttle(percent: Any) -> Dict[str, Any]:
    if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 <= percent <= 100:
        raise ValueError("percent must be a number from 0 to 100")
    sampler, _ = _services()
    import importlib
    throttle = importlib.import_module(pkg().__name__ + ".throttle")
    root, hub_db, now = _state["root"], throttle.hub_db_path(), time.time()
    # Stamp it after the level in force, so a gateway whose clock runs ahead cannot outrank this newer setting.
    level = throttle.effective(root, hub_db, now)
    stamp = _stamp_after(level, now)
    # On a budget level only percent and set_at change: the pool caps (reserve holds) stay in force fleet-wide.
    throttle.set_level(root, level, percent, stamp, throttle.reference_rate(sampler.series_path(root), now))
    throttle.note_seen(root, hub_db, stamp)
    return throttle_payload()


@router.get("/v1/throttle")
async def get_throttle() -> Dict[str, Any]:
    note_interest()
    return await asyncio.to_thread(throttle_payload)


@router.put("/v1/throttle")
async def put_throttle(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        if "cap_per_min" in body:  # a person's absolute cap (the Runners panel's tokens/min field, the CLI)
            return await asyncio.to_thread(set_cap, body.get("cap_per_min"))
        return await asyncio.to_thread(set_throttle, body.get("percent"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _stamp_after(level, now: float) -> float:
    """A ``set_at`` after the level in force (2 ms: stamps round to 1 ms), so a gateway whose clock runs ahead
    cannot outrank this newer setting."""
    return max(now, level["set_at"] + 0.002)


def _burst_args(factor: Any, hours: Any) -> tuple[float, float]:
    for value in (factor, hours):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
            raise ValueError("factor and hours must be numbers")
    if not 1 < factor <= 4:
        raise ValueError("factor must be above 1 and at most 4")
    if not 0 < hours <= 24:
        raise ValueError("hours must be above 0 and at most 24")
    return float(factor), float(hours)


def set_burst(factor: Any, hours: Any) -> Dict[str, Any]:
    factor, hours = _burst_args(factor, hours)
    _services()
    import importlib
    throttle = importlib.import_module(pkg().__name__ + ".throttle")
    root, hub_db, now = _state["root"], throttle.hub_db_path(), time.time()
    level = throttle.effective(root, hub_db, now)
    stamp = _stamp_after(level, now)
    throttle.set_burst(root, level, factor, hours * 3600.0, stamp)  # BurstRefused on a manual or expired level
    throttle.note_seen(root, hub_db, stamp)
    return throttle_payload()


def clear_burst() -> Dict[str, Any]:
    _services()
    import importlib
    throttle = importlib.import_module(pkg().__name__ + ".throttle")
    root, hub_db, now = _state["root"], throttle.hub_db_path(), time.time()
    level = throttle.effective(root, hub_db, now)
    stamp = _stamp_after(level, now)
    if throttle.clear_burst(root, level, stamp):
        throttle.note_seen(root, hub_db, stamp)
    return throttle_payload()


def set_pacing(on: Any) -> Dict[str, Any]:
    """The budget pacing switch: ``on`` false stores an explicit manual level (the publisher stops), true a level the
    publisher folds into a budget level on its next pass (``throttle.set_pacing``); asking for the state already in
    force writes nothing. Stamped after the level in force, like the slider."""
    if not isinstance(on, bool):
        raise ValueError("on must be true or false")
    sampler, _ = _services()
    import importlib
    throttle = importlib.import_module(pkg().__name__ + ".throttle")
    root, hub_db, now = _state["root"], throttle.hub_db_path(), time.time()
    level = throttle.effective(root, hub_db, now)
    stamp = throttle.set_pacing(root, level, on, now, throttle.reference_rate(sampler.series_path(root), now))
    if stamp is not None:
        throttle.note_seen(root, hub_db, stamp)
    return throttle_payload()


@router.put("/v1/throttle/pacing")
async def put_pacing(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(set_pacing, body.get("on"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/v1/throttle/burst")
async def put_burst(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    import importlib
    throttle = importlib.import_module(pkg().__name__ + ".throttle")
    try:
        return await asyncio.to_thread(set_burst, body.get("factor"), body.get("hours"))
    except throttle.BurstRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/v1/throttle/burst")
async def delete_burst() -> Dict[str, Any]:
    return await asyncio.to_thread(clear_burst)


HISTORY_CACHE_S = 60.0  # the 7-day hourly history moves by one bucket an hour: re-read it at most once a minute
_history_lock = threading.Lock()
_history_cache: Dict[str, tuple] = {}


def cached_history(view, series_db, now: float, clock=time.monotonic) -> Dict[str, Any]:
    """``budget_view.history`` for this process, re-read at most every ``HISTORY_CACHE_S`` per series db (single
    flight: concurrent callers wait for one read)."""
    key = str(series_db)
    with _history_lock:  # the read is ~0.2 s on a large series; one at a time is fine for one panel
        hit = _history_cache.get(key)
        if hit is not None and 0 <= clock() - hit[0] < HISTORY_CACHE_S:
            return hit[1]
        value = view.history(series_db, now)
        _history_cache[key] = (clock(), value)
        return value


def budget_payload(factor: float | None = None, hours: float | None = None, now: float | None = None,
                   projection_only: bool = False) -> Dict[str, Any]:
    """``{now, computed_at, margin_s, global_rate_tokens_per_s, pools, events, curve, history, burst, projection}``:
    one read of this host's ``llm_pacing.json`` and the 7-day series slice (``cached_history``, at most once a
    minute). ``projection`` is the cost of a ``factor`` x burst for ``hours`` (``budget_view.project``), or None when
    no burst was asked about. ``projection_only``: just ``{now, computed_at, burst, projection}`` (the panel asks
    this when only its burst pick changes; it reads no series)."""
    sampler, _ = _services()
    import importlib
    import json
    throttle = importlib.import_module(pkg().__name__ + ".throttle")
    runway = importlib.import_module(pkg().__name__ + ".runway")
    view = importlib.import_module(pkg().__name__ + ".budget_view")
    root = _state["root"]
    now = time.time() if now is None else now
    try:
        data = json.loads(runway.pacing_path(root).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        data = None
    v2 = data.get("v2") if isinstance(data, dict) else None
    chart = view.chart_v2(v2)
    if projection_only:
        out: Dict[str, Any] = {"now": now, "computed_at": chart["computed_at"]}
    else:
        out = {"now": now, **chart, "history": cached_history(view, sampler.series_path(root), now)}
    out["burst"] = throttle.burst_status(throttle.effective(root, throttle.hub_db_path(), now), now)
    out["projection"] = None
    if factor is not None:
        out["projection"] = {"factor": factor, "hours": hours, "pools": view.project(v2, factor, hours * 3600.0, now)}
    return out


@router.get("/v1/budget")
async def get_budget(factor: float | None = Query(None), hours: float | None = Query(None),
                     projection_only: bool = Query(False)) -> Dict[str, Any]:
    if (factor is None) != (hours is None):
        raise HTTPException(status_code=400, detail="give both factor and hours, or neither")
    if projection_only and factor is None:
        raise HTTPException(status_code=400, detail="projection_only needs factor and hours")
    if factor is not None:
        try:
            factor, hours = _burst_args(factor, hours)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return await asyncio.to_thread(budget_payload, factor, hours, None, projection_only)


# ── push: one snapshot a minute while someone looks ──────────────────────────────────────────────────────────────────
import itertools  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import socket  # noqa: E402
import uuid  # noqa: E402

PLUGIN_ID = "throughput"
PUSH_EVENT = "snapshot"
FEED_ID = f"{socket.gethostname()}:{os.getpid()}"
INTEREST_S = 300.0  # a client that neither read nor watched for 5 min gets no pushes and causes no computation
PUSH_DEADLINE_S = 20.0  # push by this far into a minute even if its advisory flight has not finished
PUSH_MAX_BYTES = 256 * 1024
MAX_SERIES_KEYS = 4
_log = logging.getLogger("hermes.plugins.throughput")


def json_safe(v):
    """``v`` as plain JSON: non-finite floats become null, unknown types their str(), keys strings."""
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else None
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, dict):
        return {str(k): json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [json_safe(x) for x in v]
    return str(v)


def series_key(range_key: str, by: str) -> str:
    return f"{range_key}|{by}"


class Pusher:
    """Interest, the push thread and its snapshots for this process. ``emit(payload)`` delivers one push; ``clock`` is
    monotonic, ``wall`` the minute clock. Tests drive ``step()`` directly."""

    def __init__(self, emit, build_throttle, build_series, clock=time.monotonic, wall=time.time):
        self.emit = emit
        self.build_throttle = build_throttle
        self.build_series = build_series
        self.clock = clock
        self.wall = wall
        self.epoch = uuid.uuid4().hex[:12]
        self.seq = itertools.count(1)
        self.lock = threading.Lock()
        self.interest_at = float("-inf")
        self.keys: Dict[str, float] = {}  # "range|by" -> last interest
        self.pushed_minute = None
        self.last_hash = None
        self.kick = threading.Event()
        self.stopped = threading.Event()
        self.thread = None
        self.pushes = 0

    def note(self, keys=()) -> None:
        now = self.clock()
        with self.lock:
            self.interest_at = now
            for r, by in keys:
                self.keys[series_key(r, by)] = now
            for k, at in list(self.keys.items()):
                if now - at >= INTEREST_S:
                    self.keys.pop(k)
            while len(self.keys) > MAX_SERIES_KEYS:
                self.keys.pop(min(self.keys, key=self.keys.get))
            start = (not self.stopped.is_set() and self.can_push()
                     and (self.thread is None or not self.thread.is_alive()))
            if start:
                self.thread = threading.Thread(target=self.run, name="throughput-push", daemon=True)
                self.thread.start()

    def interested(self) -> bool:
        return self.clock() - self.interest_at < INTEREST_S

    @staticmethod
    def can_push() -> bool:
        """Only a process whose gateway transport is loaded (``hermes serve``, the one the Desktop talks to) has anyone
        to push to; elsewhere (tests, a CLI importing the router) no thread starts."""
        return "tui_gateway.server" in sys.modules

    def snapshot(self) -> Dict[str, Any]:
        """The push body (also the catch-up answer). Reads record no interest (they must not keep themselves alive)."""
        with self.lock:
            keys = sorted(k for k, at in self.keys.items() if self.clock() - at < INTEREST_S)
        try:
            throttle = self.build_throttle()
        except Exception:
            _log.debug("throughput push: throttle unavailable", exc_info=True)
            throttle = None
        series = {}
        for k in keys:
            r, by = k.split("|", 1)
            try:
                series[k] = self.build_series(r, by)
            except Exception:
                _log.debug("throughput push: series %s unavailable", k, exc_info=True)
        now = self.wall()
        return {"feed": FEED_ID, "epoch": self.epoch, "at": now, "minute": int(now // 60) * 60, "interval_s": 60,
                "throttle": throttle, "series": series, "series_keys": sorted(series)}

    def push(self) -> Dict[str, Any]:
        body = json_safe(self.snapshot())
        digest = json.dumps({k: body.get(k) for k in ("throttle", "series")}, sort_keys=True)
        body["seq"] = next(self.seq)
        if len(json.dumps(body)) > PUSH_MAX_BYTES:
            body["series"] = {}  # names stay in series_keys: clients read those views themselves
        self.last_hash = digest
        self.emit(body)
        self.pushes += 1
        return body

    def step(self, kicked: bool) -> bool:
        """One decision: push when the minute's advisory flight finished (``kicked``) or this minute has no push yet
        at ``PUSH_DEADLINE_S``. True when it pushed."""
        if not self.interested():
            return False
        wall = self.wall()
        minute = int(wall // 60)
        due = kicked or (self.pushed_minute != minute and wall - minute * 60 >= PUSH_DEADLINE_S) \
            or self.pushed_minute is None
        if not due:
            return False
        try:
            self.push()
        except Exception:  # no transport (a CLI-imported router) or a broken build: the next minute tries again
            _log.debug("throughput push failed", exc_info=True)
        self.pushed_minute = minute
        return True

    def wait_s(self) -> float:
        if self.pushed_minute is None:
            return 0.0  # interest just started: push at once
        wall = self.wall()
        minute = int(wall // 60) * 60
        target = minute + PUSH_DEADLINE_S if self.pushed_minute != minute // 60 else minute + 60 + PUSH_DEADLINE_S
        return max(0.5, min(target - wall, INTEREST_S))

    def run(self) -> None:
        while not self.stopped.is_set():
            if not self.interested():
                with self.lock:
                    if not self.interested():
                        self.thread = None
                        return
            kicked = self.kick.wait(self.wait_s())
            self.kick.clear()
            if self.stopped.is_set():
                return
            self.step(kicked)

    def on_flight(self, root, ok) -> None:
        if root == str(_state.get("root")):
            self.kick.set()

    def stop(self) -> None:
        self.stopped.set()
        self.kick.set()


_pusher_lock = threading.Lock()
_pusher: Dict[str, Any] = {}


def _emit(body: Dict[str, Any]) -> None:
    from hermes_cli.plugin_events import broadcast_plugin_event
    broadcast_plugin_event(PLUGIN_ID, PUSH_EVENT, body)


def pusher() -> Pusher:
    with _pusher_lock:
        p = _pusher.get("p")
        if p is None or p.stopped.is_set():
            import importlib
            _services()
            advisory = importlib.import_module(pkg().__name__ + ".advisory")
            p = Pusher(_emit, lambda: throttle_payload(interest=False), cached)
            p.off = advisory.subscribe(p.on_flight)
            _pusher["p"] = p
        return p


def stop_pusher() -> None:
    with _pusher_lock:
        p = _pusher.pop("p", None)
    if p is not None:
        p.stop()
        off = getattr(p, "off", None)
        if callable(off):
            off()


def note_interest(keys=()) -> None:
    try:
        pusher().note(keys)
    except Exception:  # interest is best effort: the route still answers
        _log.debug("throughput push: interest not recorded", exc_info=True)


@router.post("/v1/watch")
async def watch(body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    keys = []
    for item in (body or {}).get("series") or []:
        if isinstance(item, dict) and item.get("range") in ("1h", "6h", "24h", "7d") and item.get("by", "gateway") in BYS:
            keys.append((item["range"], item.get("by", "gateway")))
    p = pusher()
    p.note(keys[:MAX_SERIES_KEYS])
    # ``advisory: true`` (a client that shows slot limits) keeps the per-minute slots advisory alive, like a GET
    # /v1/throttle; a client that only charts series does not.
    advisory = bool((body or {}).get("advisory"))
    snap = await asyncio.to_thread(lambda: {**p.snapshot(), "throttle": throttle_payload(interest=advisory)})
    return json_safe({**snap, "seq": None, "interest_s": INTEREST_S})
