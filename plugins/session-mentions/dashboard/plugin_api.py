"""GET  /api/plugins/session-mentions/outbox  -> {"requests": [...]}  (claims them)
POST /api/plugins/session-mentions/result  {id, result}  -> {"ok": true}
GET  /api/plugins/session-mentions/ping    -> {"ok": true}
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
    if mod is None:
        path = Path(__file__).resolve().parent.parent / "relay_files.py"
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        sys.modules[name] = mod
    return mod


@router.get("/ping")
async def ping() -> Dict[str, Any]:
    return {"ok": True}


@router.get("/outbox")
async def outbox() -> Dict[str, Any]:
    return {"requests": _rf().claim_pending()}


@router.post("/result")
async def result(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    res = payload.get("result")
    if not isinstance(res, dict):
        res = {"ok": False, "error": "courier posted no result"}
    return {"ok": bool(_rf().write_result(payload.get("id"), res))}
