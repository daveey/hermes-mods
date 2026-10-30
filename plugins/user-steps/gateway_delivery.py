"""Admit local-platform decisions into the existing gateway's durable FIFO.

No worker is resumed or created here. The authority owns routing, persistence,
queueing and execution; operationId is the producer's durable request identity.
"""
import asyncio
import hashlib
import json


class GatewayDeliveryError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _identity(params):
    return json.dumps(['user-steps', params['operation_id']], separators=(',', ':'))


def _runtime():
    from hermes_cli import plugins
    from hermes_constants import get_hermes_home
    from gateway.session_authorities import authority_for_home
    published = plugins._published_gateway_message_injector
    if published is None:
        raise GatewayDeliveryError('gateway_owner_unavailable')
    runner = published[0]
    authority = authority_for_home(runner, get_hermes_home())
    if authority is None:
        raise GatewayDeliveryError('gateway_profile_unavailable')
    return runner, authority


def _receipt(authority, params, row):
    if row is None:
        return {'status': 'absent'}
    payload = row['payload']
    descriptor = payload.get('local_automation_v1') or {}
    text = payload.get('text')
    digest = hashlib.sha256(json.dumps({'stored_session_id': params['stored_session_id'], 'text': text},
        sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    if (digest != params['payload_digest'] or descriptor.get('identity') != _identity(params)
            or descriptor.get('owner') != params['stored_session_id']
            or row['request_id'] != _identity(params)
            or row['principal_id'] != 'automation:' + descriptor.get('route', '')):
        raise GatewayDeliveryError('gateway_admission_mismatch')
    if row.get('outcome') == 'cancelled':
        dispatch = 'cancelled'
    elif row.get('outcome') in ('failed', 'error', 'rejected') or row.get('status') == 'unknown':
        dispatch = 'failed'
    else:
        dispatch = 'queued'
    return {k: params[k] for k in ('profile', 'stored_session_id', 'operation_id', 'payload_digest')} | {
        'status': 'accepted', 'dispatch': dispatch, 'admission_id': row['admission_id'],
        'transport': 'gateway_fifo'}


def call(method, params):
    runner, authority = _runtime()
    # The caller already checked the authenticated install/profile. The authority
    # lookup is profile-home scoped, never a search through other profiles.
    async def dispatch():
        from hermes_state_runtime import find_session_admission
        sid = params['stored_session_id']
        live = authority.sessions.get(sid)
        if live is None:
            from gateway.session_local_recovery import restore_local_session
            restore_local_session(authority, sid)
            live = authority.sessions[sid]
        # Read an immutable existing receipt before checking today's routing.
        # A reset after an acknowledged commit must not strand lost-response retries.
        row = await asyncio.to_thread(find_session_admission, authority.db,
            principal_id='automation:' + live.route, session_id=sid, request_id=_identity(params))
        if row is not None:
            return _receipt(authority, params, row)
        # Compression retains the logical chat/operation identity; a different
        # conversation is never an acceptable decision target.
        from gateway.session_detached import route_entry
        entry = route_entry(authority, live.route)
        if (entry is None or authority.logical_owner(sid) != sid
                or (entry.session_id != sid and authority.db.get_compression_tip(sid) != entry.session_id)):
            raise GatewayDeliveryError('gateway_target_rotated')
        if method == 'prompt.admit':
            from gateway.platforms.event import MessageEvent, MessageType
            event = MessageEvent(text=params['text'], message_type=MessageType.TEXT, source=live.source,
                internal=True, allow_gateway_control=False, message_id=_identity(params),
                metadata={'gateway_session_key': live.route, 'gateway_session_id': entry.session_id})
            # The core validates the live source and commits the immutable input
            # under UNIQUE(principal, target, request) before scheduling its drain.
            async with runner._plugin_injection_scope(live.source):
                await authority.admit_automation(runner._adapter_for_source(live.source), event, _identity(params))
            row = await asyncio.to_thread(find_session_admission, authority.db,
                principal_id='automation:' + live.route, session_id=sid, request_id=_identity(params))
        return _receipt(authority, params, row)

    loop = runner._gateway_loop
    if loop is None or loop.is_closed() or not runner._running:
        raise GatewayDeliveryError('gateway_owner_unavailable')
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    if current is loop:
        raise GatewayDeliveryError('gateway_off_loop_required')
    # A timeout never cancels the committed input: retry first reads the same key.
    return asyncio.run_coroutine_threadsafe(dispatch(), loop).result(timeout=20)
