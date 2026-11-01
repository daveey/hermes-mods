"""Final terminal results enter the owning gateway's existing durable FIFO.

The job directory is a producer outbox; the authority's admission receipt is the
exactly-once boundary. Never synthesize viewer input or execute from a watchdog.
"""
import asyncio
import fcntl
import json
import os
from pathlib import Path


class Unavailable(RuntimeError):
    pass


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


async def admit(runner, authority, payload):
    from hermes_state_runtime import find_session_admission
    from gateway.session_local_recovery import restore_local_session
    from gateway.session_detached import route_entry
    from gateway.platforms.event import MessageEvent, MessageType
    sid = await asyncio.to_thread(authority.logical_owner, payload['session_id'])
    identity = payload['identity']
    # Missing is terminal only after consulting the actual profile authority.
    if await asyncio.to_thread(authority.db.get_session, sid) is None:
        return {'status': 'missing', 'session_id': sid}
    live = authority.sessions.get(sid)
    if live is None:
        restore_local_session(authority, sid)
        live = authority.sessions[sid]
    row = await asyncio.to_thread(find_session_admission, authority.db,
        principal_id='automation:' + live.route, session_id=sid, request_id=identity)
    if row is None:
        entry = route_entry(authority, live.route)
        if (entry is None or authority.logical_owner(sid) != sid or
                (entry.session_id != sid and authority.db.get_compression_tip(sid) != entry.session_id)):
            raise Unavailable('session route changed')
        event = MessageEvent(text=payload['text'], message_type=MessageType.TEXT,
            source=live.source, internal=True, allow_gateway_control=False,
            message_id=identity,
            metadata={'gateway_session_key': live.route, 'gateway_session_id': entry.session_id})
        async with runner._plugin_injection_scope(live.source, home=Path(authority.db.db_path).parent):
            await authority.admit_automation(runner._adapter_for_source(live.source), event, identity)
        row = await asyncio.to_thread(find_session_admission, authority.db,
            principal_id='automation:' + live.route, session_id=sid, request_id=identity)
    if row is None:
        raise Unavailable('admission not committed')
    if row['payload'].get('text') != payload['text'] or row['request_id'] != identity:
        raise Unavailable('admission payload mismatch')
    return {'status': 'admitted', 'admission_id': row['admission_id'], 'session_id': sid}


def deliver(job, status, owner, home, clean_output):
    if owner.get('kind') != 'session' or not owner.get('session_id'):
        return
    receipt = job / 'completion-receipt.json'
    if receipt.exists():
        return
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
        # Timeout does not cancel an admitted turn. A retry consults the same key.
        result = asyncio.run_coroutine_threadsafe(admit(runner, authority, payload), loop).result(timeout=20)
        save(receipt, result)
