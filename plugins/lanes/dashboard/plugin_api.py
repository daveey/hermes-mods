"""GET /api/plugins/lanes/v1/lanes?hours=6: per lane target and actual share of run-hours, running, ready, held at
the gate, open, done, tokens and estimated cost over the window (core ``kanban_lanes_cli.snapshot``).
PUT /api/plugins/lanes/v1/lanes/{lane}: body ``{"share": n, "paused": bool, "admit_per_day": n,
"max_workers": n|null, "cards_per_day": n|null, "usd_per_day": n|null}`` (any subset;
unsupported worker ceilings or null clears return 409 before any write).
GET /api/plugins/lanes/v1/lanes/flow?window=6h: every open card with step/reason/source plus per-lane done, run-hours,
cost, limits and today's usage (core ``kanban_lanes_flow.snapshot``, the data of ``hermes kanban lanes flow --json``).
GET /api/plugins/lanes/v1/held?lane=x: cards waiting at a lane gate. POST /api/plugins/lanes/v1/held/{card}/admit.

Without lanes: ``GET /v1/lanes`` answers ``{"configured": false, "reason": ...}`` on a core without lanes support or
with no lanes configured, so the panel can say why it is empty; every other route answers 409 in both cases.
Reads are cached for 10 s per window; a write starts a new cache generation, so a read that began before it
cannot be stored after it. `blocked_on_you` is whatever core's snapshot reports (human-request parks); older
cores omit it and the Desktop strip treats a missing count as 0.
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import time
from functools import lru_cache
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query, WebSocket, WebSocketDisconnect

router = APIRouter()
CACHE_S = 10
WINDOWS = (1.0, 6.0, 24.0, 168.0)
FLOW_WINDOWS = ("1h", "6h", "24h", "7d")
LIMIT_FIELDS = ("cards_per_day", "usd_per_day", "max_workers")
_flow_cache: Dict[str, tuple] = {}
_lock = threading.Lock()
_observer_init_lock = threading.Lock()
_view_init_lock = threading.Lock()
_cache: Dict[float, tuple] = {}
_generation = 0


def core():
    """Core's lanes modules, or None on a core that predates them."""
    try:
        from hermes_cli import kanban_db_connect, kanban_lanes, kanban_lanes_cli
    except ImportError:
        return None
    return kanban_db_connect, kanban_lanes, kanban_lanes_cli


def read(hours: float, now: Optional[float] = None) -> Dict[str, Any]:
    mods = core()
    if mods is None:
        return {"configured": False, "reason": "this Hermes core has no lanes support yet"}
    now = time.time() if now is None else now
    with _lock:
        hit = _cache.get(hours)
        if hit and now - hit[0] < CACHE_S:
            return hit[1]
        generation = _generation
    kbc, _lanes, cli = mods
    try:
        with kbc.connect_closing() as conn:
            snap = cli.snapshot(conn, hours=hours)
    except Exception as exc:  # an unreadable or invalid config is a reason to show, not a 500
        return {"configured": False, "reason": f"lanes configuration is unreadable or invalid: {exc}"}
    if not snap.get("configured"):
        snap = {"configured": False, "reason": "no lanes are configured (kanban.admission.lanes)"}
    with _lock:
        if generation == _generation:
            _cache[hours] = (now, snap)
    return snap


def _written() -> None:
    global _generation
    with _lock:
        _generation += 1
        _cache.clear()
        _flow_cache.clear()


def flow_core():
    """Core's connect module and flow module, or None on a core that predates ``lanes flow``."""
    try:
        from hermes_cli import kanban_db_connect, kanban_lanes_flow
    except ImportError:
        return None
    return kanban_db_connect, kanban_lanes_flow


def flow(window: str, now: Optional[float] = None) -> Dict[str, Any]:
    """Open cards by step and reason; ``{"configured": false, ...}`` where flow is not available."""
    mods = flow_core()
    if mods is None:
        return {"configured": False, "reason": "this Hermes core has no lanes flow support yet"}
    kbc, kanban_lanes_flow = mods
    now = time.time() if now is None else now
    with _lock:
        hit = _flow_cache.get(window)
        if hit and now - hit[0] < CACHE_S and hit[2] == _observation_cursor():
            return hit[1]
        generation = _generation
    observation_cursor = _observation_cursor()
    try:
        with kbc.connect_closing() as conn:
            # Match the CLI's fleet/local selection and ownership of its replica.
            fleet_modules = core()
            if fleet_modules is None:
                raise RuntimeError("this Hermes core has no lanes support yet")
            replica = fleet_modules[2]._fleet(conn, local=False)
            try:
                db = replica if replica is not None else conn
                if 'reader' in inspect.signature(kanban_lanes_flow.snapshot).parameters:
                    evidence = kanban_lanes_flow.Evidence(db) if hasattr(kanban_lanes_flow, 'Evidence') else None
                    contexts = _pr_contexts(db, kanban_lanes_flow, evidence=evidence)
                    with _observer_init_lock:
                        reader = _observations().reader(contexts)
                        observation_cursor = reader.cursor
                    extra = {'evidence': evidence} if evidence is not None else {}
                    body = dict(kanban_lanes_flow.snapshot(db, window=window, reader=reader, **extra))
                else:
                    # Compatibility while the additive core reader seam rolls out.
                    body = dict(kanban_lanes_flow.snapshot(db, window=window))
            finally:
                if replica is not None:
                    replica.close()
    except Exception as exc:
        return {"configured": False, "reason": f"lanes flow is unavailable: {exc}"}
    body["configured"] = bool(body.get("lanes"))
    if not body["configured"]:
        body["reason"] = "no lanes are configured (kanban.admission.lanes)"
    with _lock:
        if generation == _generation:
            _flow_cache[window] = (now, body, observation_cursor)
    return body


def _pr_contexts(db, flow_module, *, evidence=None):
    """Only a PR's owning card evidence invalidates its network observation.

    Heartbeats/external-watch ticks carry no head evidence. Other events on the
    card (including comments/new publication) conservatively invalidate it.
    """
    from collections import defaultdict
    groups = defaultdict(list)
    identity = tuple(tuple(row) for row in db.execute('PRAGMA database_list'))
    rows = evidence.rows if evidence is not None else db.execute(f'SELECT * FROM tasks WHERE status IN {flow_module._OPEN}')
    for row in rows:
        events = (evidence.events(row['id']) if evidence is not None else
                  db.execute('SELECT id,kind,payload FROM task_events WHERE task_id=? ORDER BY id', (row['id'],)).fetchall())
        pr = None if evidence is not None else flow_module._pr_snapshot(row, events, flow_module._awaiting(row, events))
        url = evidence.pr_url(row) if evidence is not None else (pr or {}).get('url')
        if url:
            revision = 0
            for event in events:
                payload = flow_module._payload(dict(event))
                meta = payload.get('metadata')
                pr_evidence = meta if isinstance(meta, dict) else payload
                carries_pr = any(key in pr_evidence for key in ('published_pr', 'reviewed_pr', 'pr', 'flow_pr', 'head', 'head_sha', 'headRefOid'))
                if event['kind'] not in ('heartbeat', 'external_watch') or carries_pr:
                    revision = max(revision, event['id'])
            source = evidence.identity(row['id']) if evidence is not None and hasattr(evidence, 'identity') else identity
            groups[url].append((row['id'], row['status'], row['completion_contract'], (source, revision)))
    return {url: (identity, tuple(sorted(cards))) for url, cards in groups.items()}


def _observation_cursor():
    return _observations().cursor() if _observations.cache_info().currsize else None


@lru_cache(maxsize=1)
def _observations():
    import importlib.util
    from pathlib import Path
    from hermes_cli.kanban_lanes_flow_pr import Reader
    spec = importlib.util.spec_from_file_location('lanes_observations', Path(__file__).with_name('observations.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Observations(Reader)


@lru_cache(maxsize=1)
def _view_module():
    # File-loaded by core's plugin loader and by standalone test harnesses.
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("lanes_view", Path(__file__).with_name("view.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shared_views():
    # lru_cache alone permits duplicate concurrent cold construction.
    with _view_init_lock:
        return _make_shared_views()


@lru_cache(maxsize=1)
def _make_shared_views():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('lanes_shared_views', Path(__file__).with_name('shared_views.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SharedViews(CACHE_S)


def lane_view(hours: float = 6.0, window: str = "6h", expanded: bool = False):
    _validate_view(hours, window, expanded)
    mods = core()
    if mods is not None and 'evidence' not in inspect.signature(mods[2].snapshot).parameters:
        # Legacy read/flow already own their expiry. An outer cache would
        # resurrect a nearly expired inner snapshot for another full TTL.
        return _lane_view(hours, window, expanded)
    with _lock:
        generation = _generation
    return _shared_views().get((hours, window, expanded), (generation, _observation_cursor()),
                               lambda: _lane_view(hours, window, expanded))


def _lane_view(hours, window, expanded):
    mods = core()
    if mods is not None and 'evidence' in inspect.signature(mods[2].snapshot).parameters:
        return _current_view(mods, hours, window, expanded)
    snap = read(hours)
    return _view_module().project(snap, flow(window) if expanded and snap.get("configured") else None, expanded)


def _current_view(mods, hours, window, expanded):
    """One source selection under short read snapshots, for strip AND panel."""
    from hermes_cli.kanban_lanes_sources import read_view
    kbc, _, cli = mods
    try:
        with kbc.connect_closing() as conn, read_view(conn) as evidence:
            snap = cli.snapshot(conn, hours=hours, evidence=evidence)
            raw = None
            if expanded and snap.get('configured'):
                try:
                    flow_mods = flow_core()
                    if flow_mods is None:
                        raise RuntimeError('this Hermes core has no lanes flow support yet')
                    flow_mod = flow_mods[1]
                    contexts = _pr_contexts(evidence.conn, flow_mod, evidence=evidence)
                    with _observer_init_lock:
                        reader = _observations().reader(contexts)
                    raw = flow_mod.snapshot(evidence.conn, window=window, reader=reader, evidence=evidence)
                    raw['configured'] = bool(raw.get('lanes'))
                except Exception as exc:
                    raw = {'configured': False, 'reason': f'lanes flow is unavailable: {exc}'}
            return _view_module().project(snap, raw, expanded)
    except Exception as exc:
        return _view_module().project({'configured': False, 'reason': f'lanes view is unavailable: {exc}'}, None, expanded)


def _need_core():
    """Core's lanes modules, or 409 when this core has none or no lanes are configured."""
    mods = core()
    if mods is None:
        raise HTTPException(status_code=409, detail="this Hermes core has no lanes support yet")
    try:
        configured = mods[1].policy() is not None
    except Exception as exc:  # unreadable or invalid config: nothing to write to
        raise HTTPException(status_code=409, detail=f"lanes configuration is unreadable or invalid: {exc}")
    if not configured:
        raise HTTPException(status_code=409, detail="no lanes are configured (kanban.admission.lanes)")
    return mods


def change(lane: str, body: Dict[str, Any]) -> Dict[str, Any]:
    _kbc, _lanes, cli = _need_core()
    unknown = sorted(set(body) - {"share", "paused", "admit_per_day", *LIMIT_FIELDS})
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(unknown)}")
    if "paused" in body and type(body["paused"]) is not bool:
        raise ValueError("paused must be true or false")
    extra = {k: body[k] for k in LIMIT_FIELDS if k in body}
    if any(value is None for value in extra.values()) and not hasattr(cli, "UNSET"):
        raise HTTPException(status_code=409, detail="this Hermes core cannot clear lane limits yet")
    if extra:
        # Check before writing; catching TypeError from inside set_lane could hide a bug
        # or report failure after a partial write on an otherwise supported core.
        try:
            inspect.signature(cli.set_lane).bind(lane, share=body.get("share"),
                                                admit_per_day=body.get("admit_per_day"),
                                                paused=body.get("paused"), **extra)
        except TypeError:
            raise HTTPException(status_code=409, detail="this Hermes core does not support the requested lane limits yet")
    cli.set_lane(lane, share=body.get("share"), admit_per_day=body.get("admit_per_day"), paused=body.get("paused"),
                 **extra)
    _written()
    return read(6.0)


def held(lane: Optional[str]) -> Dict[str, Any]:
    kbc, lanes, _cli = _need_core()
    with kbc.connect_closing() as conn:
        rows = lanes.held(conn, lane)[:200]
    return {"cards": [{"id": r["id"], "title": r["title"], "lane": r["tenant"], "created_at": r["created_at"]}
                      for r in rows]}


def admit(card: str, reason: str) -> Dict[str, Any]:
    kbc, lanes, _cli = _need_core()
    with kbc.connect_closing() as conn:
        done = lanes.admit(conn, card, actor="user (lanes panel)", reason=reason, budget=False)  # the user's own admit
    _written()
    return {"admitted": done}


@router.get("/v1/lanes")
async def get_lanes(hours: float = Query(6.0), window: str = Query("6h"), expanded: bool = Query(False)) -> Dict[str, Any]:
    _validate_view(hours, window, expanded)
    return await asyncio.to_thread(lane_view, hours, window, expanded)


@router.get("/v1/lanes/flow")
async def get_flow(window: str = Query("6h")) -> Dict[str, Any]:
    if window not in FLOW_WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of {', '.join(FLOW_WINDOWS)}")
    return await asyncio.to_thread(lane_view, dict(zip(FLOW_WINDOWS, WINDOWS))[window], window, True)


@router.put("/v1/lanes/{lane}")
async def put_lane(lane: str, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(change, lane, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/v1/held")
async def get_held(lane: Optional[str] = Query(None)) -> Dict[str, Any]:
    return await asyncio.to_thread(held, lane)


@router.post("/v1/held/{card}/admit")
async def post_admit(card: str, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    return await asyncio.to_thread(admit, card, str(body.get("reason") or "admitted from the lanes panel"))


WATCH_S = 0.5
DEBOUNCE_S = 1.0
HEARTBEAT_S = 30.0
_seen_cursor = None


def _refresh_cursor(cursor):
    """Invalidate shared GET caches once per source revision, not once per viewer."""
    global _seen_cursor, _generation
    with _lock:
        if cursor != _seen_cursor:
            _seen_cursor = cursor
            _generation += 1
            _cache.clear()
            _flow_cache.clear()


def _validate_view(hours, window, expanded):
    if isinstance(hours, bool) or hours not in WINDOWS or window not in FLOW_WINDOWS or type(expanded) is not bool:
        raise HTTPException(status_code=400, detail="invalid lanes hours, window or expanded")


def _ws_upgrade_authorized(ws):
    # Same canonical gate as Kanban /events, but fail closed if unavailable.
    try:
        from hermes_cli.web_server_chat import _ws_auth_ok
        return bool(_ws_auth_ok(ws))
    except ImportError:
        return False


def change_cursor():
    """Cheap watch of the source board AND fleet replica, policy and planner notes."""
    from pathlib import Path
    from hermes_constants import get_default_hermes_root
    mods = core()
    if mods is None:
        return None
    kbc, _, cli = mods
    cursors = []
    with kbc.connect_closing() as conn:
        replica = cli._fleet(conn, local=False)
        try:
            for db in (conn, replica):
                if db is not None:
                    cursors.append(tuple(db.execute('SELECT coalesce(max(id),0) FROM task_events').fetchone()))
        finally:
            if replica is not None:
                replica.close()
    root = Path(get_default_hermes_root())
    for path in (root / 'config.yaml', root / 'kanban' / 'lanes-planner.json'):
        try:
            stat = path.stat()
            cursors.append((stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            cursors.append(None)
    if _observations.cache_info().currsize:
        cursors.append(_observations().cursor())
    return cursors


@router.websocket('/v1/lanes/stream')
async def stream_lanes(ws: WebSocket):
    import hashlib
    import json
    if not _ws_upgrade_authorized(ws):
        await ws.close(code=1008)
        return
    try:
        params = (float(ws.query_params.get('hours', '6')), ws.query_params.get('window', '6h'),
                  ws.query_params.get('expanded', 'false').lower() == 'true')
        _validate_view(*params)
    except (ValueError, HTTPException):
        await ws.close(code=1008)
        return
    await ws.accept()
    last_hash = None
    version = 0
    async def send_view():
        nonlocal last_hash, version
        view = await asyncio.to_thread(lane_view, *params)
        echo = dict(zip(('hours', 'window', 'expanded'), params))
        digest = hashlib.sha256(json.dumps({'view': view, 'params': echo}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if digest != last_hash:
            version += 1
            await ws.send_json({'version': version, 'view': view, 'params': echo})
            last_hash = digest
    try:
        cursor = await asyncio.to_thread(change_cursor)
        _refresh_cursor(cursor)
        await send_view()
        last_heartbeat = time.monotonic()
        dirty_at = None
        while True:
            # wait_for cancels and joins receive on timeout. No detached poll/receive tasks.
            try:
                message = await asyncio.wait_for(ws.receive(), WATCH_S)
                if message['type'] == 'websocket.disconnect':
                    return
                if message.get('text'):
                    try:
                        update = json.loads(message['text'])
                        new = (update.get('hours', params[0]), update.get('window', params[1]), update.get('expanded', params[2]))
                        _validate_view(*new)
                    except (ValueError, AttributeError, HTTPException):
                        await ws.close(code=1008)
                        return
                    params = new
                    await send_view()
            except TimeoutError:
                pass
            current = await asyncio.to_thread(change_cursor)
            now = time.monotonic()
            if current != cursor:
                cursor = current
                if dirty_at is None:
                    dirty_at = now
            if dirty_at is not None and now - dirty_at >= DEBOUNCE_S:
                _refresh_cursor(cursor)
                await send_view()
                dirty_at = None
            if now - last_heartbeat >= HEARTBEAT_S:
                await ws.send_json({'type': 'heartbeat', 'version': version})
                last_heartbeat = now
    except WebSocketDisconnect:
        return
    except (OSError, RuntimeError) as exc:
        import logging
        logging.getLogger(__name__).warning('Lanes stream closed: %s', exc)
        try:
            await ws.close(code=1011)
        except (RuntimeError, WebSocketDisconnect):
            pass
