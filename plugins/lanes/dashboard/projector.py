"""Projection-only worker. Parent owns detection, authorization and scheduling."""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
import queue
import sqlite3
import sys
import threading
import time


def sibling(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location('lanes_worker_' + name, Path(__file__).with_name(name + '.py'))
    assert spec is not None and spec.loader is not None
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


@contextlib.contextmanager
def readers(local, fleet, mode):
    """C1: open through generation recovery EVERY job; never retain dead files."""
    from hermes_cli import kanban_lockguard as guard, kanban_db_sync as sync, kanban_db as kb
    with contextlib.ExitStack() as scope:
        handles = []
        for path, local_source in ((local, True), (fleet, False)):
            if path is None:
                handles.append(None)
                continue
            uri = Path(path).resolve().as_uri() + '?mode=ro'
            db = guard.connect(uri, uri=True, timeout=.25)
            scope.callback(db.close)
            db.row_factory = sqlite3.Row
            if local_source:
                db.text_factory = kb._lossy_text
            db.execute('PRAGMA query_only=ON')
            # Both local and fleet handles participate in presentation. Do not
            # leave the replica on a separate two-second wall deadline while
            # the local handle receives the board's CPU/starvation budget.
            sibling('board_merge').arm_deadline(db, phase='projector-open')
            if local_source:
                sync.register_without_install(db, sync._db_key(db))
                if sync.board_mode(db) != mode:
                    raise RuntimeError('lanes source mode mismatch')
            db.execute('BEGIN')
            scope.callback(db.rollback)
            handles.append(db)
        yield tuple(handles)


def main():
    protocol_out = sys.stdout.buffer
    sys.stdout = sys.stderr  # imported helpers may print; stdout is protocol only
    rpc = sibling('projector_protocol')
    identity = sibling('projector_identity')
    config = rpc.decode(sys.stdin.buffer.readline(rpc.REPLY_CAP + 1), rpc.REPLY_CAP)
    manifest, session = config['identity'], config['session']
    if str(Path(__file__).resolve()) not in manifest or not identity.matches(manifest):
        return
    api = identity.load(Path(__file__).with_name('plugin_api.py'), 'lanes_rpc_api')
    if not identity.matches(manifest):
        return
    output_lock = threading.Lock()
    def send(body):
        raw = rpc.encode(body, rpc.REPLY_CAP)
        with output_lock:
            protocol_out.write(raw)
            protocol_out.flush()
    def envelope(cmd, op):
        return {k: cmd[k] for k in ('v', 'session', 'id')} | {'op': op}
    jobs = queue.Queue(maxsize=1)
    busy = threading.Event()
    def compute():
        while True:
            cmd = jobs.get()
            try:
                if not identity.matches(manifest):
                    raise RuntimeError('code identity mismatch')
                started = time.time()
                with readers(config['local'], config.get('fleet'), cmd['mode']) as (local, fleet):
                    from hermes_cli.kanban_lanes_sources import presentation
                    views = api._project_evidence(api.core(), local, presentation(local, fleet), *cmd['params'], variants=True)
                    view = views['expanded' if cmd['params'][2] else 'collapsed']
                finished = time.time()  # snapshots released before publish
                if not identity.matches(manifest):
                    raise RuntimeError('code identity moved')
                send(envelope(cmd, 'result') | {'view': view, 'params': cmd['params'],
                     'revision': cmd['revision'], 'views': views, 'projector_pid': os.getpid(),
                     'timing': {'project_start': started, 'project_end': finished, 'reply_start': time.time()}})
            except Exception:
                send(envelope(cmd, 'error'))  # no paths, SQL, tokens or exception text
            finally:
                busy.clear()
    threading.Thread(target=compute, name='lanes-projector-compute', daemon=True).start()
    send({'v': rpc.VERSION, 'session': session, 'op': 'ready', 'identity': manifest, 'pid': os.getpid()})
    # This control thread never touches SQLite and stays responsive during Python/SQL work.
    # EOF exits even if projection is hung. Closing parent pipes also prevents orphans.
    while True:
        raw = sys.stdin.buffer.readline(rpc.COMMAND_CAP + 1)
        if not raw:
            os._exit(0)
        try:
            cmd = rpc.decode(raw, rpc.COMMAND_CAP)
            rpc.validate_command(cmd, session)
            if cmd['op'] == 'stop':
                os._exit(0)
            if cmd['op'] == 'observation_cursor':
                send(envelope(cmd, 'cursor') | {'cursor': rpc.digest(api._observation_cursor())})
            elif busy.is_set():
                send(envelope(cmd, 'error'))
            else:
                busy.set()
                jobs.put_nowait(cmd)
        except Exception:
            os._exit(1)


if __name__ == '__main__':
    main()
