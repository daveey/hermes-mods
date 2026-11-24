"""Canonical ActionRequest v2 API, mounted at /api/plugins/user-steps.

Authentication is provided by the owning dashboard/profile mount. Namespace comes
from server configuration, never a payload. The optional owner_resolver is a server
integration seam for authenticated connection principals, not an HTTP parameter.
Legacy reads remain passive; generic resolve is intentionally gone (410).
"""
import contextvars
import functools
import importlib.util
import inspect
import json
import os
import sqlite3
import sys
import time
import logging
import threading
import asyncio
import weakref
import anyio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool


# Thread lanes (core ``hermes_cli.web_thread_lanes``): this plugin's Desktop-polled reads get their own thread
# budget and a queue deadline (503 + Retry-After, never started) instead of the shared AnyIO pool, which slow
# reads filled for ~2 h on 2026-11-21. A core without lanes keeps the shared pool, as before. [daveey]
try:
    from hermes_cli.web_thread_lanes import run_in_lane as _run_in_lane
except ImportError:  # core before thread lanes
    _run_in_lane = None


async def _on_lane(lane, fn, *args, request=None, deadline=None, **kwargs):
    if _run_in_lane is None:
        return await run_in_threadpool(functools.partial(fn, *args, **kwargs))
    return await _run_in_lane(lane, fn, *args, request=request, deadline=deadline, **kwargs)
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

API_VERSION = 2
# Separate capacity from both feed reads and the default AnyIO read pool.
_DECISION_LIMITERS = weakref.WeakKeyDictionary()


async def _on_decision(fn, *args):
    loop = asyncio.get_running_loop()
    gate, runner = _DECISION_LIMITERS.setdefault(loop, (asyncio.BoundedSemaphore(4), anyio.CapacityLimiter(4)))
    try:
        async with asyncio.timeout(0.75):
            await gate.acquire()
    except TimeoutError:
        raise HTTPException(503, 'Decision not started: capacity busy', headers={'Retry-After': '1'})
    work = asyncio.create_task(anyio.to_thread.run_sync(functools.partial(fn, *args), limiter=runner))
    # Native task cancellation must not free capacity while a worker is still
    # delivering the frozen operation. Keep its context and slot until it exits.
    def finished(task):
        gate.release()
        if not task.cancelled():
            task.exception()  # retrieve errors even if the HTTP caller left
    work.add_done_callback(finished)
    return await asyncio.shield(work)
# Background re-delivery of decisions refused because the session was mid-turn. ~1h of attempts, then manual Retry.
RETRY_INTERVAL_S = float(os.environ.get('HERMES_USER_STEPS_RETRY_S', '15'))
RETRY_MAX_ATTEMPTS = 240
# GET /requests skips the remote card pass for an owner whose strict feed pass finished cleanly within this window
# (``Mirror.reconcile_poll``); 0 turns the skip off. The session-panel feed re-runs that pass whenever its sources
# (boards, hub replica, origin stores) move and at least every 60 s (``PRODUCER_FULL_EVERY_S``), so a quiet chat
# stays covered between passes; a change the feed's sources don't see shows at most this late.
FEED_FRESH_S = float(os.environ.get('HERMES_USER_STEPS_FEED_FRESH_S', '60'))


_LOAD_LOCK = threading.Lock()


def _load(name: str):
    key = f"_user_steps_{name}"
    path = Path(__file__).resolve().parents[1] / f"{name}.py"
    with _LOAD_LOCK:
        mtime = path.stat().st_mtime_ns
        mod = sys.modules.get(key)
        if mod is None or getattr(mod, "_mtime", None) != mtime:
            spec = importlib.util.spec_from_file_location(key, path)
            candidate = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(candidate)
            setattr(candidate, "_mtime", mtime)
            # A failed import leaves the old module and its readers usable.
            sys.modules[key] = candidate
            if mod is not None and callable(getattr(mod, 'close_pool', None)):
                mod.close_pool()
            mod = candidate
        return mod


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session: StrictStr
    expectedRevision: StrictInt
    choiceId: StrictStr
    revisionText: Optional[StrictStr] = None
    idempotencyKey: StrictStr


class ClientTimingBody(BaseModel):
    """Desktop's per-hop timing for one real decision click; joins the server 'Decision timing' line by operation."""
    model_config = ConfigDict(extra='forbid')
    session: StrictStr
    operation: StrictStr = Field(min_length=1, max_length=200, pattern=r'^[A-Za-z0-9_-]+$')
    posted_ms: float = Field(strict=True, ge=0, le=600_000, allow_inf_nan=False)
    responded_ms: float = Field(strict=True, ge=0, le=600_000, allow_inf_nan=False)
    refreshed_ms: float = Field(strict=True, ge=0, le=600_000, allow_inf_nan=False)
    painted_ms: float = Field(strict=True, ge=0, le=600_000, allow_inf_nan=False)
    visible_ms: float | None = Field(default=None, strict=True, ge=0, le=600_000, allow_inf_nan=False)

    @model_validator(mode='after')
    def monotonic(self):
        if not (self.posted_ms <= self.responded_ms <= self.refreshed_ms and
                self.responded_ms <= self.painted_ms and
                (self.visible_ms is not None and self.visible_ms == self.painted_ms or
                 self.visible_ms is None and self.refreshed_ms <= self.painted_ms)):
            raise ValueError('Timing offsets must be monotonic')
        return self


class ListAnswerBody(BaseModel):
    """A numbered answer to a frozen list snapshot. The owner, request, choice and revision come from the server."""
    model_config = ConfigDict(extra='forbid')
    session: StrictStr
    snapshotDigest: StrictStr
    n: StrictInt
    idempotencyKey: StrictStr


class ChatReconcileBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session: StrictStr
    expectedRevision: StrictInt


class WithdrawBody(BaseModel):
    """The person removes an ask without deciding it: nothing is delivered and no turn starts."""
    model_config = ConfigDict(extra='forbid')
    session: StrictStr
    expectedRevision: StrictInt
    reason: Optional[StrictStr] = None


@functools.lru_cache(maxsize=8)
def _accepts_stale_ok(reader) -> bool:
    """Whether the installed core's ``remote_links_and_origin_rows`` takes ``stale_ok`` (decided once per
    function, never by retrying a TypeError per request). Older cores only have the strict call."""
    try:
        return 'stale_ok' in inspect.signature(reader).parameters
    except (TypeError, ValueError):
        return False


def _with_terminal_history(tasks, history, ids):
    """Display-only union with the core's gateway/profile-scoped receipt projection.

    Do not reinterpret terminal reported_status, suppress live rows, mutate shared
    projection dictionaries, or infer an owner from a bare task id.
    """
    def identity(row):
        if all(row.get(k) for k in ('gateway', 'profile', 'board', 'id')):
            return '/'.join(str(row[k]) for k in ('gateway', 'profile', 'board', 'id'))
        return row.get('key') or None

    out = list(tasks)
    seen = {identity(row) for row in tasks} - {None}
    wanted = set(ids)
    for row in history:
        if row.get('status') not in ('done', 'archived') or row.get('unfinished') is not False:
            continue
        if not wanted.intersection((row.get('session'), row.get('origin_session'))):
            continue
        # Require the owner and origin qualification guaranteed by the public helper.
        if not all(row.get(k) for k in ('gateway', 'profile', 'board', 'id', 'origin_gateway', 'origin_profile')):
            continue
        key = identity(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(row, session_id=row['session'], source='origin-history'))
    return out


def _server_owner(core, session, *, install_id=None, profile=None):
    if install_id is None and profile is None:
        return core.server_owner(session)
    return core.server_owner(session, install_id=install_id, profile=profile)


def _bound_identity(core, install_id, profile):
    import re
    if not isinstance(install_id, str) or not re.fullmatch(r'[0-9a-f]{32}', install_id):
        raise core.ActionError('Explicit install identity unavailable', 503)
    if not isinstance(profile, str) or not profile.strip():
        raise core.ActionError('Explicit profile unavailable', 503)


def _panel_presenter(core, store, *, bound):
    module = _load('kanban_mirror')
    return module.Presentation(core, store) if bound else module.Mirror(core, store)


def panel_owners(home: Path, session: str, lineage, *, install_id=None, profile=None, bound=False) -> list:
    """Capture stored membership once, closing SQL before owner derivation.

    Absence is a 404; an unreadable source is an error, never an empty snapshot.
    Identity is resolved once in the current profile, not from supplied paths.
    ``bound=True`` uses the authorized home and explicit install identity only.
    """
    core = _load('core')
    if bound:
        _bound_identity(core, install_id, profile)
    if not bound:
        if core.home().resolve() != Path(home).resolve():
            raise core.ActionError('Panel profile does not match current profile', 409)
    result = _panel_owners_many(core, {session: lineage}, home=Path(home) if bound else None,
                                install_id=install_id, profile=profile)[session]
    if isinstance(result, Exception):
        raise result
    return result


def _panel_owners_many(core, lineages, *, home=None, install_id=None, profile=None):
    scopes = {sid: list(dict.fromkeys([*lineage, sid])) for sid, lineage in lineages.items()}
    ids = list(dict.fromkeys(s for lineage in scopes.values() for s in lineage))
    if not ids:
        return {}
    c = core._ro_connect(core._state_db(home), timeout=2)
    try:
        c.execute('PRAGMA query_only=ON')
        rows = c.execute('SELECT id FROM sessions WHERE id IN (SELECT value FROM json_each(?))',
                         (json.dumps(ids),)).fetchall()
    finally:
        c.close()
    present = {row[0] for row in rows}
    valid = [sid for sid in scopes if sid in present]
    identity = (_server_owner(core, valid[0], install_id=install_id, profile=profile) if valid else None)
    owners = {sid: dict(identity, storedSessionId=sid) for sid in ids if sid in present} if identity else {}
    for own in owners.values():
        core.validate_owner(own)
    return {sid: [owners[s] for s in lineage if s in owners] if sid in present
            else core.ActionError('Unknown stored session on this profile', 404)
            for sid, lineage in scopes.items()}


def panel_requests_many(home: Path, lineages, *, install_id=None, profile=None, bound=False) -> dict:
    """Read all lineage requests with one closed snapshot per source database.

    A missing watched session is a per-session 404, never authority inherited
    from its parent. Source failures raise for the pass, not a successful empty
    capture. Only immutable materialized values cross the SQL boundary.
    """
    import copy
    core = _load('core')
    if bound:
        _bound_identity(core, install_id, profile)
    if not bound:
        if core.home().resolve() != Path(home).resolve():
            raise core.ActionError('Panel profile does not match current profile', 409)
    scopes = _panel_owners_many(core, lineages, home=Path(home) if bound else None,
                                install_id=install_id, profile=profile)
    owners = {}
    for group in scopes.values():
        if not isinstance(group, Exception):
            for own in group:
                owners[own['storedSessionId']] = own
    store_path = Path(home) / 'user-steps' / 'actions.sqlite3' if bound else None
    st = core.ActionStore(path=store_path, read_only=True) if store_path is not None else core.ActionStore(read_only=True)
    by_session = {sid: [] for sid in owners}
    if owners and st.path.exists():
        producer = _panel_presenter(core, st, bound=bound)
        for row in producer.views(list(owners.values()), include_withdrawn=True):
            by_session[row['owner']['storedSessionId']].append(row)
    return {sid: group if isinstance(group, Exception) else
            copy.deepcopy([row for own in group for row in by_session[own['storedSessionId']]])
            for sid, group in scopes.items()}


class PanelReconcileResult(list):
    """Deferred owners plus a conservative unchanged-source timing annotation."""
    unchanged = False


def panel_reconcile(home: Path, session: str, lineage, *, install_id=None, profile=None, bound=False) -> list:
    """Source-event producer, separate from read-only panel presentation.

    Returns what stayed deferred, one line per owner ([] = every owner reconciled). One owner's
    unavailable source (a host with no hub projection, a busy store) neither stops the other owners
    nor fails the caller's pass: the caller retries the deferred work, it does not certify it.
    """
    core = _load('core')
    try:
        owners = panel_owners(home, session, lineage, install_id=install_id, profile=profile, bound=bound)
        if bound:
            raise core.ActionError('Explicit-home reconciliation adapter unavailable', 503)
    except core.ActionError as exc:
        if exc.status != 404:
            raise
        return []  # queried absent own session, not failed source or failed reconciliation
    store_path = Path(home) / 'user-steps' / 'actions.sqlite3' if bound else None
    st = core.ActionStore(path=store_path) if store_path is not None else core.ActionStore()
    producer = _load('kanban_mirror').Mirror(core, st)
    deferred = PanelReconcileResult()
    deferred.unchanged = bool(owners)
    for own in owners:
        try:
            producer.reconcile_panel(own)
            deferred.unchanged = deferred.unchanged and producer.panel_remote_unchanged
        except Exception as exc:
            deferred.unchanged = False
            deferred.append(f"{own['storedSessionId']}: {type(exc).__name__}: {exc}"[:300])
    return deferred


def panel_requests(home: Path, session: str, lineage, *, reconcile=True,
                   install_id=None, profile=None, bound=False) -> list:
    """Reconcile the canonical producer before the feed renders; no legacy GET needed.

    The feed's context must be the owning profile. Never turn a supplied filesystem
    path or source reference into write authority for a different profile.
    """
    core = _load('core')
    if bound and reconcile:
        raise core.ActionError('Explicit-home reconciliation adapter unavailable', 503)
    owners = panel_owners(home, session, lineage, install_id=install_id, profile=profile, bound=bound)
    store_path = Path(home) / 'user-steps' / 'actions.sqlite3' if bound else None
    if store_path is not None:
        st = core.ActionStore(path=store_path) if reconcile else core.ActionStore(path=store_path, read_only=True)
    else:
        st = core.ActionStore() if reconcile else core.ActionStore(read_only=True)
    if not reconcile and not st.path.exists():
        return []  # a fresh profile has no producer store; readers must not create it
    producer = _panel_presenter(core, st, bound=bound)
    for own in owners:
        if reconcile:
            producer.reconcile(own)
    return producer.views(owners, include_withdrawn=True)


def create_router(ledger_dir: Optional[Path] = None, state_db: Optional[Path] = None,
                  cards=None, *, action_store=None, owner_resolver=None, receiver=None, kanban_boards=None,
                  session_tasks=None, kanban_mirror=None):
    recovered = set()
    recovery_lock = threading.Lock()
    recovery_tasks = weakref.WeakKeyDictionary()

    def recover_once():
        # The core hot-mount path includes routes AFTER app startup; router
        # lifespan is not entered there. Its outer profile dependency runs before
        # this dependency, and the threadpool copies that request-local context.
        # Key by home, not process launch identity: each served profile owns an outbox.
        key = str(_load('core').home().resolve())
        with recovery_lock:
            if key in recovered and retriers[key].thread.is_alive():
                return
            # Register one coalescing outbox worker for this served profile.
            # Recovery may contact an executor: it must never run on the HTTP
            # admission path, even on a cold hot-mount.
            start_retrier(key)
            recovered.add(key)

    @asynccontextmanager
    async def lifespan(app):
        # Never resume a cold owner or start a competing writer on restart.
        await run_in_threadpool(recover_once)
        try:
            yield
        finally:
            await run_in_threadpool(stop_delivery)

    async def ensure_recovered():
        # A sync dependency would queue before the isolated endpoint can start.
        key = str(_load('core').home().resolve())
        if key not in recovered:
            # Every cold reader awaits the same bounded worker registration,
            # never executor recovery or delivery I/O.
            tasks = recovery_tasks.setdefault(asyncio.get_running_loop(), {})
            task = tasks.get(key)
            if task is None or task.done():
                task = tasks[key] = asyncio.create_task(_on_decision(recover_once))
            await asyncio.shield(task)

    router = APIRouter(lifespan=lifespan, dependencies=[Depends(ensure_recovered)])

    def ledger():
        return _load('core').Ledger(ledger_dir, state_db)

    def store():
        return action_store if action_store is not None else _load('core').ActionStore(
            Path(ledger_dir) / 'actions.sqlite3' if ledger_dir else None)

    def mirror():
        return kanban_mirror if kanban_mirror is not None else _load('kanban_mirror').Mirror(
            _load('core'), store(), boards=kanban_boards)

    def delivery():
        ordinary = receiver if receiver is not None else _load('cards').DecisionDelivery(
            Path(ledger_dir) / 'decision-inbox.sqlite3' if ledger_dir else None)
        return _load('kanban_mirror').Delivery(mirror(), ordinary)

    def owner(session):
        core = _load('core')
        if not session.strip(): raise HTTPException(422, 'Stored session is required')
        if owner_resolver is not None:
            result = owner_resolver(session)
            core.validate_owner(result)
            if result['storedSessionId'] != session: raise HTTPException(403, 'Session mismatch')
            return result
        result = core.server_owner(session)
        # Require a real stored session on THIS profile, not a guessed runtime ID or
        # lineage-root fallback. No branches/siblings silently inherit authorization.
        if not core._row(state_db or core._state_db(), session):
            raise HTTPException(404, 'Unknown stored session on this profile')
        return result

    def run(fn):
        try: return fn()
        except ValueError as exc:
            # The core module may be hot-reloaded, so class identity isn't stable.
            if hasattr(exc, 'status'): raise HTTPException(exc.status, str(exc)) from exc
            raise

    def migrate(own):
        # Only explicitly session-associated ledger entries. Rolling aggregate cards
        # and title similarities are NOT decision aliases. Unknown rows stay passive.
        lg = ledger(); st = store()
        _load('chat').reconcile(_load('core'), st, own, db=state_db)
        for step in lg.read(own['storedSessionId']).get('steps', []):
            if step.get('session') == own['storedSessionId'] and step.get('status') == 'open':
                st.migrate_step(own, step)
        return st

    def recover():
        try:
            st = store()
            for recorded_owner in st.pending_owners():
                try:
                    own = owner(recorded_owner['storedSessionId'])
                    if own == recorded_owner:
                        st.reconcile(own, delivery(), min_retry_interval=max(1.0, RETRY_INTERVAL_S or 15.0))
                except Exception:
                    logging.getLogger('user-steps').warning('Startup reconciliation deferred for an unavailable owner')
        except Exception:
            logging.getLogger('user-steps').warning('Startup reconciliation unavailable; manual retry remains available')
        try:
            # Re-render every row still on the retired generic fallback, idle sessions too.
            core = _load('core')
            store().backfill_legacy(ledger(), gateway=core.server_owner('backfill')['gatewayId'])
        except Exception:
            logging.getLogger('user-steps').warning('Legacy request backfill deferred')

    def retry_pending(max_attempts=RETRY_MAX_ATTEMPTS, *, min_retry_interval=0.0):
        # Re-dispatch failed/pending operations (same operation + idempotency key) for owners this profile serves.
        # A decision made while its session was mid-turn lands here once the turn ends; nobody has to click Retry.
        st = store(); done = 0
        for recorded_owner in st.pending_owners():
            try:
                own = owner(recorded_owner['storedSessionId'])
                if own == recorded_owner:
                    done += len(st.reconcile(own, delivery(), max_attempts=max_attempts,
                                             min_retry_interval=min_retry_interval))
            except Exception:
                continue
        return done

    # One process-owned worker per profile/storage scope, including hot mounts.
    # Injected stores are isolated from other fixture/application instances.
    retriers = {}
    runtime = _load('outbox')
    token = runtime.Token()
    router._delivery_token = token

    def start_retrier(key):
        scope = (key, str(ledger_dir), str(state_db), id(action_store) if action_store is not None else None)
        def autonomous_retry():
            return retry_pending(min_retry_interval=max(1.0, RETRY_INTERVAL_S or 15.0))
        retriers[key] = runtime.acquire(scope, token, recover, autonomous_retry, RETRY_INTERVAL_S)

    def hint_delivery():
        # Notification failure cannot turn a committed decision into HTTP failure.
        # The process worker's periodic scan retains custody even if this hint is lost.
        try:
            key = str(_load('core').home().resolve())
            recover_once()  # restart a dead worker; never strand a committed hint
            retriers[key].wake.set()
        except Exception:
            logging.getLogger('user-steps').exception('Committed decision delivery hint deferred')

    def stop_delivery():
        with recovery_lock:
            workers = list(retriers.values())
            retriers.clear()
            recovered.clear()
        for worker in workers:
            runtime.release(worker, token)

    router.retry_pending = retry_pending
    router.stop_delivery = stop_delivery

    def retire_moot(st, own):
        # The queue is never staler than the board: settle-linked asks are withdrawn before listing.
        # A failure here must not hide the queue itself.
        try:
            _load('core').reconcile_moot(st, ledger(), own, boards=kanban_boards)
        except Exception:
            logging.getLogger('user-steps').exception('Moot-ask reconciliation failed')

    @router.get('/ping')
    def ping():
        out = {'ok': True, 'api': API_VERSION, 'features': ['withdraw', 'receipts']}
        try:
            # Requests still up after their question was answered (desktop-1321064734); >0 needs a look.
            out['stale'] = _load('chat').stale_counts(_load('core'), store(), db=state_db)
        except Exception:
            out['stale'] = None
        return out

    @router.get('/requests')
    async def requests(request: Request, session: str, include: str = ''):
        # Withdrawn requests are omitted unless asked for: older Desktop builds reject unknown states.
        def action():
            own = owner(session)
            try:
                producer = mirror()
                poll = getattr(producer, 'reconcile_poll', None)  # an injected mirror without it: full pass
                poll(own, FEED_FRESH_S) if poll is not None else producer.reconcile(own)
            except (ValueError, sqlite3.Error, OSError):
                logging.getLogger('user-steps').exception('Card projection reconciliation deferred')
            st = migrate(own)
            retire_moot(st, own)
            presenter = mirror()
            return {'requests': [presenter.present(r) for r in st.views(own, include_withdrawn=include == 'withdrawn')]}
        # Desktop's queue GET has a 10s total budget; the shared lane default is
        # 25s. Refuse unstarted work promptly instead of consuming that budget
        # waiting behind unrelated feed reads. This does not interrupt admitted
        # reconciliation or turn an unavailable queue into an empty one.
        return await _on_lane('feed', run, action, request=request, deadline=0.75)

    @router.get('/requests/{request_id}')
    async def request(request_id: str, session: str):
        return await _on_decision(run, lambda: {'request': mirror().present(store().view(owner(session), request_id))})

    @router.post('/requests/{request_id}/withdraw')
    def withdraw(request_id: str, body: WithdrawBody):
        def action():
            own = owner(body.session)
            return {'request': _load('core').withdraw_request(
                store(), ledger(), own, request_id, by='user', expected_revision=body.expectedRevision,
                reason=(body.reason or '').strip() or 'Dismissed as not needed')}
        return run(action)

    @router.post('/requests/{request_id}/decisions')
    async def decide(request_id: str, body: DecisionBody):
        received = time.monotonic()
        def action():
            started = time.monotonic()
            own = owner(body.session); st = store()
            payload = body.model_dump(exclude={'session'})
            payload['requestId'] = request_id
            # Only this producer can supply the supplemental informational choice.
            producer = mirror()
            recorded = (producer.decide_other(own, payload)
                        if payload['choiceId'] == 'answer-text' and producer.binding(own, request_id)
                        else st.decide(own, payload))  # durable commit BEFORE receiver effects
            committed = time.monotonic()
            # A committed operation is the receipt. Executor delivery is durable
            # outbox work and may take seconds; a lost hint is recovered by scans.
            hint_delivery()
            logging.getLogger('user-steps').info(
                'Decision timing operation=%s queue_ms=%.1f record_ms=%.1f hint_ms=%.1f',
                recorded['operation']['operationId'], (started-received)*1000,
                (committed-started)*1000, (time.monotonic()-committed)*1000)
            return recorded
        return await _on_decision(run, action)

    @router.post('/client-timing')
    def client_timing(body: ClientTimingBody):
        def action():
            # Telemetry is read-only and can name only an operation of this owner.
            store().operation(owner(body.session), body.operation)
            logging.getLogger('user-steps').info(
                'Decision client timing operation=%s owner_ms=%.1f post_ms=%.1f refresh_ms=%.1f paint_ms=%.1f total_ms=%.1f%s',
                body.operation, body.posted_ms, body.responded_ms-body.posted_ms,
                body.refreshed_ms-body.responded_ms, max(0, body.painted_ms-body.refreshed_ms), body.painted_ms,
                '' if body.visible_ms is None else f' visible_ms={body.visible_ms:.1f}')
            return {'ok': True}
        return run(action)

    @router.post('/requests/{request_id}/reconcile-chat')
    def reconcile_chat(request_id: str, body: ChatReconcileBody):
        def action():
            own = owner(body.session); st = store()
            results = _load('chat').reconcile(_load('core'), st, own,
                db=state_db, request_id=request_id, expected_revision=body.expectedRevision)
            return {'request': st.get(own, request_id), 'results': results}
        return run(action)

    @router.get('/lists/{source_id}')
    def list_snapshot(source_id: str, session: str):
        # Exact-session read of a frozen numbered list. Owner is the mounted/authenticated owner of ``session``;
        # no lineage-derived access.
        return run(lambda: {'list': store().list_snapshot(owner(session), source_id)})

    @router.post('/lists/{source_id}/answers')
    def answer_list(source_id: str, body: ListAnswerBody):
        def action():
            own = owner(body.session); st = store()
            recorded = st.answer_numbered(own, source_id, body.snapshotDigest, body.n, body.idempotencyKey)
            return st.dispatch(own, recorded['operation']['operationId'], delivery())
        return run(action)

    def present_operation(result):
        # Decorate only the readback view, never the operation's frozen intent.
        if result.get('request'):
            return dict(result, request=mirror().present(result['request']))
        return result

    @router.get('/operations')
    async def operation_by_key(session: str, idempotencyKey: str):
        return await _on_decision(run, lambda: present_operation(store().operation(owner(session), key=idempotencyKey)))

    @router.get('/operations/{operation_id}')
    async def operation(operation_id: str, session: str):
        return await _on_decision(run, lambda: present_operation(store().operation(owner(session), oid=operation_id)))

    @router.get('/operations/{operation_id}/receipts')
    async def receipts(operation_id: str, session: str):
        # Read only: owner receipts are written by the owning agent through its action_request tool (server-derived
        # owner), never through this human surface, so the UI cannot claim an acknowledgment or application.
        return await _on_decision(run, lambda: store().receipts(owner(session), operation_id))

    @router.post('/operations/{operation_id}/retry')
    async def retry(operation_id: str, session: str):
        return await _on_decision(run, lambda: store().dispatch(owner(session), operation_id, delivery()))

    @router.post('/reconcile')
    def reconcile(session: str):
        return run(lambda: {'results': store().reconcile(owner(session), delivery())})

    @router.get('/steps')
    async def steps(request: Request, session: str = ''):
        # Compatibility read only; old callers cannot mutate through /resolve.
        return await _on_lane('feed', lambda: _load('core').steps_payload(ledger(), session), request=request)

    @router.get('/step')
    async def step(request: Request, id: str, s: str = ''):
        if not s: return {'root': None, 'step': None}
        # Desktop polls this per open step; lineage_root reads state.db, which can wait on a lock. On the
        # shared pool it queued behind slow reads (101 Desktop timeouts in 30 min, 2026-11-22): feed lane.
        def read():
            lg = ledger(); root = lg.root(s)
            found = next((x for x in lg._read(root).get('steps', [])
                          if x.get('id') == id and x.get('session') == s), None)
            return {'root': root, 'step': _load('core').public_step(found) if found else None}
        return await _on_lane('feed', read, request=request)

    def _session_cards_read(ids: str):
        # Every card the lineage created or shepherds, finished ones included, for the Desktop session panel's
        # progress counts (core's /api/session-kanban-tasks lists only unfinished ones).
        wanted = [s.strip() for s in ids.split(',') if s.strip()][:50]
        # 'includes' tells the client this list has finished cards (core's list never does).
        if not wanted:
            return {'tasks': [], 'includes': ['done', 'archived']}
        read = session_tasks
        if read is None:
            try:
                from hermes_cli import kanban_session_activity as ksa
            except ImportError:
                raise HTTPException(501, 'This backend has no kanban session reader')
            def read(ids):
                tasks = ksa.session_tasks(ids, tuple(ksa.LISTED_STATUSES) + ('done', 'archived'), stale_ok=True)
                # The public ledger projection is already scoped to the mounted gateway/profile.
                # Unlike replica_cards it retains released receipts. Only settled terminal rows
                # are history: reported_status alone must NEVER retire an unknown obligation.
                history_reader = getattr(ksa, 'remote_links_and_origin_rows', None)
                if history_reader is None:
                    return tasks
                try:
                    # This route is stale-only (session_tasks above): the history read must not wait on the
                    # global projection lock either. Cores before stale_ok keep their strict call.
                    if _accepts_stale_ok(history_reader):
                        _, history = history_reader(stale_ok=True)
                    else:
                        _, history = history_reader()
                except Exception:
                    logging.getLogger('user-steps').warning('Session card history unavailable', exc_info=True)
                    return tasks
                return _with_terminal_history(tasks, history, ids)
        return {'tasks': read(wanted), 'includes': ['done', 'archived']}

    @router.get('/session-cards')
    async def session_cards(request: Request, ids: str):
        return await _on_lane('feed', _session_cards_read, ids, request=request)

    @router.post('/resolve')
    def resolve():
        raise HTTPException(410, 'Legacy resolution is read-only; request a complete canonical action.')

    return router


router = create_router()
