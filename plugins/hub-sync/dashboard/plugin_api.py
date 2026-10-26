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
import importlib.util
import sys
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


@router.get("/ping")
async def ping() -> Dict[str, Any]:
    return {"ok": True, "api": 1}


@router.get("/status")
async def get_status() -> Dict[str, Any]:
    return await asyncio.to_thread(_hv().status)


@router.get("/sessions")
async def get_sessions(gateway: Optional[str] = None, exclude: Optional[str] = None, archived: int = 0,
                       limit: int = 500) -> Dict[str, Any]:
    return await asyncio.to_thread(_hv().sessions, gateway, _split(exclude), bool(archived), limit)


@router.get("/inbox")
async def get_inbox(gateway: Optional[str] = None, exclude: Optional[str] = None, all: int = 0) -> Dict[str, Any]:  # noqa: A002
    return await asyncio.to_thread(_hv().inbox, gateway, _split(exclude), bool(all))


@router.get("/tasks")
async def get_tasks(gateway: Optional[str] = None, exclude: Optional[str] = None, board: Optional[str] = None,
                    all: int = 0) -> Dict[str, Any]:  # noqa: A002
    return await asyncio.to_thread(_hv().tasks, gateway, _split(exclude), board, bool(all))


@router.get("/where/{session_id}")
async def get_where(session_id: str) -> Dict[str, Any]:
    return await asyncio.to_thread(_hv().where, session_id)


@router.get("/session-links/{session_id}")
async def get_session_links(session_id: str, ids: Optional[str] = None, all: int = 0,
                            origin_gateway: Optional[str] = None,
                            origin_profile: Optional[str] = None) -> Dict[str, Any]:  # noqa: A002
    return await asyncio.to_thread(_hv().session_links, [session_id] + _split(ids), bool(all),
                                   origin_gateway, origin_profile)


@router.get("/requests")
async def get_requests(limit: int = 50) -> Dict[str, Any]:
    return await asyncio.to_thread(_hv().requests, limit)


@router.post("/request")
async def post_request(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    hv = _hv()
    try:
        return await asyncio.to_thread(hv.request, body)
    except hv.RequestError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
