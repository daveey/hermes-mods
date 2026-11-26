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
cannot be stored after it. REST views retain the last completed exact-parameter snapshot across revisions
and expiry up to 120s, return its age as ``stale_seconds``, and refresh single-flight in the background.
Cold or older keys wait at most 1s, then return ``{configured: true, warming: true}`` without lane data.
Router startup warms the default (6, 6h, collapsed) key. `blocked_on_you` is whatever core's snapshot reports (human-request parks); older
cores omit it and the Desktop strip treats a missing count as 0.
"""
from __future__ import annotations

import asyncio
import inspect
import os
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

    Only explicit PR/head evidence changes the provider observation. Scheduling,
    comments and dispatch diagnostics change the projection, not GitHub's head.
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
            def newest(events):
                revision = 0
                for event in events:
                    payload = flow_module._payload(dict(event))
                    meta = payload.get('metadata')
                    pr_evidence = meta if isinstance(meta, dict) else payload
                    carries_pr = any(key in pr_evidence for key in ('published_pr', 'reviewed_pr', 'pr', 'flow_pr', 'head', 'head_sha', 'headRefOid'))
                    if carries_pr:
                        revision = max(revision, event['id'])
                return revision
            revision = newest(events)
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
    from hermes_constants import get_hermes_home
    shared = _sibling('shared_observations').SharedObservations(
        Path(get_hermes_home()) / 'cache' / 'lanes-pr-observations-v1')
    return module.Observations(Reader, trace=_sibling('observation_trace').observer_trace, shared=shared)


@lru_cache(maxsize=1)
def _attention_module():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('lanes_attention', Path(__file__).with_name('attention.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _lane_views(hours, window):
    """Both presentation variants share the same hub evidence, including fallback."""
    return _lane_view(hours, window, True, variants=True)


def _lane_view(hours, window, expanded, *, variants=False):
    mods = core()
    if mods is not None and 'evidence' in inspect.signature(mods[2].snapshot).parameters:
        return _current_view(mods, hours, window, expanded, variants=variants)
    snap = read(hours)
    raw = flow(window) if snap.get("configured") else None
    if variants:
        return {name: _view_module().project(snap, raw, flag)
                for name, flag in (('collapsed', False), ('expanded', True))}
    return _view_module().project(snap, raw, expanded)


def _current_view(mods, hours, window, expanded, *, variants=False):
    """One source selection under short read snapshots, for strip AND panel."""
    from hermes_cli.kanban_lanes_sources import read_view
    kbc = mods[0]
    try:
        with kbc.connect_closing() as conn, read_view(conn) as evidence:
            return _project_evidence(mods, conn, evidence, hours, window, expanded, variants=variants)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception('lanes view computation failed')
        snap = {'configured': False, 'reason': f'lanes view is unavailable: {exc}'}
        if variants:
            return {name: _view_module().project(snap, None, flag)
                    for name, flag in (('collapsed', False), ('expanded', True))}
        return _view_module().project(snap, None, expanded)


def token_series(db, policy, *, now=None):
    """Six-hour, 10-minute buckets from canonical run-end usage, like core usage().

    A repeated worker_result for one task/run contributes its maximum reported
    usage once, at the latest report time. Do not merge local/fleet accounting.
    """
    now = int(time.time() if now is None else now)
    cutoff = now - 21600
    out = {name: [0] * 36 for name in policy.lanes}
    for tenant, stamp, tokens in db.execute(
            "SELECT t.tenant, MAX(e.created_at), "
            "MAX(COALESCE(json_extract(e.payload, '$.usage.input_tokens'), 0) + "
            "COALESCE(json_extract(e.payload, '$.usage.output_tokens'), 0)) "
            "FROM task_events e JOIN tasks t ON t.id=e.task_id "
            "WHERE e.kind='worker_result' AND e.created_at > ? "
            "AND json_extract(e.payload, '$.usage') IS NOT NULL GROUP BY e.task_id,e.run_id", (cutoff,)):
        bucket = min(35, max(0, int((stamp - cutoff) // 600)))
        out[policy.lane_of(tenant)][bucket] += int(tokens or 0)
    return out


def _project_evidence(mods, conn, evidence, hours, window, expanded, *, variants=False, sources=False, reader=None):
    """Same hub-computed projection for owned in-process or borrowed RPC readers."""
    if not sources:
        snap, raw = _governed_sources(mods, conn, evidence, hours, window)
        if variants:
            return {name: _view_module().project(snap, raw, flag)
                    for name, flag in (('collapsed', False), ('expanded', True))}
        return _view_module().project(snap, raw, expanded)
    snap = mods[2].snapshot(conn, hours=hours, evidence=evidence)
    raw = None
    if snap.get('configured'):
        try:
            flow_mods = flow_core()
            if flow_mods is None:
                raise RuntimeError('this Hermes core has no lanes flow support yet')
            flow_mod = flow_mods[1]
            if reader is None:
                contexts = _pr_contexts(evidence.conn, flow_mod, evidence=evidence)
                with _observer_init_lock:
                    reader = _observations().reader(contexts)
            raw = flow_mod.snapshot(evidence.conn, window=window, reader=reader, evidence=evidence)
            raw['configured'] = bool(raw.get('lanes'))
            for card in raw.get('cards', []):
                source_row = evidence.row(card['id'])
                card['status'] = source_row['status'] if source_row else None
            _stamp_chats(evidence.conn, raw.get('cards', []))
            raw['attention'] = _attention_module().project(raw['cards'], evidence)
            pol = mods[1].policy()
            if pol is not None:
                raw['strip_tokens'] = token_series(evidence.conn, pol)
                six_hour = snap if hours == 6 else mods[2].snapshot(conn, hours=6, evidence=evidence)
                raw['strip_actual'] = {name: row['actual'] for name, row in six_hour['lanes'].items()}
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception('lanes flow computation failed')
            raw = {'configured': False, 'reason': f'lanes flow is unavailable: {exc}'}
    return snap, raw


@lru_cache(maxsize=None)
def _sibling(name):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('lanes_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stamp_chats(db, cards):
    """Stamp each card with the chats linked to it (unreleased ``task_chat_links``), for the session filter."""
    import sqlite3
    ids = [c['id'] for c in cards if c.get('id')]
    linked = {}
    try:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for task, chat in db.execute(
                    'SELECT task_id, chat_session FROM task_chat_links WHERE released=0 AND task_id IN '
                    f'({",".join("?" * len(chunk))})', chunk):
                if chat:
                    linked.setdefault(task, []).append(chat)
    except sqlite3.Error:
        return  # older board without chat links: creator sessions only
    for card in cards:
        if card.get('id') in linked:
            card['chats'] = sorted(set(linked[card['id']]))


def _governed_sources(mods, conn, evidence, hours, window):
    from contextlib import ExitStack
    from collections import defaultdict
    import sqlite3
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_lanes_sources import read_view
    from hermes_constants import get_hermes_home
    try:
        from hermes_cli import kanban_governors as gov
    except ImportError:
        return _project_evidence(mods, conn, evidence, hours, window, True, sources=True)
    current = gov._board(conn)
    pairs, snapshots = [], []
    source_failed = False
    source_errors = []
    with ExitStack() as stack:
        sources = [(current, conn, evidence)]
        for meta in kb.list_boards(include_archived=False):
            slug = meta.get('slug') or kb.DEFAULT_BOARD
            if slug == current:
                continue
            try:
                other = stack.enter_context(_sibling('board_merge').read_board(meta['db_path']))
                sources.append((slug, other, stack.enter_context(read_view(other))))
            except (OSError, RuntimeError, sqlite3.Error) as exc:
                source_failed = True
                source_errors.append(f'{slug}: {exc}')
                snapshots.append(dict(board=slug, available=False, stale=True, reason=str(exc)))
        # One immutable PR observation scope for the entire projection. Alternating
        # per-board reader() calls would evict the other boards' published values.
        contexts = defaultdict(list)
        flow_mods = flow_core()
        context_failures = {}
        if flow_mods is not None:
            for slug, source_conn, source in sources:
                try:
                    _sibling('board_merge').arm_deadline(source_conn, phase='contexts')
                    for url, context in _pr_contexts(source.conn, flow_mods[1], evidence=source).items():
                        contexts[url].append(context)
                except (OSError, RuntimeError, sqlite3.Error) as exc:
                    context_failures[slug] = str(exc)
        combined = {url: values[0] if len(values) == 1 else values for url, values in contexts.items()}
        with _observer_init_lock:
            reader = _observations().reader(combined)
        for slug, source_conn, source in sources:
            try:
                _sibling('board_merge').arm_deadline(source_conn, phase='projection')
                if slug in context_failures:
                    raise RuntimeError(context_failures[slug])
                pair = _project_evidence(mods, source_conn, source, hours, window, True, sources=True, reader=reader)
            except (OSError, RuntimeError, sqlite3.Error) as exc:
                source_failed = True
                pairs.append(({'configured': True, 'lanes': {}}, {'configured': False, 'reason': f'{slug}: {exc}'}))
                snapshots.append(dict(board=slug, available=False, stale=True, reason=str(exc)))
                continue
            for card in (pair[1] or {}).get('cards', []):
                card['board'] = slug
            if pair[1] and pair[1].get('configured') is False:
                pair[1]['reason'] = f"{slug}: {pair[1].get('reason', 'flow unavailable')}"
            pairs.append(pair)
            try:
                receipt = gov.snapshot(source_conn)
            except (OSError, RuntimeError, sqlite3.Error) as exc:
                receipt = dict(board=slug, available=False, stale=True, reason=str(exc))
            receipt['flow_available'] = (pair[1] or {}).get('configured') is not False
            receipt['open_lanes'] = [name for name, row in pair[0].get('lanes', {}).items() if row.get('open')]
            snapshots.append(receipt)
        snap, raw = _sibling('board_merge').merge(pairs)
        # A flow failure still has an independent summary; an unreadable board
        # has neither. Do not certify the surviving summaries as fleet totals.
        raw['summary_complete'] = not source_failed
        if source_failed:
            raw['flow_complete'] = False
            raw['incomplete_status_lanes'] = list(snap.get('lanes', {}))
            raw['partial_reason'] = '; '.join(filter(None, [raw.get('partial_reason', ''),
                                                           *source_errors, 'board source unavailable']))
    raw['governors'] = _sibling('governors').project(snapshots, raw.get('cards', []), gov.registry(),
        adapter=_sibling('governors').read_adapter(get_hermes_home()))
    return snap, raw


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


def rest_view(hours, window, expanded):
    """REST delivery retains last completed exact-parameter view across revisions."""
    _validate_view(hours, window, expanded)
    with _lock:
        generation = _generation
    return _shared_views().get_stale(
        (hours, window, expanded), (generation, _observation_cursor()),
        lambda: _lane_view(hours, window, expanded))


REST_REFRESH_S = 60.  # default key only; completed views still expire at 120s


def start_legacy():
    """Keep the startup default fresh even without GET or stream demand."""
    warmer = _identity.load(_Path(__file__).with_name('default_warm.py'), 'lanes_default_warm')
    return warmer.DefaultWarm(_shared_views(), lambda: (_generation, _observation_cursor()),
                              lambda: _lane_view(6., '6h', False), interval=REST_REFRESH_S)


@router.get("/v1/lanes")
async def get_lanes(hours: float = Query(6.0), window: str = Query("6h"), expanded: bool = Query(False)) -> Dict[str, Any]:
    _validate_view(hours, window, expanded)
    try:
        return await asyncio.wait_for(asyncio.to_thread(rest_view, hours, window, expanded), timeout=4.)
    except asyncio.TimeoutError:
        # Also bound threadpool queueing during backend bootstrap contention.
        return {'configured': True, 'warming': True}


@router.get('/v1/governors')
async def get_governors():
    view = await get_lanes(6.0, '6h', False)
    return view.get('governors', {'available': False, 'state': 'unknown', 'rows': [], 'scope': []})


@router.get("/v1/lanes/flow")
async def get_flow(window: str = Query("6h")) -> Dict[str, Any]:
    if window not in FLOW_WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of {', '.join(FLOW_WINDOWS)}")
    return await get_lanes(dict(zip(FLOW_WINDOWS, WINDOWS))[window], window, True)


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


WATCH_S = 0.25  # change-cursor poll; a change is projected on the first tick that sees it
MIN_PROJECT_GAP_S = 3.0 if os.environ.get('HERMES_LANES_CHANGE_ONLY', '1').lower() not in ('0', 'false', 'off', 'no') else 1.0  # reversible start-to-start floor; trailing demand retained
HEARTBEAT_S = 30.0
_seen_cursor = None
_active_streams = 0  # accepted viewers in this backend process, including non-debug clients


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
    """Native source admission, followed by semantic—not WAL—revision comparison."""
    enabled = os.environ.get('HERMES_LANES_CHANGE_ONLY', '1').lower() not in ('0', 'false', 'off', 'no')
    source = _change_source.read(_capture_cursor) if enabled else _capture_cursor()[0]
    observation = _observations().cursor() if _observations.cache_info().currsize else None
    # Freshness is a clock transition even when a stopped source writes nothing.
    freshness = tuple(time.time() <= deadline for deadline in _governor_deadlines)
    return [source, observation, freshness]


_governor_deadlines = ()


def _capture_cursor():
    """Read after a source notification (or initial demand), never an idle timer."""
    from pathlib import Path
    from hermes_constants import get_default_hermes_root
    from hermes_cli import kanban_activity_revision as activity
    mods = core()
    if mods is None:
        return None, ()
    kbc, _, cli = mods
    cursors, paths = [], []
    with kbc.connect_closing() as conn:
        from hermes_cli import kanban_worker_node as wn
        from hermes_cli.kanban_capacity_evidence import _board as board_of
        current_board = board_of(conn)
        current_path = next(r[2] for r in conn.execute('PRAGMA database_list') if r[1] == 'main')
        # Cover configured replica creation/replacement even when opening it
        # fails transiently. Recovery must not depend on a local board write.
        replica_path = str(wn._replica_path(conn))
        paths.append(replica_path)
        def incarnation(path):
            try:
                stat = os.stat(path)
                return stat.st_dev, stat.st_ino
            except FileNotFoundError:
                return None
        # Take identity before opening: never label an old SQLite handle with
        # the replacement file's identity if an atomic rename races capture.
        replica_identity = incarnation(replica_path)
        replica = cli._fleet(conn, local=False)
        try:
            for db in (conn, replica):
                if db is not None:
                    path = next(row[2] for row in db.execute('PRAGMA database_list') if row[1] == 'main')
                    paths.append(path)
                    # Revision triggers explicitly omit heartbeat/claim clocks.
                    # Older boards still use event identity, never file write stamps.
                    revision = activity.read_key(db, activity.tables_for(path))
                    cursors.append((path, revision if revision is not None else tuple(
                        db.execute("SELECT coalesce(max(id),0) FROM task_events WHERE kind != 'heartbeat'").fetchone())))
            if incarnation(replica_path) != replica_identity:
                # Source keeps failed captures pending, even after the native
                # notification was consumed. Do not publish a mixed capture.
                raise RuntimeError('replica replaced during lanes cursor capture')
            # Stable through ordinary WAL/checkpoint writes, changes on replace
            # even when the new database has exactly the same revision counters.
            cursors.append(('replica-incarnation', replica_identity))
        finally:
            if replica is not None:
                replica.close()
    root = Path(get_default_hermes_root())
    for path in (root / 'config.yaml', root / 'kanban' / 'lanes-planner.json'):
        paths.append(str(path))
        try:
            stat = path.stat()
            cursors.append((stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            cursors.append(None)
    global _governor_deadlines
    from hermes_constants import get_hermes_home
    from hermes_cli import kanban_db as kb
    import json
    deadlines = []
    paths.append(str(kb.boards_root()))
    for meta in kb.list_boards(include_archived=False):
        paths.append(str(kb.board_metadata_path(meta['slug'])))
        board_path = Path(current_path if meta['slug'] == current_board else meta['db_path'])
        if str(board_path) not in paths:
            paths.append(str(board_path))
            import sqlite3
            try:
                with _sibling('board_merge').read_board(board_path, snapshot=False) as db:
                    revision = activity.read_key(db, activity.tables_for(board_path))
                    cursors.append((str(board_path), revision if revision is not None else tuple(
                        db.execute("SELECT coalesce(max(id),0) FROM task_events WHERE kind != 'heartbeat'").fetchone())))
            except (OSError, RuntimeError, sqlite3.Error):
                # Preserve the failed path in native coverage, including recovery
                # writes/replacement; an unreadable sibling cannot hide healthy
                # board revisions or terminate the shared stream.
                cursors.append((str(board_path), 'unavailable'))
        paths.append(str(board_path) + '.governors.json')
    paths.append(str(Path(get_hermes_home()) / 'state/governors/mac-mini.json'))
    for name in paths:
        if not name.endswith('.json') or not (name.endswith('.governors.json') or name.endswith('/mac-mini.json')):
            continue
        try:
            text = Path(name).read_text()
            doc = json.loads(text)
            stamp = doc.get('tick_at', doc.get('read_at'))
            if isinstance(stamp, (int, float)):
                cadence = doc.get('cadence_s', 60)
                # Core owns its receipt freshness bound. The device adapter has
                # no core receipt and retains the documented 3x cadence policy.
                if name.endswith('.governors.json'):
                    from hermes_cli.kanban_governors import freshness_bound
                    bound = freshness_bound(doc)
                else:
                    bound = 3 * cadence if isinstance(cadence, (int, float)) else None
                if isinstance(bound, (int, float)) and 0 <= bound < float('inf'):
                    deadlines.append(stamp + bound)
            # Ignore tick time while unchanged decisions remain fresh.
            cursors.append((name, {k: v for k, v in doc.items() if k not in
                                  ('tick_at', 'read_at', 'tick_completions', 'tick_gap_p95_s')}))
        except (OSError, ValueError, AttributeError, ImportError):
            cursors.append((name, None))
    _governor_deadlines = tuple(deadlines)
    return cursors, tuple(paths)


@router.websocket('/v1/lanes/stream')
async def stream_lanes(ws: WebSocket):
    global _active_streams
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
    debug = ws.query_params.get('debug', 'false').lower() == 'true'
    _active_streams += 1
    try:
        # Keep the census live, rather than freezing it in the helper namespace.
        class Context:
            def __getattr__(self, name):
                return globals()[name]
        await _stream_engine.run(Context(), ws, params, debug)
    except WebSocketDisconnect:
        return
    except (OSError, RuntimeError, TimeoutError):
        try:
            await ws.close(code=1013)
        except (RuntimeError, WebSocketDisconnect):
            pass
    finally:
        _active_streams -= 1


# Capture once at router load, never recalculate expected hashes at child spawn.
# Core and common projection stay loaded; lazily file-loaded helpers are fenced
# against these same captured bytes before child or fallback work starts.
import importlib.util as _import_util
from pathlib import Path as _Path
_spec = _import_util.spec_from_file_location('lanes_rpc_identity', _Path(__file__).with_name('projector_identity.py'))
assert _spec is not None and _spec.loader is not None
_identity = _import_util.module_from_spec(_spec)
_spec.loader.exec_module(_identity)
_loaded_identity = _identity.capture(_Path(__file__).parent)
_change_source = _identity.load(_Path(__file__).with_name('change_source.py'), 'lanes_change_source').Source()
_rpc_client = _identity.load(_Path(__file__).with_name('projector_client.py'), 'lanes_rpc_client')
_stream_engine = _identity.load(_Path(__file__).with_name('stream_engine.py'), 'lanes_stream_engine')
_compat_fallback = _rpc_client.Fallback()
_projector_instance = None
_projector_init = threading.Lock()
_projector_enabled = core() is not None


def _projector():
    global _projector_instance
    with _projector_init:
        if _projector_instance is None:
            from types import SimpleNamespace
            _projector_instance = _rpc_client.Supervisor(
                SimpleNamespace(core=core, _lane_views=_lane_views, MIN_PROJECT_GAP_S=MIN_PROJECT_GAP_S), _loaded_identity)
        return _projector_instance
