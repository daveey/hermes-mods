"""/api/plugins/inbox — REST for the Desktop Inbox section (see ../store.py, ../card_types.py).

GET  /ping                      {ok, api}   (api 3 = card types)
GET  /items                     {items:[...], profile}  (root + every profile with an inbox; item.profile,
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
    return {"items": rows, "profile": _profile()}


@router.post("/items")
async def post(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    return await _run(lambda: _store().Inbox().post(
        payload.get("title", ""), payload.get("body", ""), payload.get("accept_prompt", ""),
        key=payload.get("key"), poster=payload.get("poster"), cwd=payload.get("cwd"),
        prompt_label=payload.get("prompt_label"), type=payload.get("type"), fields=payload.get("fields"), ask=payload.get("ask")))


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
    item = await _run(lambda: inbox.act(session_id, action, value))
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
    if not item:
        _not_open(inbox, session_id)
    return _public(item)


class _Conflict(Exception):
    pass


@router.post("/items/{session_id}/archive")
async def archive(session_id: str, profile: Optional[str] = None) -> Dict[str, Any]:
    inbox = _inbox(profile)
    ok = await asyncio.to_thread(lambda: inbox.archive(session_id))
    if not ok:
        raise HTTPException(status_code=404, detail="not an inbox message")
    return {"ok": True}


@router.post("/items/{session_id}/junk")
async def junk(session_id: str, profile: Optional[str] = None) -> Dict[str, Any]:
    """Owner marks a card as junk: archive, mute the poster family, file a five-whys Kanban card."""
    inbox = _inbox(profile)
    result = await asyncio.to_thread(lambda: inbox.junk(session_id))
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
