"""GET /api/plugins/throughput/v1/series?range=1h|6h|24h|7d[&by=gateway|model] (see ../series.py for the payload).

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

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
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
