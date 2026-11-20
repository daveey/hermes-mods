"""GET /api/plugins/throughput/v1/series?range=1h|6h|24h|7d[&by=gateway|model] (see ../series.py for the payload).
GET / PUT /api/plugins/throughput/v1/throttle: the fleet's token throttle level (../throttle.py) and the
subscription pools' runway (../runway.py). PUT body ``{"percent": 0..100}``.
PUT / DELETE /api/plugins/throughput/v1/throttle/burst: start a burst on the budget level (body ``{"factor": 1 < f <= 4,
"hours": 0 < h <= 24}``; 409 on a manual or expired level) or end it now.
GET /api/plugins/throughput/v1/budget[?factor=&hours=]: this host's capacity model, the measured spend per provider
over 7 days and the current burst, plus the cost of the given burst (../budget_view.py).

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
    return await asyncio.to_thread(cached, range, by)


def throttle_payload(now: float | None = None) -> Dict[str, Any]:
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
    out = throttle.status(root, sampler.series_path(root), now, hub_db)
    reg = _registry()
    out["scope"] = (reg or {}).get("me") or _state["store"].get_meta("me") or "local"
    out["fleet"] = hub_db.is_file()
    out["gateways"] = throttle.gateways(root, hub_db, now, out["scope"])
    out["pools"] = runway.pools(runway.pacing_path(root), now)
    try:  # advisory (the Runners panel crosses out slots a limit will not let run): never fail the route
        slots = importlib.import_module(pkg().__name__ + ".slots")
        out["slots"] = slots.payload(root, sampler.series_path(root), out["cap_per_s"], cards=_state["cards"],
                                     now=now)
    except Exception:
        out["slots"] = None
    out["now"] = now
    return out


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
    return await asyncio.to_thread(throttle_payload)


@router.put("/v1/throttle")
async def put_throttle(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
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


def budget_payload(factor: float | None = None, hours: float | None = None, now: float | None = None) -> Dict[str, Any]:
    """``{now, computed_at, margin_s, global_rate_tokens_per_s, pools, events, curve, history, burst, projection}``:
    one read of this host's ``llm_pacing.json`` and one 7-day slice of the series. ``projection`` is the cost of a
    ``factor`` x burst for ``hours`` (``budget_view.project``), or None when no burst was asked about."""
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
    out: Dict[str, Any] = {"now": now, **view.chart_v2(v2)}
    out["history"] = view.history(sampler.series_path(root), now)
    out["burst"] = throttle.burst_status(throttle.effective(root, throttle.hub_db_path(), now), now)
    out["projection"] = None
    if factor is not None:
        out["projection"] = {"factor": factor, "hours": hours, "pools": view.project(v2, factor, hours * 3600.0, now)}
    return out


@router.get("/v1/budget")
async def get_budget(factor: float | None = Query(None), hours: float | None = Query(None)) -> Dict[str, Any]:
    if (factor is None) != (hours is None):
        raise HTTPException(status_code=400, detail="give both factor and hours, or neither")
    if factor is not None:
        try:
            factor, hours = _burst_args(factor, hours)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return await asyncio.to_thread(budget_payload, factor, hours)
