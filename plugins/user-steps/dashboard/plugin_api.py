"""Canonical ActionRequest v2 API, mounted at /api/plugins/user-steps.

Authentication is provided by the owning dashboard/profile mount. Namespace comes
from server configuration, never a payload. The optional owner_resolver is a server
integration seam for authenticated connection principals, not an HTTP parameter.
Legacy reads remain passive; generic resolve is intentionally gone (410).
"""
import contextvars
import importlib.util
import os
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


class ChatReconcileBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session: StrictStr
    expectedRevision: StrictInt


def create_router(ledger_dir: Optional[Path] = None, state_db: Optional[Path] = None,
                  cards=None, *, action_store=None, owner_resolver=None, receiver=None):
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

    def delivery():
        return receiver if receiver is not None else _load('cards').DecisionDelivery(
            Path(ledger_dir) / 'decision-inbox.sqlite3' if ledger_dir else None)

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

    @router.get('/ping')
    def ping():
        return {'ok': True, 'api': API_VERSION}

    @router.get('/requests')
    def requests(session: str):
        def action():
            own = owner(session)
            st = migrate(own)
            _load('chat').reconcile(_load('core'), st, own, db=state_db)
            return {'requests': st.requests(own)}
        return run(action)

    @router.get('/requests/{request_id}')
    def request(request_id: str, session: str):
        return run(lambda: {'request': store().get(owner(session), request_id)})

    @router.post('/requests/{request_id}/decisions')
    def decide(request_id: str, body: DecisionBody):
        def action():
            own = owner(body.session); st = store()
            payload = body.model_dump(exclude={'session'})
            payload['requestId'] = request_id
            recorded = st.decide(own, payload)  # durable commit BEFORE any receiver effects
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

    @router.get('/operations')
    def operation_by_key(session: str, idempotencyKey: str):
        return run(lambda: store().operation(owner(session), key=idempotencyKey))

    @router.get('/operations/{operation_id}')
    def operation(operation_id: str, session: str):
        return run(lambda: store().operation(owner(session), oid=operation_id))

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

    @router.post('/resolve')
    def resolve():
        raise HTTPException(410, 'Legacy resolution is read-only; request a complete canonical action.')

    return router


router = create_router()
