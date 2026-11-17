"""GET /api/plugins/lanes/v1/lanes?hours=6: per lane target and actual share of run-hours, running, ready, held at
the gate, open, done, tokens and estimated cost over the window (core ``kanban_lanes_cli.snapshot``).
PUT /api/plugins/lanes/v1/lanes/{lane}: body ``{"share": n, "paused": bool, "admit_per_day": n}`` (any subset).
GET /api/plugins/lanes/v1/held?lane=x: cards waiting at a lane gate. POST /api/plugins/lanes/v1/held/{card}/admit.

Every route answers ``{"configured": false, "reason": ...}`` (or 409 for a write) on a core without lanes support
or with no lanes configured, so the panel can say why it is empty. Reads are cached for 10 s per window.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query

router = APIRouter()
CACHE_S = 10
WINDOWS = (1.0, 6.0, 24.0, 168.0)
_lock = threading.Lock()
_cache: Dict[float, tuple] = {}


def core():
    """Core's lanes modules, or None on a core that predates them."""
    try:
        from hermes_cli import kanban_db_connect, kanban_lanes, kanban_lanes_cli
    except ImportError:
        return None
    return kanban_db_connect, kanban_lanes, kanban_lanes_cli


def read(hours: float, now: Optional[float] = None) -> Dict[str, Any]:
    mods = core()
    if mods is None:
        return {"configured": False, "reason": "this Hermes core has no lanes support yet"}
    now = time.time() if now is None else now
    with _lock:
        hit = _cache.get(hours)
        if hit and now - hit[0] < CACHE_S:
            return hit[1]
    kbc, _lanes, cli = mods
    try:
        with kbc.connect_closing() as conn:
            snap = cli.snapshot(conn, hours=hours)
    except ValueError as exc:
        return {"configured": False, "reason": f"lanes configuration is invalid: {exc}"}
    if not snap.get("configured"):
        snap = {"configured": False, "reason": "no lanes are configured (kanban.admission.lanes)"}
    with _lock:
        _cache[hours] = (now, snap)
    return snap


def _need_core():
    mods = core()
    if mods is None:
        raise HTTPException(status_code=409, detail="this Hermes core has no lanes support yet")
    return mods


def change(lane: str, body: Dict[str, Any]) -> Dict[str, Any]:
    _kbc, _lanes, cli = _need_core()
    unknown = sorted(set(body) - {"share", "paused", "admit_per_day"})
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(unknown)}")
    if "paused" in body and type(body["paused"]) is not bool:
        raise ValueError("paused must be true or false")
    cli.set_lane(lane, share=body.get("share"), admit_per_day=body.get("admit_per_day"), paused=body.get("paused"))
    with _lock:
        _cache.clear()
    return read(6.0)


def held(lane: Optional[str]) -> Dict[str, Any]:
    kbc, lanes, _cli = _need_core()
    with kbc.connect_closing() as conn:
        rows = lanes.held(conn, lane)[:200]
    return {"cards": [{"id": r["id"], "title": r["title"], "lane": r["tenant"], "created_at": r["created_at"]}
                      for r in rows]}


def admit(card: str, reason: str) -> Dict[str, Any]:
    kbc, lanes, _cli = _need_core()
    with kbc.connect_closing() as conn:
        done = lanes.admit(conn, card, actor="user (lanes panel)", reason=reason)
    with _lock:
        _cache.clear()
    return {"admitted": done}


@router.get("/v1/lanes")
async def get_lanes(hours: float = Query(6.0)) -> Dict[str, Any]:
    if hours not in WINDOWS:
        raise HTTPException(status_code=400, detail=f"hours must be one of {', '.join(f'{w:g}' for w in WINDOWS)}")
    return await asyncio.to_thread(read, hours)


@router.put("/v1/lanes/{lane}")
async def put_lane(lane: str, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(change, lane, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/v1/held")
async def get_held(lane: Optional[str] = Query(None)) -> Dict[str, Any]:
    return await asyncio.to_thread(held, lane)


@router.post("/v1/held/{card}/admit")
async def post_admit(card: str, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    return await asyncio.to_thread(admit, card, str(body.get("reason") or "admitted from the lanes panel"))
