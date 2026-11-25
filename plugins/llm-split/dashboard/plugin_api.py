"""GET /api/plugins/llm-split/v1/split: ``core.snapshot`` (pools, quota share, traffic share + weights, mode).
PUT /api/plugins/llm-split/v1/split body ``{"mode": "auto"}`` or ``{"mode": "manual", "share": {pool: number, ...}}``:
writes the weights to every Hermes home on this host, answers the new snapshot; 400 on a bad body.

Loaded by path by the dashboard, outside its package: core.py is imported under a private name.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException

router = APIRouter()
_lock = threading.Lock()
NAME = "hermes_llm_split_core"


def core():
    with _lock:
        mod = sys.modules.get(NAME)
        if mod is None:
            spec = importlib.util.spec_from_file_location(NAME, Path(__file__).resolve().parents[1] / "core.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod = sys.modules.setdefault(NAME, mod)
        return mod


@router.get("/ping")
def ping() -> Dict[str, Any]:
    return {"ok": True, "api": 1}


@router.get("/v1/split")
async def get_split() -> Dict[str, Any]:
    c = core()
    return await asyncio.to_thread(c.snapshot, c.hermes_root())


@router.put("/v1/split")
async def put_split(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    c = core()
    mode = body.get("mode")
    share = body.get("share")
    try:
        return await asyncio.to_thread(c.apply, c.hermes_root(), mode, share)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
