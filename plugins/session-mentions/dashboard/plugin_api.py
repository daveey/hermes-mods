"""GET  /api/plugins/session-mentions/outbox[?home=1&courier=<device>&reach=a,b&aliases=x,y]
                                                    -> {"requests": [...]}  (claims the ones this courier may serve)
POST /api/plugins/session-mentions/result  {id, result}  -> {"ok": true}
GET  /api/plugins/session-mentions/ping[?home=1&courier=<device>&reach=..&aliases=..&plugin=..]
                                         -> {"ok": true, "home_courier": bool, "forwarding": true, "targeting": true,
                                                "device": "<this machine's courier name>", "aliases": [...],
                                                "gateway": "<hermes-hub registry name, '' if none>", "feed": "<id>",
                                                "push": true}
                                            With ``courier`` it also refreshes couriers/<device>.json (the courier's
                                            liveness heartbeat; claims nothing).
POST /api/plugins/session-mentions/enqueue {request}     -> {"ok": bool}  (forwarded by another Desktop)
GET  /api/plugins/session-mentions/status/{id}           -> {"state": pending|claimed|done|unknown, "result"?}
POST /api/plugins/session-mentions/cancel  {id}          -> {"ok": bool}  (only while unclaimed)

Push: any write to the outbox emits ``plugin.session-mentions.outbox`` {feed, seq} on this process's event stream
(``relay_files.notify_outbox``), and a watcher thread started by the first /ping or /outbox (so it runs in the
process that serves the Desktop) stats the outbox dir every WATCH_TICK_S and emits when it changed and holds a
request — that covers writers in other processes (user-terminal, coach, ``hermes gateway run`` turns) — plus a
{feed, seq, heartbeat: true} every HEARTBEAT_S, which tells the Desktop that this gateway's pushes reach it.

``home=1`` marks this gateway's own Desktop courier. Any other drainer (another Mac's Desktop,
reaching this gateway through a separate serve process) gets nothing while the home courier is
alive — see relay_files.py for why (and for the targeted-request claim rule).

Routes are mounted once per backend, so they stay thin: every call goes through ``_rf()``, which
re-executes relay_files.py whenever the file changes, so protocol fixes land without a restart.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Body, Request

router = APIRouter()

WATCH_TICK_S = 1.0
HEARTBEAT_S = 20.0
# Off only for test processes (tests/conftest.py), which drive watch_once() directly instead of a thread.
WATCH_ENABLED = os.environ.get("HERMES_SESSION_MENTIONS_OUTBOX_WATCH", "1") != "0"
_WATCH_LOCK = threading.Lock()
_WATCHER: list = []  # [thread] once started in this process


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


def _outbox_key(path) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns)


def watch_once(state: dict, now: float) -> str:
    """One watcher step: '' (nothing), 'outbox' (dir changed and holds a request) or 'heartbeat'. Emits it.
    ``state`` carries {key, beat_at} between steps. Never raises."""
    try:
        rf = _rf()
        base = rf.root()
        key = _outbox_key(base / "outbox")
        if key is not None and key != state.get("key"):
            state["key"] = key
            if any((base / "outbox").glob("*.json")):
                rf.notify_outbox(base)
                state["beat_at"] = now
                return "outbox"
        if now - state.get("beat_at", 0.0) >= HEARTBEAT_S:
            rf.notify_outbox(base, heartbeat=True)
            state["beat_at"] = now
            return "heartbeat"
    except Exception:
        pass
    return ""


def _watch() -> None:
    state: dict = {}
    while True:
        watch_once(state, time.time())
        time.sleep(WATCH_TICK_S)


def ensure_watcher() -> bool:
    """Start this process's outbox watcher once (the process serving the Desktop). True when it started now."""
    if not WATCH_ENABLED:
        return False
    with _WATCH_LOCK:
        if _WATCHER and _WATCHER[0].is_alive():
            return False
        t = threading.Thread(target=_watch, name="session-mentions-outbox-watch", daemon=True)
        t.start()
        _WATCHER[:] = [t]
        return True


def _courier_args(rf, q) -> Dict[str, Any]:
    extra: Dict[str, Any] = {}
    if hasattr(rf, "plugin_stamp"):  # relay_files reloads on mtime; these routes are mounted once
        extra["plugin"] = rf.plugin_stamp(q.get("plugin"), q.get("plugin_disk"), q.get("plugin_at"))
    return {"courier": q.get("courier"), "reach": q.get("reach") or "", "aliases": q.get("aliases") or "", **extra}


@router.get("/ping")
async def ping(request: Request) -> Dict[str, Any]:
    ensure_watcher()
    rf = _rf()
    q = request.query_params
    info = rf.device_info() if hasattr(rf, "device_info") else {}
    if "courier" in q and hasattr(rf, "register_courier"):
        # The courier's liveness heartbeat between drains: refresh its record, claim nothing.
        a = _courier_args(rf, q)
        rf.register_courier(rf.root(), a["courier"], a["aliases"], a["reach"],
                            str(q.get("home") or "") in ("1", "true"), a.get("plugin"))
    feed = rf.feed_id() if hasattr(rf, "feed_id") else ""
    return {"ok": True, "home_courier": bool(rf.home_courier_alive()), "forwarding": True, "targeting": True,
            "gateway": "", **info, "feed": feed, "push": bool(feed)}


@router.get("/outbox")
async def outbox(request: Request) -> Dict[str, Any]:
    ensure_watcher()
    q = request.query_params
    home = str(q.get("home") or "") in ("1", "true")
    rf = _rf()
    feed = {"feed": rf.feed_id()} if hasattr(rf, "feed_id") else {}
    if "courier" not in q:  # legacy courier
        return {"requests": rf.claim_pending(home=home), **feed}
    return {"requests": rf.claim_pending(home=home, **_courier_args(rf, q)), **feed}


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
