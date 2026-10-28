"""GET  /api/plugins/session-mentions/outbox[?home=1&courier=<device>&reach=a,b&aliases=x,y]
                                                    -> {"requests": [...]}  (claims the ones this courier may serve)
POST /api/plugins/session-mentions/result  {id, result}  -> {"ok": true}
GET  /api/plugins/session-mentions/ping    -> {"ok": true, "home_courier": bool, "forwarding": true, "targeting": true,
                                                "device": "<this machine's courier name>", "aliases": [...],
                                                "gateway": "<hermes-hub registry name, '' if none>"}
POST /api/plugins/session-mentions/enqueue {request}     -> {"ok": bool}  (forwarded by another Desktop)
GET  /api/plugins/session-mentions/status/{id}           -> {"state": pending|claimed|done|unknown, "result"?}
POST /api/plugins/session-mentions/cancel  {id}          -> {"ok": bool}  (only while unclaimed)

``home=1`` marks this gateway's own Desktop courier. Any other drainer (another Mac's Desktop,
reaching this gateway through a separate serve process) gets nothing while the home courier is
alive — see relay_files.py for why (and for the targeted-request claim rule).

Routes are mounted once per backend, so they stay thin: every call goes through ``_rf()``, which
re-executes relay_files.py whenever the file changes, so protocol fixes land without a restart.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Body, Request

router = APIRouter()


def _rf():
    name = "_session_mentions_relay_files"
    path = Path(__file__).resolve().parent.parent / "relay_files.py"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    mod = sys.modules.get(name)
    if mod is None or getattr(mod, "_sm_mtime", None) != mtime:  # new file on disk (or an older cached copy)
        spec = importlib.util.spec_from_file_location(name, path)
        fresh = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(fresh)  # type: ignore[union-attr]
        fresh._sm_mtime = mtime
        sys.modules[name] = mod = fresh
    return mod


@router.get("/ping")
async def ping() -> Dict[str, Any]:
    rf = _rf()
    info = rf.device_info() if hasattr(rf, "device_info") else {}
    return {"ok": True, "home_courier": bool(rf.home_courier_alive()), "forwarding": True, "targeting": True,
            "gateway": "", **info}


@router.get("/outbox")
async def outbox(request: Request) -> Dict[str, Any]:
    q = request.query_params
    home = str(q.get("home") or "") in ("1", "true")
    rf = _rf()
    if "courier" not in q:  # legacy courier
        return {"requests": rf.claim_pending(home=home)}
    return {"requests": rf.claim_pending(home=home, courier=q.get("courier"), reach=q.get("reach") or "",
                                         aliases=q.get("aliases") or "")}


@router.post("/result")
async def result(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    res = payload.get("result")
    if not isinstance(res, dict):
        res = {"ok": False, "error": "courier posted no result"}
    return {"ok": bool(_rf().write_result(payload.get("id"), res))}


@router.post("/enqueue")
async def enqueue(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    rf = _rf()
    req = payload.get("request")
    if isinstance(req, dict) and req.get("action") == "send" and hasattr(rf, "worker_send_refusal"):
        sender = req.get("sender")
        args = req.get("args")
        sender_task = sender.get("kanban_task") if isinstance(sender, dict) else ""
        target = args.get("target") if isinstance(args, dict) else None
        err = rf.worker_send_refusal(target, kanban_task=str(sender_task or ""))
        if err:
            return {"ok": False, "error": err}
    return {"ok": bool(rf.enqueue(req))}


@router.get("/status/{req_id}")
async def status(req_id: str) -> Dict[str, Any]:
    return _rf().request_status(req_id)


@router.post("/cancel")
async def cancel(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    return {"ok": bool(_rf().cancel(payload.get("id")))}
