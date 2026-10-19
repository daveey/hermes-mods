"""/api/plugins/inbox — REST for the Desktop Inbox section (see ../store.py).

GET  /ping                      {ok, api}
GET  /items                     {items:[...], profile}
POST /items                     {title, body, accept_prompt, key?, poster?, cwd?} -> {session_id, updated}
GET  /items/{sid}               item (any state) or 404
POST /items/{sid}/accept        un-hide -> Recents; returns {accept_prompt, ...}
POST /items/{sid}/archive       archive the session
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException

router = APIRouter()
_STORE = Path(__file__).resolve().parents[1] / "store.py"


def _store():
    key = f"_hermes_inbox_store_{int(_STORE.stat().st_mtime_ns)}"
    mod = sys.modules.get(key)
    if mod is None:
        spec = importlib.util.spec_from_file_location(key, _STORE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[key] = mod
    return mod


def _profile() -> str:
    try:
        from hermes_cli.profiles import current_profile_name
        return current_profile_name("default") or "default"
    except Exception:
        return "default"


def _public(item: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("session_id", "key", "title", "poster", "accept_prompt", "created_at", "updated_at", "state", "unread")
    return {k: item.get(k) for k in keep if k in item}


@router.get("/ping")
async def ping() -> Dict[str, Any]:
    return {"ok": True, "api": 1}


@router.get("/items")
async def items() -> Dict[str, Any]:
    rows = await asyncio.to_thread(lambda: _store().Inbox().list())
    return {"items": [_public(r) for r in rows], "profile": _profile()}


@router.post("/items")
async def post(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(lambda: _store().Inbox().post(
            payload.get("title", ""), payload.get("body", ""), payload.get("accept_prompt", ""),
            key=payload.get("key"), poster=payload.get("poster"), cwd=payload.get("cwd")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/items/{session_id}")
async def get(session_id: str) -> Dict[str, Any]:
    item: Optional[Dict[str, Any]] = await asyncio.to_thread(lambda: _store().Inbox().get(session_id))
    if not item:
        raise HTTPException(status_code=404, detail="not an inbox message")
    return _public(item)


@router.post("/items/{session_id}/accept")
async def accept(session_id: str) -> Dict[str, Any]:
    item = await asyncio.to_thread(lambda: _store().Inbox().accept(session_id))
    if not item:
        raise HTTPException(status_code=404, detail="not an open inbox message")
    return _public(item)


@router.post("/items/{session_id}/archive")
async def archive(session_id: str) -> Dict[str, Any]:
    ok = await asyncio.to_thread(lambda: _store().Inbox().archive(session_id))
    if not ok:
        raise HTTPException(status_code=404, detail="not an inbox message")
    return {"ok": True}
