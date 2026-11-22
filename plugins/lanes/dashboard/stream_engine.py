"""Socket control stays independent of slow projection or bounded result writes."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time


async def run(api, ws, params, debug):
    supervisor = api._projector() if api._projector_enabled else None
    if supervisor:
        supervisor.acquire()
    outgoing = asyncio.Queue(maxsize=1)
    wake = asyncio.Event()
    tasks = []
    version = 0
    last_hash = None
    cursor = None
    generation = 0
    pending = None
    current_task = None
    last_start = 0.
    observed_child = None

    async def receive():
        nonlocal params, pending
        while True:
            message = await ws.receive()
            if message['type'] == 'websocket.disconnect':
                return
            if message.get('text'):
                try:
                    update = json.loads(message['text'])
                    new = (update.get('hours', params[0]), update.get('window', params[1]), update.get('expanded', params[2]))
                    api._validate_view(*new)
                except Exception:
                    await ws.close(code=1008)
                    return
                params = new
                pending = pending or {}
                wake.set()

    async def sender():
        while True:
            frame = await outgoing.get()
            if debug and 'timing' in frame:
                frame['timing']['sent'] = time.time()
            await asyncio.wait_for(ws.send_json(frame), 3)

    def enqueue(frame):
        if outgoing.full():
            outgoing.get_nowait()
        outgoing.put_nowait(frame)

    async def project(key, rev, gen, timing):
        if supervisor:
            future = supervisor.submit(key, gen, rev)
            # The daemon supervisor retains ownership through cancellation.
            result = await asyncio.shield(asyncio.wrap_future(future))
        else:
            # Compatibility path for old cores. Same single fallback slot and deadline.
            def compute():
                if not api._identity.matches(api._loaded_identity):
                    raise RuntimeError('loaded projection identity unavailable')
                started = time.time()
                view = api.lane_view(*key)
                if not api._identity.matches(api._loaded_identity):
                    raise RuntimeError('fallback identity moved')
                return {'view': view, 'mode': 'fallback', 'projector_pid': None,
                        'revision': [gen, api._rpc_client.rpc.digest(rev)],
                        'timing': {'project_start': started, 'project_end': time.time()}}
            future = api._compat_fallback.submit((key, api._rpc_client.rpc.digest(rev)), compute)
            result = await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), 3)
        revision = result['revision']
        if revision != [gen, api._rpc_client.rpc.digest(rev)]:
            # A shared queued future may follow a newer revision. Its view has
            # that provenance, not this waiter's superseded detection timestamps.
            timing = {}
        return key, revision, timing, result

    def source_cursor():
        nonlocal observed_child
        source = api.change_cursor()
        child = supervisor.cursor() if supervisor else None
        if child is not None:
            observed_child = child
        # A refused/timed-out read is absence of evidence, not a dirty revision.
        return [source, observed_child] if supervisor else source

    async def poll():
        nonlocal cursor, generation, pending
        polled = time.time()
        new = await asyncio.to_thread(source_cursor)
        if new != cursor:
            cursor = new
            generation += 1
            api._refresh_cursor(cursor)
            if pending is None:
                pending = {'cursor_poll': polled, 'detected': time.time()}

    try:
        tasks = [asyncio.create_task(receive(), name='lanes-receive'),
                 asyncio.create_task(sender(), name='lanes-send')]
        await poll()
        next_poll = time.monotonic() + api.WATCH_S
        for task in tasks:
            task.add_done_callback(lambda _: wake.set())
        pending = {}  # initial frame has no per-change detection
        last_heartbeat = time.monotonic()
        while True:
            if any(t.done() for t in tasks):
                for t in tasks:
                    if t.done():
                        t.result()
                return
            if current_task is not None and current_task.done():
                key, revision, timing, result = current_task.result()
                current_task = None

                # Parameter/session supersession never leaks an old-key frame.
                # A source change during compute is allowed as a revision-bound
                # view, with its pending follow-up retained, never labelled current.
                if key == params:
                    echo = dict(zip(('hours', 'window', 'expanded'), key))
                    digest = hashlib.sha256(json.dumps({'view': result['view'], 'params': echo}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                    if digest != last_hash:
                        version += 1
                        frame = {'version': version, 'view': result['view'], 'params': echo}
                        if debug:
                            frame['timing'] = {**timing, **result['timing']}
                            frame['load'] = {'pid': api.os.getpid(), 'stream_viewers': api._active_streams}
                            frame['projector'] = {'mode': result['mode'], 'projector_pid': result['projector_pid'],
                                                  'revision': revision,
                                                  'source_current': revision == [api._generation, api._rpc_client.rpc.digest(cursor)]}
                        enqueue(frame)
                        last_hash = digest
            if pending is not None and current_task is None and time.monotonic() - last_start >= api.MIN_PROJECT_GAP_S:
                timing, pending = pending, None
                if timing:
                    timing = {**timing, 'released': time.time()}
                last_start = time.monotonic()
                current_task = asyncio.create_task(project(params, cursor, api._generation, timing), name='lanes-project')
                current_task.add_done_callback(lambda _: wake.set())
            if time.monotonic() - last_heartbeat >= api.HEARTBEAT_S:
                # Don't replace an unsent view with a heartbeat.
                if outgoing.empty():
                    enqueue({'type': 'heartbeat', 'version': version})
                last_heartbeat = time.monotonic()
            wake.clear()
            now = time.monotonic()
            deadline = min(next_poll, last_heartbeat + api.HEARTBEAT_S)
            if pending is not None and current_task is None:
                deadline = min(deadline, last_start + api.MIN_PROJECT_GAP_S)
            try:
                await asyncio.wait_for(wake.wait(), max(0., deadline - now))
            except TimeoutError:
                pass
            # A completed result is ready immediately. Cursor sampling retains
            # its independent cadence rather than blocking every result wake.
            if time.monotonic() >= next_poll:
                await poll()
                next_poll = time.monotonic() + api.WATCH_S
    finally:
        if current_task is not None:
            tasks.append(current_task)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if supervisor:
            supervisor.release()
