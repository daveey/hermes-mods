"""Final terminal results enter the owning gateway's existing durable FIFO.

The job directory is a producer outbox; the authority's admission receipt is the
exactly-once boundary. Never synthesize viewer input or execute from a watchdog.

Routing (``resolve``): the job records only the owning session id. Its logical
owner (compression root) is resolved to the route that conversation is on now:

* LOCAL gateway chats (Desktop/CLI/GUI over the gateway) and messaging
  conversations a chat moved past (detached) ride ``local_automation_v1`` on the
  live session's own source, restoring a cold local receipt first.
* Native messaging routes borrow the connector provenance of the route's last
  committed human turn (as core's route wakes and process completions do),
  re-checked against today's adapter, profile and authorization.
* Previously adopted legacy CLI/TUI history uses the same local receipt, even
  with no connected viewer. Unadopted inline history enters core's trusted
  producer ingress without a published gateway, including while an inline owner
  is active. Core queues the notice for its owning runtime; this plugin neither
  adopts the session nor executes its turn. Routed sessions retain the gateway
  authority, and moved routes stay pending rather than being marked delivered.

Retries are classified. ``Unavailable`` (process-local or transient) and
``Blocked`` (route/support) both leave the outbox pending; a job-specific failure
records a per-job backoff in ``completion-pending.json`` and each distinct reason
is logged once per job.
"""
import asyncio
import concurrent.futures
import fcntl
import json
import logging
import os
import time
from pathlib import Path
from types import SimpleNamespace

log = logging.getLogger('user_terminal.completion')

PENDING = 'completion-pending.json'
BACKOFF_BASE_S = 15.0
BACKOFF_CAP_S = {'transient': 120.0, 'blocked': 600.0}
# Store refusals that clear by themselves (drain, restart, writer contention).
TRANSIENT_REASONS = frozenset({'runtime_draining', 'stale_epoch', 'stale_generation', 'storage_unavailable',
                               'queue_full', 'revision_conflict', 'admission_started', 'unknown_execution'})


class Unavailable(RuntimeError):
    """Not admitted now; the reconciler retries. ``kind``: transient | blocked."""
    kind = 'transient'

    def __init__(self, reason, detail=''):
        super().__init__(f'{reason}: {detail}' if detail else reason)
        self.reason, self.detail = reason, detail


class Blocked(Unavailable):
    """The route cannot take the notice as things stand (unsupported surface, moved route,
    unauthorized sender, disconnected adapter). Pending, logged once, slower retry."""
    kind = 'blocked'


def runtime(home):
    from hermes_cli import plugins
    from gateway.session_authorities import authority_for_home
    published = plugins._published_gateway_message_injector
    if published is None:
        raise Unavailable('gateway owner unavailable')
    runner = published[0]
    authority = authority_for_home(runner, home)
    if authority is None:
        raise Unavailable('profile owner unavailable')
    return runner, authority


def save(path, value):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def _read_json(path):
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def classify(exc):
    """The retry class of a failed attempt, as an ``Unavailable``."""
    if isinstance(exc, Unavailable):
        return exc
    from hermes_state_runtime import RuntimeStoreError
    if isinstance(exc, RuntimeStoreError):
        detail = ' '.join(x for x in (exc.field, exc.detail) if x)
        return (Unavailable if exc.reason in TRANSIENT_REASONS else Blocked)('store_' + exc.reason, detail)
    if isinstance(exc, (TimeoutError, concurrent.futures.TimeoutError)):
        return Unavailable('admission_timeout', 'the gateway loop did not answer in time')
    if isinstance(exc, (concurrent.futures.CancelledError, asyncio.CancelledError)):
        return Unavailable('admission_cancelled', 'the gateway loop cancelled the attempt')
    import sqlite3
    if isinstance(exc, sqlite3.OperationalError):
        return Unavailable('sqlite_busy', str(exc)[:200])
    return Blocked('unexpected_' + type(exc).__name__, str(exc)[:200])


def _defer(job, err, cause=None):
    """Record a failed attempt and its backoff; log each distinct reason once per job."""
    path = job / PENDING
    prior = _read_json(path) or {}
    now = time.time()
    attempts = int(prior.get('attempts') or 0) + 1
    delay = min(BACKOFF_CAP_S[err.kind], BACKOFF_BASE_S * 2 ** min(attempts - 1, 10))
    logged = list(prior.get('logged') or [])
    key = f'{err.kind}:{err.reason}'
    if key not in logged:
        logged.append(key)
        if cause is not None and cause is not err and err.reason.startswith('unexpected_'):
            log.warning('user_terminal: completion %s pending (%s)', job.name, err, exc_info=cause)
        elif err.kind == 'blocked':
            log.warning('user_terminal: completion %s pending (%s); retrying with backoff', job.name, err)
        else:
            log.info('user_terminal: completion %s pending (%s); retrying', job.name, err)
    save(path, {'kind': err.kind, 'reason': err.reason, 'detail': err.detail, 'attempts': attempts,
                'first_at': prior.get('first_at') or now, 'last_at': now, 'next_at': now + delay,
                'logged': logged})


def _admitted_row(db, owner, identity):
    """This producer's admission on *owner*, whichever route principal admitted it (a route
    that moved after admission must not hide it and cause a second one)."""
    from hermes_state_runtime import list_session_admissions
    for row in list_session_admissions(db, session_id=owner, pending_only=False):
        if row['request_id'] == identity and str(row['principal_id']).startswith('automation:'):
            return row
    return None


def _has_local_receipt(db, sid):
    from hermes_state_local import local_receipt
    from hermes_state_runtime import RuntimeStoreError
    try:
        local_receipt(db, sid)
    except RuntimeStoreError:
        return False
    return True


def _recorded_route(db, sid):
    """The gateway route recorded on the conversation's transcript tip (or its root)."""
    tip = db.get_compression_tip(sid) or sid
    for candidate in dict.fromkeys((tip, sid)):
        row = db.get_session(candidate) or {}
        if row.get('session_key'):
            return row['session_key']
    return None


def _live_local(authority, sid):
    from gateway.session_local_recovery import restore_local_session
    live = authority.sessions.get(sid)
    if live is None:
        restore_local_session(authority, sid)
        live = authority.sessions[sid]
    return live


def resolve(runner, authority, sid):
    """Where the logical conversation *sid* takes a producer turn now. Runs on the gateway loop
    (a cold local restore publishes into the authority's live map)."""
    from gateway.config import Platform
    from gateway.session_detached import detached_route_of, route_entry
    db = authority.db
    live = authority.sessions.get(sid)
    if live is None and _has_local_receipt(db, sid):
        live = _live_local(authority, sid)
    route = live.route if live is not None and live.route else _recorded_route(db, sid)
    if not route:
        raise Blocked('no_gateway_route', 'the session has no gateway route (legacy inline TUI or a '
                      'non-gateway session); there is no durable producer admission path for it')
    entry = route_entry(authority, route)
    if entry is None or entry.origin is None:
        raise Blocked('route_not_found', route)
    if entry.suspended:
        raise Blocked('route_suspended', route)
    if authority.logical_owner(entry.session_id) != sid:
        # /new, /resume or a handoff moved the route on; the closed conversation fails closed,
        # as core's own pinned completions do.
        raise Blocked('route_moved', 'the route now names another conversation')
    if live is not None and detached_route_of(live.source):
        return SimpleNamespace(kind='local', owner=sid, route=route, entry=entry, live=live)
    if entry.origin.platform == Platform.LOCAL:
        owner = entry.origin.chat_id  # the local receipt the FIFO is keyed by (kept across /new)
        live = _live_local(authority, owner)
        if live.route != route:
            raise Blocked('route_moved', 'the local session is on another route')
        return SimpleNamespace(kind='local', owner=owner, route=route, entry=entry, live=live)
    if entry.origin.platform == Platform.API_SERVER:
        raise Blocked('unsupported_surface', 'api_server sessions take no producer admissions')
    return SimpleNamespace(kind='native', owner=sid, route=route, entry=entry, live=live)


def _owned_profile(runner, route, home):
    """Under multiplexing a messaging route belongs to the profile its key names; it must be
    the job's own (the plugin-injection boundary)."""
    if getattr(getattr(runner, 'config', None), 'multiplex_profiles', False) is not True:
        return True
    from hermes_state_keys import profile_from_session_key
    served = runner._served_profile_for_home(home)
    return served is not None and profile_from_session_key(route) == served


async def _admit_local(runner, authority, target, payload, home):
    from gateway.platforms.event import MessageEvent, MessageType
    live = target.live
    adapter = runner._adapter_for_source(live.source)
    if adapter is None:
        raise Blocked('adapter_unavailable', 'no local session adapter')
    event = MessageEvent(text=payload['text'], message_type=MessageType.TEXT,
        source=live.source, internal=True, allow_gateway_control=False,
        message_id=payload['identity'],
        metadata={'gateway_session_key': live.route, 'gateway_session_id': target.entry.session_id})
    async with runner._plugin_injection_scope(live.source, home=home):
        await authority.admit_automation(adapter, event, payload['identity'])


async def _admit_native(runner, authority, target, payload, home):
    from gateway.platforms.event import MessageEvent, MessageType
    from gateway.session_automation import committed_route_envelope
    from gateway.session_envelope import restore_native
    if not _owned_profile(runner, target.route, home):
        raise Blocked('foreign_profile', "the route belongs to another profile than the job's")
    envelope = await asyncio.to_thread(committed_route_envelope, authority, target.entry)
    if envelope is None:
        raise Blocked('no_committed_turn', 'the route has no committed human turn to borrow provenance from')
    # The route's committed source, which ``snapshot_automation`` re-derives and compares.
    restored = restore_native({'text': payload['text'], 'native_text_v1': {
        k: v for k, v in envelope.items() if k != 'media'}}, runner)
    source = restored.source
    adapter = runner._adapter_for_source(source)
    if adapter is None:
        raise Blocked('adapter_unavailable', f'no connected {source.platform.value} adapter')
    # Host code never widens access: today's allowlists must still admit the borrowed sender.
    if not runner._is_user_authorized_for_source(source, allow_adapter_delegation=False):
        raise Blocked('unauthorized', 'the route sender is not authorized by current gateway policy')
    event = MessageEvent(text=payload['text'], message_type=MessageType.TEXT, source=source, internal=True,
        allow_gateway_control=False, message_id=payload['identity'],
        metadata={'gateway_session_key': target.route, 'gateway_session_id': target.entry.session_id})
    async with runner._plugin_injection_scope(source, home=home):
        await authority.admit_automation(adapter, event, payload['identity'])


async def admit(runner, authority, payload):
    db = authority.db
    home = Path(db.db_path).parent
    sid = await asyncio.to_thread(authority.logical_owner, payload['session_id'])
    identity = payload['identity']
    # Missing is terminal only after consulting the actual profile authority.
    if await asyncio.to_thread(db.get_session, sid) is None:
        return {'status': 'missing', 'session_id': sid}
    owner = sid
    row = await asyncio.to_thread(_admitted_row, db, sid, identity)
    if row is None:
        target = resolve(runner, authority, sid)
        owner = target.owner
        if owner != sid:
            row = await asyncio.to_thread(_admitted_row, db, owner, identity)
        if row is None:
            admit_target = _admit_local if target.kind == 'local' else _admit_native
            await admit_target(runner, authority, target, payload, home)
            row = await asyncio.to_thread(_admitted_row, db, owner, identity)
    if row is None:
        raise Unavailable('admission_not_committed')
    if row['payload'].get('text') != payload['text'] or row['request_id'] != identity:
        raise Blocked('admission_payload_mismatch')
    return {'status': 'admitted', 'admission_id': row['admission_id'], 'session_id': owner}


def admit_producer(payload, home):
    """Core owns legacy routing and durability; no gateway publication is needed to queue.

    None means routed, not delivered. Read back the same producer after admission
    (and before a retry) so a lost acknowledgment cannot create a second notice.
    """
    try:
        from hermes_state_producer_admission import (
            admit_producer_completion, read_producer_admission, payload_digest)
    except ImportError as exc:
        raise Unavailable('producer_ingress_unavailable') from exc
    args = {'producer': payload['identity'], 'profile_home': Path(home)}
    result = read_producer_admission(**args)
    if result['outcome'] == 'absent':
        result = admit_producer_completion(**args, stored_session_id=payload['session_id'], text=payload['text'])
        if result['outcome'] == 'admitted':
            result = read_producer_admission(**args)
    outcome = result['outcome']
    if outcome == 'admitted':
        if result.get('payload_digest') != payload_digest(payload['text']):
            raise Blocked('admission_payload_mismatch')
        return {'status': 'admitted', 'session_id': result['owner'],
                'producer': payload['identity'], 'ingress': 'producer_completion'}
    if outcome == 'routed':
        return None
    if outcome == 'missing':
        return {'status': 'missing', 'session_id': payload['session_id'], 'reason': result.get('reason')}
    if outcome == 'retry':
        raise Unavailable('producer_' + result.get('reason', 'retry'))
    raise Unavailable('producer_admission_not_committed')


def deliver(job, status, owner, home, clean_output):
    if owner.get('kind') != 'session' or not owner.get('session_id'):
        return
    receipt = job / 'completion-receipt.json'
    if receipt.exists():
        return
    pending = _read_json(job / PENDING)
    if pending and float(pending.get('next_at') or 0) > time.time():
        raise Unavailable('backoff')
    with (job / 'completion.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if receipt.exists():
            return
        outbox = job / 'completion-payload.json'
        if outbox.exists():
            payload = json.loads(outbox.read_text())
        else:
            output = job / 'output.log'
            tail = ''
            if output.exists():
                with output.open('rb') as stream:
                    stream.seek(0, 2)
                    stream.seek(max(0, stream.tell() - 16384))
                    tail = clean_output(stream.read().decode('utf-8', errors='replace'))[-6000:]
            payload = {'session_id': owner['session_id'],
                'identity': json.dumps(['user-terminal', job.name, 'final'], separators=(',', ':')),
                'text': (f'[User-terminal completion — tool result, not user input]\n'
                         f'job_id: {job.name}\nstatus: {status["status"]}\n'
                         f'exit_code: {status.get("exit_code")}\nOutput tail:\n{tail}')}
            save(outbox, payload)
        try:
            result = admit_producer(payload, home)
            if result is None:  # Core says routed: retain the gateway's existing authority/FIFO.
                runner, authority = runtime(home)
                loop = runner._gateway_loop
                if loop is None or loop.is_closed() or not runner._running:
                    raise Unavailable('gateway loop unavailable')
                try:
                    current = asyncio.get_running_loop()
                except RuntimeError:
                    current = None
                if current is loop:
                    raise Unavailable('delivery requires off-loop caller')
                # Timeout does not cancel an admitted turn. A retry consults the same key.
                future = asyncio.run_coroutine_threadsafe(admit(runner, authority, payload), loop)
                result = future.result(timeout=20)
        except Exception as exc:
            err = classify(exc)
            _defer(job, err, exc)
            if err is exc:
                raise
            raise err from exc
        save(receipt, result)
        prior = _read_json(job / PENDING)
        if prior:
            log.info('user_terminal: completion %s %s after %s deferred attempt(s)', job.name,
                     result.get('status'), prior.get('attempts'))
            (job / PENDING).unlink(missing_ok=True)
