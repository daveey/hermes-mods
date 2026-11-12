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
import os
import sqlite3
import sys
import time
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

API_VERSION = 2
# Background re-delivery of decisions refused because the session was mid-turn. ~1h of attempts, then manual Retry.
RETRY_INTERVAL_S = float(os.environ.get('HERMES_USER_STEPS_RETRY_S', '15'))
RETRY_MAX_ATTEMPTS = 240


def _load(name: str):
    key = f"_user_steps_{name}"
    path = Path(__file__).resolve().parents[1] / f"{name}.py"
    mtime = path.stat().st_mtime_ns
    mod = sys.modules.get(key)
    if mod is None or getattr(mod, "_mtime", None) != mtime:
        spec = importlib.util.spec_from_file_location(key, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        setattr(mod, "_mtime", mtime)
        sys.modules[key] = mod
    return mod


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session: StrictStr
    expectedRevision: StrictInt
    choiceId: StrictStr
    revisionText: Optional[StrictStr] = None
    idempotencyKey: StrictStr


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


def create_router(ledger_dir: Optional[Path] = None, state_db: Optional[Path] = None,
                  cards=None, *, action_store=None, owner_resolver=None, receiver=None, kanban_boards=None,
                  session_tasks=None, kanban_mirror=None):
    recovered = set()
    recovery_lock = threading.Lock()

    def recover_once():
        # The core hot-mount path includes routes AFTER app startup; router
        # lifespan is not entered there. Its outer profile dependency runs before
        # this dependency, and the threadpool copies that request-local context.
        # Key by home, not process launch identity: each served profile owns an outbox.
        key = str(_load('core').home().resolve())
        with recovery_lock:
            if key in recovered:
                return
            recover()
            recovered.add(key)
            start_retrier(key)

    @asynccontextmanager
    async def lifespan(app):
        # Never resume a cold owner or start a competing writer on restart.
        await run_in_threadpool(recover_once)
        yield

    router = APIRouter(lifespan=lifespan, dependencies=[Depends(recover_once)])

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
                        st.reconcile(own, delivery())
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

    def retry_pending(max_attempts=RETRY_MAX_ATTEMPTS):
        # Re-dispatch failed/pending operations (same operation + idempotency key) for owners this profile serves.
        # A decision made while its session was mid-turn lands here once the turn ends; nobody has to click Retry.
        st = store(); done = 0
        for recorded_owner in st.pending_owners():
            try:
                own = owner(recorded_owner['storedSessionId'])
                if own == recorded_owner:
                    done += len(st.reconcile(own, delivery(), max_attempts=max_attempts))
            except Exception:
                continue
        return done

    retriers = set()

    def start_retrier(key):
        if RETRY_INTERVAL_S <= 0 or key in retriers: return
        retriers.add(key)
        ctx = contextvars.copy_context()  # keep the request's profile scope for owner/store resolution

        def loop():
            while True:
                time.sleep(RETRY_INTERVAL_S)
                try: ctx.run(retry_pending)
                except Exception:
                    logging.getLogger('user-steps').warning('Decision delivery retry pass failed')
        threading.Thread(target=loop, name='user-steps-retrier', daemon=True).start()

    router.retry_pending = retry_pending

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
    def requests(session: str, include: str = ''):
        # Withdrawn requests are omitted unless asked for: older Desktop builds reject unknown states.
        def action():
            own = owner(session)
            try:
                mirror().reconcile(own)
            except (ValueError, sqlite3.Error, OSError):
                logging.getLogger('user-steps').exception('Card projection reconciliation deferred')
            st = migrate(own)
            _load('chat').reconcile(_load('core'), st, own, db=state_db)
            retire_moot(st, own)
            presenter = mirror()
            return {'requests': [presenter.present(r) for r in st.views(own, include_withdrawn=include == 'withdrawn')]}
        return run(action)

    @router.get('/requests/{request_id}')
    def request(request_id: str, session: str):
        return run(lambda: {'request': mirror().present(store().view(owner(session), request_id))})

    @router.post('/requests/{request_id}/withdraw')
    def withdraw(request_id: str, body: WithdrawBody):
        def action():
            own = owner(body.session)
            return {'request': _load('core').withdraw_request(
                store(), ledger(), own, request_id, by='user', expected_revision=body.expectedRevision,
                reason=(body.reason or '').strip() or 'Dismissed as not needed')}
        return run(action)

    @router.post('/requests/{request_id}/decisions')
    def decide(request_id: str, body: DecisionBody):
        def action():
            own = owner(body.session); st = store()
            payload = body.model_dump(exclude={'session'})
            payload['requestId'] = request_id
            # Only this producer can supply the supplemental informational choice.
            producer = mirror()
            recorded = (producer.decide_other(own, payload)
                        if payload['choiceId'] == 'answer-text' and producer.binding(own, request_id)
                        else st.decide(own, payload))  # durable commit BEFORE receiver effects
            return st.dispatch(own, recorded['operation']['operationId'], delivery())
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
    def operation_by_key(session: str, idempotencyKey: str):
        return run(lambda: present_operation(store().operation(owner(session), key=idempotencyKey)))

    @router.get('/operations/{operation_id}')
    def operation(operation_id: str, session: str):
        return run(lambda: present_operation(store().operation(owner(session), oid=operation_id)))

    @router.get('/operations/{operation_id}/receipts')
    def receipts(operation_id: str, session: str):
        # Read only: owner receipts are written by the owning agent through its action_request tool (server-derived
        # owner), never through this human surface, so the UI cannot claim an acknowledgment or application.
        return run(lambda: store().receipts(owner(session), operation_id))

    @router.post('/operations/{operation_id}/retry')
    def retry(operation_id: str, session: str):
        return run(lambda: store().dispatch(owner(session), operation_id, delivery()))

    @router.post('/reconcile')
    def reconcile(session: str):
        return run(lambda: {'results': store().reconcile(owner(session), delivery())})

    @router.get('/steps')
    def steps(session: str = ''):
        # Compatibility read only; old callers cannot mutate through /resolve.
        return _load('core').steps_payload(ledger(), session)

    @router.get('/step')
    def step(id: str, s: str = ''):
        if not s: return {'root': None, 'step': None}
        lg = ledger(); root = lg.root(s)
        found = next((x for x in lg._read(root).get('steps', [])
                      if x.get('id') == id and x.get('session') == s), None)
        return {'root': root, 'step': _load('core').public_step(found) if found else None}

    @router.get('/session-cards')
    def session_cards(ids: str):
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

    @router.post('/resolve')
    def resolve():
        raise HTTPException(410, 'Legacy resolution is read-only; request a complete canonical action.')

    return router


router = create_router()
