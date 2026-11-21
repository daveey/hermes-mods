"""/api/plugins/inbox — REST for the Desktop Inbox section (see ../store.py, ../card_types.py).

GET  /ping                      {ok, api}   (api 3 = card types)
GET  /items                     {items:[...], profile, feed}  (root + every profile with an inbox; item.profile,
                                 item.type/type_label/fields/field_specs/actions)
POST /items                     {title, body, accept_prompt?, key?, poster?, cwd?, prompt_label?, type?, fields?}
                                 -> {session_id, updated}
GET  /items/{sid}[?profile=p]    item (any state) or 404
POST /items/{sid}/act[?profile=p]      {action, input?} run a card action; prompt actions return {prompt}
POST /items/{sid}/accept[?profile=p]   alias for act(first prompt action); returns {accept_prompt, ...};
                                 409 on a card with resolve actions (an older Desktop's Do it)
POST /items/{sid}/archive[?profile=p]  archive the session (outcome 'archived')
POST /items/{sid}/junk[?profile=p]     archive + mute the poster family + file a five-whys card
GET  /outcome?key=K[&profile=p]  {found, state, outcome, value, fields, at, session_id, type, task}
GET  /types[?profile=p]          {types:[...]}
GET  /types/{name}[?profile=p]   the resolved type (with source) or 404
PUT  /types/{name}[?profile=p]   {spec} or the spec itself -> define an ad-hoc local type

Push: `plugin.inbox.changed` {feed, seq} on the gateway event stream whenever an inbox sidecar
(`<home>/inbox/inbox.db`, plus every named profile's on the root dashboard) is written: a post, an
update of a keyed card, an act/accept/archive/junk/settle, from ANY process (agent tool, CLI, cron,
this API). A watcher thread compares the sidecar files' stat (db, -wal, -journal) every WATCH_TICK_S
and pushes once two ticks agree (a half-done write is one push); a write through this API pushes at
once. `feed` (hostname:pid) is also on every GET /items answer, so the Desktop re-reads only the
connection that changed. The watcher starts on the first GET /items, i.e. only in the process that
serves a Desktop, and keeps that request's profile context. A session archived or un-hidden through
another door (core's sidebar) touches state.db only: the Desktop's 5-minute safety poll covers that.
"""
from __future__ import annotations

import asyncio
import contextvars
import importlib.util
import logging
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException

router = APIRouter()
_STORE = Path(__file__).resolve().parents[1] / "store.py"
PLUGIN_ID = "inbox"
FEED_ID = f"{socket.gethostname()}:{os.getpid()}"
WATCH_TICK_S = float(os.environ.get("HERMES_INBOX_WATCH_TICK_S", "2"))
log = logging.getLogger("inbox.push")


def _store():
    key = f"_hermes_inbox_store_{int(_STORE.stat().st_mtime_ns)}"
    mod = sys.modules.get(key)
    if mod is None:
        spec = importlib.util.spec_from_file_location(key, _STORE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod = sys.modules.setdefault(key, mod)
    return mod


def _profile() -> str:
    try:
        from hermes_cli.profiles import current_profile_name
        return current_profile_name("default") or "default"
    except Exception:
        return "default"


_KEEP = ("session_id", "key", "title", "poster", "accept_prompt", "created_at", "updated_at", "state", "unread",
         "type", "type_label", "fields", "field_specs", "actions", "outcome", "outcome_value", "resolved_at",
         "prompt", "action", "kind", "outcome_task")


def _public(item: Dict[str, Any]) -> Dict[str, Any]:
    full = _store().public_item(item)
    return {k: full.get(k) for k in _KEEP if k in full}


def _inbox(profile: Optional[str] = None):
    st = _store()
    if not profile or profile in ("default", _profile()):
        return st.Inbox()
    try:
        return st.Inbox.for_home(st.profile_home(st._home(), profile))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _all_items() -> list:
    st = _store()
    own = _profile()
    out = [dict(_public(r), profile=own) for r in st.Inbox().list()]
    if own == "default":  # the root dashboard also lists what its named profiles posted
        for name, home in st.profile_homes(st._home())[1:]:
            try:
                out += [dict(_public(r), profile=name) for r in st.Inbox.for_home(home).list()]
            except Exception:
                continue
    return out


# ── change push ──────────────────────────────────────────────────────────────

def _sidecar_paths() -> list:
    """The sidecars GET /items reads: this profile's, plus every named profile's on the root dashboard."""
    st = _store()
    paths = [st.Inbox().path]
    if _profile() == "default":
        paths += [home / "inbox" / "inbox.db" for _, home in st.profile_homes(st._home())[1:]]
    return paths


def _signature(paths) -> tuple:
    """(path, mtime_ns, size) of each sidecar and its -wal / -journal; None for a missing file."""
    out = []
    for path in paths:
        for f in (Path(path), Path(f"{path}-wal"), Path(f"{path}-journal")):
            try:
                st = f.stat()
                out.append((str(f), st.st_mtime_ns, st.st_size))
            except OSError:
                out.append((str(f), None))
    return tuple(out)


def _emit(payload: Dict[str, Any]) -> None:
    from hermes_cli.plugin_events import broadcast_plugin_event
    broadcast_plugin_event(PLUGIN_ID, "changed", payload)


class _Watch:
    """Push `plugin.inbox.changed` when the sidecar signature moves. `check()` is also called right after a
    write through this API, so the Desktop does not wait for the next tick.

    The sidecar paths depend on the active profile (HERMES_HOME), a context variable of the request. `start()`
    captures the first /items request's context and every check (the thread's ticks and later requests, whatever
    profile they are scoped to) resolves the paths in a copy of it, so they always watch the same files: a tick
    never sees "different files" as a change."""

    def __init__(self, paths=_sidecar_paths, emit=None):
        self._paths, self._emit = paths, emit
        self.seq = 0
        self.sig = None
        self.lock = threading.Lock()
        self.thread = None
        self.ctx = None
        self.settling = None  # a tick's new signature, pushed once the next tick sees it unchanged
        self.stop = threading.Event()

    def _resolve(self):
        # A copy each time: one Context object cannot be entered by two threads at once.
        return self.ctx.copy().run(self._paths) if self.ctx is not None else self._paths()

    def check(self, settle: bool = False) -> bool:
        """Push if the sidecars changed. ``settle`` (the thread's ticks): a write seen from outside may be half
        done (one post commits the sidecar more than once), so push only once two ticks agree. A write through
        this API has finished when it calls check(), so it pushes at once."""
        try:
            sig = _signature(self._resolve())
        except Exception:
            log.debug("inbox watch: signature failed", exc_info=True)
            return False
        with self.lock:
            if self.sig is None or sig == self.sig:
                self.sig = sig  # the first read is the baseline: nothing changed yet
                self.settling = None
                return False
            if settle and sig != self.settling:
                self.settling = sig
                return False
            self.settling = None
            self.sig = sig
            self.seq += 1
            payload = {"feed": FEED_ID, "seq": self.seq}
        try:
            (self._emit or _emit)(payload)
        except Exception:
            log.debug("inbox watch: push failed", exc_info=True)
        return True

    def start(self) -> None:
        """Start watching from the caller's context (call it from the request, or a to_thread of it)."""
        with self.lock:
            if self.thread is not None:
                return
            self.ctx = contextvars.copy_context()
            self.thread = threading.Thread(target=self._run, name="inbox-watch", daemon=True)
        self.check()  # baseline before the first tick
        self.thread.start()

    def _run(self) -> None:
        while not self.stop.wait(WATCH_TICK_S):
            self.check(settle=True)


WATCH = _Watch()


async def _changed() -> None:
    """After a write through this API: push now (the watcher would within WATCH_TICK_S). Off the event loop:
    the check stats files and may broadcast."""
    if WATCH.thread is not None:
        await asyncio.to_thread(WATCH.check)


async def _run(fn):
    try:
        return await asyncio.to_thread(fn)
    except ValueError as exc:  # bad type / field / input
        detail = ({"reason": exc.reason, "task": exc.task, "error": str(exc)}
                  if hasattr(exc, "reason") else str(exc))
        raise HTTPException(status_code=400, detail=detail)
    except Exception as exc:
        # A task action could not queue its Kanban card (the card stays open). Matched by name: a
        # store.py reload (mtime change) between building the Inbox and here changes the class.
        if type(exc).__name__ == "TaskError":
            raise HTTPException(status_code=500, detail=str(exc))
        raise


@router.get("/ping")
async def ping() -> Dict[str, Any]:
    return {"ok": True, "api": 3}


@router.get("/items")
async def items() -> Dict[str, Any]:
    rows = await asyncio.to_thread(_all_items)
    if WATCH.thread is None:
        await asyncio.to_thread(WATCH.start)
    return {"items": rows, "profile": _profile(), "feed": FEED_ID}


@router.post("/items")
async def post(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        return await _run(lambda: _store().Inbox().post(
            payload.get("title", ""), payload.get("body", ""), payload.get("accept_prompt", ""),
            key=payload.get("key"), poster=payload.get("poster"), cwd=payload.get("cwd"),
            prompt_label=payload.get("prompt_label"), type=payload.get("type"), fields=payload.get("fields"), ask=payload.get("ask")))
    finally:
        await _changed()


@router.get("/items/{session_id}")
async def get(session_id: str, profile: Optional[str] = None) -> Dict[str, Any]:
    inbox = _inbox(profile)
    item: Optional[Dict[str, Any]] = await asyncio.to_thread(lambda: inbox.get(session_id))
    if not item:
        raise HTTPException(status_code=404, detail="not an inbox message")
    return _public(item)


@router.post("/items/{session_id}/act")
async def act(session_id: str, payload: Dict[str, Any] = Body(...), profile: Optional[str] = None) -> Dict[str, Any]:
    inbox = _inbox(profile)
    action = payload.get("action")
    if not isinstance(action, str) or not action:
        raise HTTPException(status_code=400, detail="action is required")
    value = payload.get("input")
    if value is not None and not isinstance(value, str):
        raise HTTPException(status_code=400, detail="input must be a string")
    try:
        item = await _run(lambda: inbox.act(session_id, action, value))
    finally:
        await _changed()
    if not item:
        _not_open(inbox, session_id)
    return _public(item)


def _not_open(inbox, session_id: str):
    """404 for an unknown id, 409 for a card that is no longer open (acted on / archived)."""
    if inbox.get(session_id):
        raise HTTPException(status_code=409, detail="inbox message is no longer open")
    raise HTTPException(status_code=404, detail="not an open inbox message")


@router.post("/items/{session_id}/accept")
async def accept(session_id: str, profile: Optional[str] = None) -> Dict[str, Any]:
    inbox = _inbox(profile)
    st = _store()

    def run():
        cur = inbox.get(session_id)
        # An older Desktop shows Do it on every card: never let it run a decision card's prompt action.
        if cur and any(a["kind"] in ("resolve", "task") for a in st.item_type(cur)["actions"]):
            raise _Conflict("this card has its own actions; update Hermes Desktop to use them")
        return inbox.act(session_id, None)
    try:
        item = await _run(run)
    except _Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    finally:
        await _changed()
    if not item:
        _not_open(inbox, session_id)
    return _public(item)


class _Conflict(Exception):
    pass


@router.post("/items/{session_id}/archive")
async def archive(session_id: str, profile: Optional[str] = None) -> Dict[str, Any]:
    inbox = _inbox(profile)
    ok = await asyncio.to_thread(lambda: inbox.archive(session_id))
    await _changed()
    if not ok:
        raise HTTPException(status_code=404, detail="not an inbox message")
    return {"ok": True}


@router.post("/items/{session_id}/junk")
async def junk(session_id: str, profile: Optional[str] = None) -> Dict[str, Any]:
    """Owner marks a card as junk: archive, mute the poster family, file a five-whys Kanban card."""
    inbox = _inbox(profile)
    result = await asyncio.to_thread(lambda: inbox.junk(session_id))
    await _changed()
    if not result:
        raise HTTPException(status_code=404, detail="not an inbox message")
    return result


@router.get("/outcome")
async def outcome(key: str, profile: Optional[str] = None) -> Dict[str, Any]:
    inbox = _inbox(profile)
    out = await asyncio.to_thread(lambda: inbox.outcome(key))
    return dict(out, found=True) if out else {"found": False, "key": key}


@router.get("/types")
async def types(profile: Optional[str] = None) -> Dict[str, Any]:
    inbox = _inbox(profile)
    return {"types": await asyncio.to_thread(lambda: inbox.types.list())}


@router.get("/types/{name}")
async def get_type(name: str, profile: Optional[str] = None) -> Dict[str, Any]:
    inbox = _inbox(profile)
    unknown = _store().types_module().UnknownType
    try:
        return await asyncio.to_thread(lambda: inbox.types.get(name))
    except unknown as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/types/{name}")
async def put_type(name: str, payload: Any = Body(...), profile: Optional[str] = None) -> Dict[str, Any]:
    inbox = _inbox(profile)
    spec = payload.get("spec", payload) if isinstance(payload, dict) and "spec" in payload else payload
    return await _run(lambda: inbox.types.define(name, spec))
