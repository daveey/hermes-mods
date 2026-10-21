"""Mac-side routes for run_in_user_terminal jobs relayed from a gateway with no screen.

POST /api/plugins/user-terminal/start   {command, reason, cwd, origin: {gateway, profile, session_id, title}}
                                        -> {ok, job_id, status: "waiting_for_user", terminal} | {ok: false, error}
GET  /api/plugins/user-terminal/status/{job_id}?gateway=<origin gateway>&wait_s=<=25
                                        -> the local tool's result shape (status ran|declined|pending|running, ...)

Called by the session-mentions Desktop courier on THIS Mac's own backend, after it claimed a
``user_terminal`` request from another gateway's outbox. ``origin.gateway`` is the courier's name for the
connection it drained (not something the remote agent chose), and a job is only readable with that same
gateway name. Nothing runs until the owner types y in the window (runner.sh).

Routes are mounted once per backend, so they stay thin: the plugin module is re-executed whenever
``__init__.py`` changes on disk.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Body, Request

router = APIRouter()


def _ut():
    name = "_user_terminal_plugin"
    path = Path(__file__).resolve().parent.parent / "__init__.py"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    mod = sys.modules.get(name)
    if mod is None or getattr(mod, "_ut_mtime", None) != mtime:
        spec = importlib.util.spec_from_file_location(name, path)
        fresh = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(fresh)  # type: ignore[union-attr]
        fresh._ut_mtime = mtime
        mod = sys.modules[name] = fresh  # registered only after exec_module (no half-loaded module)
    return mod


@router.get("/ping")
async def ping() -> Dict[str, Any]:
    return {"ok": True, "relay": True, "screen": bool(_ut().has_screen())}


@router.post("/start")
async def start(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    ut = _ut()
    return await asyncio.to_thread(ut.start_relayed, payload.get("command"), payload.get("reason"),
                                   payload.get("cwd"), payload.get("origin"))


@router.get("/status/{job_id}")
async def status(job_id: str, request: Request) -> Dict[str, Any]:
    q = request.query_params
    try:
        wait_s = float(q.get("wait_s") or 0)
    except ValueError:
        wait_s = 0.0
    ut = _ut()
    return await asyncio.to_thread(ut.relayed_status, job_id, str(q.get("gateway") or ""), wait_s)
