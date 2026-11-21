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
process that serves the Desktop) checks the outbox every WATCH_TICK_S (see ``watch_once``): it pushes for a request
it has not seen — that covers writers in other processes (user-terminal, coach, ``hermes gateway run`` turns) —
re-pushes every REPUSH_S while an unclaimed request is younger than REPUSH_WINDOW_S (a push lost on a socket
reconnect), and sends {feed, seq, heartbeat: true, pending} every HEARTBEAT_S, which tells the Desktop that this
gateway's pushes reach it and whether anything waits. Plugin unload stops it (``stop`` / ``__init__.stop_watcher``).

``home=1`` marks this gateway's own Desktop courier. Any other drainer (another Mac's Desktop,
reaching this gateway through a separate serve process) gets nothing while the home courier is
alive — see relay_files.py for why (and for the targeted-request claim rule).

Routes are mounted once per backend, so they stay thin: every call goes through ``_rf()``, which
re-executes relay_files.py whenever the file changes, so protocol fixes land without a restart.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Body, Request

router = APIRouter()

WATCH_TICK_S = 1.0
HEARTBEAT_S = 20.0
REPUSH_S = 5.0          # re-push an unclaimed request this often ...
REPUSH_WINDOW_S = 30.0  # ... while it is younger than this (the tools' pickup timeouts are 15 s / 30 s)
SHARED_KEY = "_session_mentions_outbox_watch"
# Off only for test processes (tests/conftest.py), which drive watch_once() directly instead of a thread.
WATCH_ENABLED = os.environ.get("HERMES_SESSION_MENTIONS_OUTBOX_WATCH", "1") != "0"
_WATCH_LOCK = threading.Lock()
log = logging.getLogger("session-mentions")


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


def _pending(outbox) -> Dict[str, float]:
    """Unclaimed requests in the outbox: name -> mtime (a request claimed or withdrawn meanwhile is left out)."""
    out: Dict[str, float] = {}
    for p in outbox.glob("*.json"):
        try:
            out[p.name] = p.stat().st_mtime
        except OSError:
            continue
    return out


def watch_once(state: dict, now: float) -> str:
    """One watcher step; emits and returns what it sent: '' (nothing), 'outbox' (a request it had not seen),
    'repush' (a still-unclaimed request younger than REPUSH_WINDOW_S, every REPUSH_S: a push the Desktop never got
    is re-sent) or 'heartbeat' (every HEARTBEAT_S, with ``pending`` from the dir listing). Never raises.

    The dir's stat key only lets a step skip the listing when nothing can have been missed: the key is trusted
    only once the dir is DIR_SETTLE_SECONDS old (a coarse or racing timestamp — a temp-file create and its rename
    in one tick — can leave the key unchanged after a request lands), and the dir is listed every step while it
    is unsettled, holds a young request, or a heartbeat is due. New requests are found by name, not by the key."""
    try:
        rf = _rf()
        base = rf.root()
        outbox = base / "outbox"
        key = _outbox_key(outbox)
        settle = float(getattr(rf, "DIR_SETTLE_SECONDS", 2.0))
        settled = key is not None and now - max(key[2], key[3]) / 1e9 >= settle
        beat_due = now - state.get("beat_at", 0.0) >= HEARTBEAT_S
        if key == state.get("key") and settled and not state.get("young") and not beat_due:
            return ""
        files = _pending(outbox)
        state["key"] = key if settled else None  # an unsettled dir is listed again next step
        state["young"] = any(now - m < REPUSH_WINDOW_S for m in files.values())
        seen = state.get("seen", set())
        state["seen"] = set(files)
        if set(files) - seen:
            rf.notify_outbox(base)
            state["push_at"] = now
            return "outbox"
        if state["young"] and now - state.get("push_at", 0.0) >= REPUSH_S:
            rf.notify_outbox(base)
            state["push_at"] = now
            return "repush"
        if beat_due:
            rf.notify_outbox(base, heartbeat=True, pending=bool(files))
            state["beat_at"] = now
            return "heartbeat"
    except Exception:
        log.debug("session-mentions outbox watch step failed", exc_info=True)
    return ""


def _shared():
    """Watcher state shared with the plugin module (``__init__.stop_watcher``), which is a separate import."""
    mod = sys.modules.get(SHARED_KEY)
    if mod is None:
        mod = types.ModuleType(SHARED_KEY)
        mod.thread = None
        mod.stop = threading.Event()
        mod.disabled = False
        sys.modules[SHARED_KEY] = mod
    return mod


def _watch(stop: threading.Event) -> None:
    state: dict = {}
    while not stop.is_set():
        watch_once(state, time.time())
        stop.wait(WATCH_TICK_S)


def ensure_watcher() -> bool:
    """Start this process's outbox watcher once (the process serving the Desktop). True when it started now.
    Not while the plugin is unloaded (``stop()``): its routes stay mounted, but nothing restarts it."""
    if not WATCH_ENABLED:
        return False
    with _WATCH_LOCK:
        sh = _shared()
        if sh.disabled or (sh.thread is not None and sh.thread.is_alive() and not sh.stop.is_set()):
            return False
        sh.stop = threading.Event()
        sh.thread = threading.Thread(target=_watch, args=(sh.stop,), name="session-mentions-outbox-watch", daemon=True)
        sh.thread.start()
        return True


def stop(join_s: float = 0.0) -> None:
    """Stop the watcher (plugin unload / disable) and keep it stopped until the plugin registers again."""
    sh = _shared()
    sh.disabled = True
    sh.stop.set()
    if join_s and sh.thread is not None:
        sh.thread.join(join_s)


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
