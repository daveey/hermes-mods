"""/api/plugins/hub-sync — the local hermes-hub replica, for the Desktop (see ../hubview.py, ../README.md).

All reads are local (hub.db on this gateway) and work with the hub down. Without hermes-hub installed every
GET answers 200 with ``available: false`` and empty lists.

GET  /status                               {available, gateway, synced_at, stale, gateways:[{gateway, local, seen_at,
                                            synced_at, profiles, boards}], last_error, requests_pending, ...}
GET  /sessions[?gateway=g&exclude=a,b&archived=1&limit=N]   {sessions:[row + gateway/key/version/local/stale/
                                            synced_at/pending]}
GET  /inbox[?gateway=g&exclude=a,b&all=1]   {items:[...]}   (open/queuing cards unless all=1)
GET  /tasks[?gateway=g&exclude=a,b&board=b&all=1]           {tasks:[...]}
GET  /where/{session_id}                   {hits:[{gateway, profile, session, ref, title, local, stale, synced_at}]}
GET  /session-links/{session_id}[?ids=a,b] {tasks:[{id, title, status, board, via, gateway, local, stale, ...}]}
GET  /requests                             {requests:[...]}  (what this gateway asked others to change)
POST /request  {gateway, coll, key, action, args?, base_version?} -> {id, queued_locally, state}
                                            400 bad action/args, 404 unknown gateway, 503 hermes-hub missing
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.util
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException

router = APIRouter()
_VIEW = Path(__file__).resolve().parents[1] / "hubview.py"


def _hv():
    key = "_hermes_hub_sync_view_%s" % int(_VIEW.stat().st_mtime_ns)
    mod = sys.modules.get(key)
    if mod is None:
        spec = importlib.util.spec_from_file_location(key, _VIEW)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod = sys.modules.setdefault(key, mod)
    return mod


def _split(v: Optional[str]):
    return [x.strip() for x in (v or "").split(",") if x.strip()]


# Desktop polls these every few seconds and times out at 4s. One slow sqlite+JSON
# decode must not make every overlapping poll wait it out: return the last snapshot
# for this key while a refresh is in flight.
_snapshots: Dict[Any, Any] = {}
_flights: Dict[Any, asyncio.Task] = {}
_POLL_WAIT_S = 0.75
# Longest a poll with no snapshot waits for its first read (Desktop times out at 4 s).
_COLD_WAIT_S = 2.0
WARMING: Dict[str, Any] = {"available": False, "warming": True}

# The reads run on hub-sync's own pool, not asyncio's shared default executor. At backend boot
# other work fills that executor (min(32, cpu+4) threads, ~370 core call sites), and the
# Desktop's first cold polls, which have no snapshot to fall back on, queued past the 4 s timeout
# even though each read takes under 0.1 s.
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="hub-sync")
_SLOW_LOG_S = 1.0
_BOOT_LOG_S = 60.0
_LOADED_AT = time.monotonic()
log = logging.getLogger(__name__)


def _default_backlog(loop) -> Any:
    ex = getattr(loop, "_default_executor", None)
    q = getattr(ex, "_work_queue", None)
    try:
        return q.qsize() if q is not None else None
    except Exception:  # noqa: BLE001
        return None


async def _run_timed(key, fn):
    loop = asyncio.get_running_loop()
    entered = time.monotonic()
    started = {}

    def call():
        started["t"] = time.monotonic()
        return fn()

    try:
        return await loop.run_in_executor(_EXECUTOR, call)
    finally:
        done = time.monotonic()
        if entered - _LOADED_AT < _BOOT_LOG_S:
            # Boot window: entry time vs. the Desktop's send time tells a blocked loop from a slow read.
            wall = time.time() - (done - entered)
            log.warning("hub-sync boot poll key=%r entered=%s.%03d total=%.2fs default_backlog=%s",
                        key, time.strftime("%H:%M:%S", time.localtime(wall)), int(wall * 1000) % 1000,
                        done - entered, _default_backlog(loop))
        if done - entered >= _SLOW_LOG_S:
            t0 = started.get("t", done)
            log.warning("hub-sync slow poll key=%r total=%.2fs queue=%.2fs run=%.2fs default_backlog=%s",
                        key, done - entered, t0 - entered, done - t0, _default_backlog(loop))


async def _cold_answer(flight: asyncio.Task):
    """A key with no snapshot yet waits at most ``_COLD_WAIT_S`` for its first read, then answers
    ``WARMING`` while the read finishes in the background and fills the snapshot. At backend boot
    the first cold reads ran 6.5 s (hubview's decode cache built under boot CPU contention), past
    the Desktop's 4 s timeout. The Desktop treats ``available: false`` as "no answer" and re-polls."""
    try:
        return await asyncio.wait_for(asyncio.shield(flight), timeout=_COLD_WAIT_S)
    except asyncio.TimeoutError:
        return dict(WARMING)


async def _poll(key, fn):
    flight = _flights.get(key)
    last = _snapshots.get(key)
    if flight is not None and not flight.done():
        return last if last is not None else await _cold_answer(flight)

    async def run():
        try:
            result = await _run_timed(key, fn)
            _snapshots[key] = result
            return result
        finally:
            _flights.pop(key, None)

    task = asyncio.create_task(run())
    _flights[key] = task
    if last is None:
        return await _cold_answer(task)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=_POLL_WAIT_S)
    except asyncio.TimeoutError:
        return last


@router.get("/ping")
async def ping() -> Dict[str, Any]:
    return {"ok": True, "api": 1}


@router.get("/status")
async def get_status() -> Dict[str, Any]:
    return await _poll("status", lambda: _hv().status())


@router.get("/sessions")
async def get_sessions(gateway: Optional[str] = None, exclude: Optional[str] = None, archived: int = 0,
                       limit: int = 500) -> Dict[str, Any]:
    excluded = _split(exclude)
    key = ("sessions", gateway, tuple(excluded), bool(archived), limit)
    return await _poll(key, lambda: _hv().sessions(gateway, excluded, bool(archived), limit))


@router.get("/inbox")
async def get_inbox(gateway: Optional[str] = None, exclude: Optional[str] = None, all: int = 0) -> Dict[str, Any]:  # noqa: A002
    excluded = _split(exclude)
    key = ("inbox", gateway, tuple(excluded), bool(all))
    return await _poll(key, lambda: _hv().inbox(gateway, excluded, bool(all)))


@router.get("/projects")
async def get_projects(gateway: Optional[str] = None, exclude: Optional[str] = None, preview_limit: int = 3) -> Dict[str, Any]:
    excluded = _split(exclude)
    preview = max(0, min(preview_limit, 50))
    key = ("projects", gateway, tuple(excluded), preview)
    return await _poll(key, lambda: _hv().projects(gateway, excluded, preview))


@router.get("/tasks")
async def get_tasks(gateway: Optional[str] = None, exclude: Optional[str] = None, board: Optional[str] = None,
                    all: int = 0) -> Dict[str, Any]:  # noqa: A002
    excluded = _split(exclude)
    key = ("tasks", gateway, tuple(excluded), board, bool(all))
    return await _poll(key, lambda: _hv().tasks(gateway, excluded, board, bool(all)))


@router.get("/where/{session_id}")
async def get_where(session_id: str) -> Dict[str, Any]:
    return await _poll(("where", session_id), lambda: _hv().where(session_id))


@router.get("/session-links/{session_id}")
async def get_session_links(session_id: str, ids: Optional[str] = None, all: int = 0,
                            origin_gateway: Optional[str] = None,
                            origin_profile: Optional[str] = None) -> Dict[str, Any]:  # noqa: A002
    id_list = [session_id] + _split(ids)
    key = ("session-links", tuple(id_list), bool(all), origin_gateway, origin_profile)
    return await _poll(key, lambda: _hv().session_links(id_list, bool(all), origin_gateway, origin_profile))


@router.get("/requests")
async def get_requests(limit: int = 50) -> Dict[str, Any]:
    return await _poll(("requests", limit), lambda: _hv().requests(limit))


@router.post("/request")
async def post_request(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    hv = _hv()
    try:
        return await asyncio.get_running_loop().run_in_executor(_EXECUTOR, hv.request, body)
    except hv.RequestError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
