"""GET  /api/plugins/session-mentions/outbox[?home=1] -> {"requests": [...]}  (claims them)
POST /api/plugins/session-mentions/result  {id, result}  -> {"ok": true}
GET  /api/plugins/session-mentions/ping    -> {"ok": true, "home_courier": bool, "forwarding": true}
POST /api/plugins/session-mentions/enqueue {request}     -> {"ok": bool}  (forwarded by another Desktop)
GET  /api/plugins/session-mentions/status/{id}           -> {"state": pending|claimed|done|unknown, "result"?}
POST /api/plugins/session-mentions/cancel  {id}          -> {"ok": bool}  (only while unclaimed)

``home=1`` marks this gateway's own Desktop courier. Any other drainer (another Mac's Desktop,
reaching this gateway through a separate serve process) gets nothing while the home courier is
alive — see relay_files.py for why.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Body

router = APIRouter()


def _rf():
    name = "_session_mentions_relay_files"
    mod = sys.modules.get(name)
    if mod is None or not hasattr(mod, "enqueue"):  # an older copy may be cached in this process
        path = Path(__file__).resolve().parent.parent / "relay_files.py"
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        sys.modules[name] = mod
    return mod


@router.get("/ping")
async def ping() -> Dict[str, Any]:
    return {"ok": True, "home_courier": bool(_rf().home_courier_alive()), "forwarding": True}


@router.get("/outbox")
async def outbox(home: int = 0) -> Dict[str, Any]:
    return {"requests": _rf().claim_pending(home=bool(home))}


@router.post("/result")
async def result(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    res = payload.get("result")
    if not isinstance(res, dict):
        res = {"ok": False, "error": "courier posted no result"}
    return {"ok": bool(_rf().write_result(payload.get("id"), res))}


@router.post("/enqueue")
async def enqueue(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    return {"ok": bool(_rf().enqueue(payload.get("request")))}


@router.get("/status/{req_id}")
async def status(req_id: str) -> Dict[str, Any]:
    return _rf().request_status(req_id)


@router.post("/cancel")
async def cancel(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    return {"ok": bool(_rf().cancel(payload.get("id")))}
