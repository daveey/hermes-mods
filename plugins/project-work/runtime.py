"""Supervised host runtime. Run: python runtime.py --home PROFILE --adapter FILE.

FILE is a private, owner-only deployment binding exporting build_ports(policy,
NativeBridge). It binds real native APIs; never put secrets or workspace policy
in this public plugin. The socket is host-only and must not enter any sandbox.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import socketserver
import stat
import sys
import threading
import types

if not __package__:
    package = types.ModuleType('_project_work_runtime')
    package.__path__ = [str(Path(__file__).resolve().parent)]
    sys.modules[package.__name__] = package
    __package__ = package.__name__

from .client import Client, MAX_BYTES
from .config import load_config
from .native import NativeBridge
from .service import AuthContext, Service
from .store import Store
from .errors import MissingWorkflow, RejectedRequest, RejectedScope


def public_status(row):
    keys = ('workflow_id', 'generation', 'state', 'session_id', 'stored_session_id',
            'title', 'approved', 'plan', 'plan_hash', 'error', 'result_receipt')
    result = {k: row.get(k) for k in keys}
    result['cards'] = {k: v.get('id') for k, v in row.get('cards', {}).items()}
    return result


class Controller:
    def __init__(self, service):
        self.service = service

    def call(self, operation, args):
        expected = {'request': {'context', 'text'}, 'status': {'context', 'workflow_id'},
            'approve': {'context', 'workflow_id', 'plan_hash'}, 'observe_followup': {'context', 'text'},
            'propose_plan': {'workflow_id', 'plan_text', 'generation'}, 'closeout': {'workflow_id', 'result_text'},
            'publication_gate': {'workflow_id', 'card_id'}, 'reconcile': set()}
        if operation not in expected or not isinstance(args, dict) or set(args) != expected[operation]:
            raise ValueError('Unknown operation or argument')
        args = dict(args)
        if 'context' in args:
            args['ctx'] = AuthContext(**args.pop('context'))
        if operation == 'observe_followup':
            # No unknown thread can become a workflow merely by being observed.
            try:
                return public_status(self.service.followup(**args))
            except MissingWorkflow:
                return {'ignored': True}
            except (RejectedRequest, RejectedScope) as exc:
                return {'rejected': True, 'reason': type(exc).__name__}
        try:
            result = getattr(self.service, operation)(**args)
        except (MissingWorkflow, RejectedScope, RejectedRequest) as exc:
            if operation in ('approve', 'request'):
                # A rejected command is terminal, not an outage to retry forever.
                # The Slack backfill records the exact attempt for watermarking.
                return {'rejected': True, 'reason': type(exc).__name__}
            raise
        if operation == 'publication_gate':
            return result
        return [public_status(row) for row in result] if operation == 'reconcile' else public_status(result)


class Handler(socketserver.StreamRequestHandler):
    server: 'Server'
    def handle(self):
        try:
            raw = self.rfile.readline(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES or not raw.endswith(b'\n'):
                raise ValueError('Invalid request frame')
            envelope = json.loads(raw)
            if set(envelope) != {'operation', 'arguments'}:
                raise ValueError('Invalid request envelope')
            result = self.server.controller.call(envelope['operation'], envelope['arguments'])
            response = {'ok': True, 'result': result}
        except Exception as exc:
            # No vendor exception payloads or secrets cross the ingress boundary.
            response = {'ok': False, 'error': type(exc).__name__}
        self.wfile.write(json.dumps(response).encode() + b'\n')


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    def __init__(self, path, controller):
        self.controller = controller
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.parent.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError('Runtime directory must be owner-only')
        if path.exists():
            # Never replace an active socket or any non-socket file.
            if not stat.S_ISSOCK(path.lstat().st_mode) or path.lstat().st_uid != os.getuid():
                raise PermissionError('Unsafe existing socket path')
            with socket.socket(socket.AF_UNIX) as probe:
                try:
                    probe.connect(str(path))
                except ConnectionRefusedError:
                    path.unlink()
                else:
                    raise RuntimeError('Runtime already active')
        old = os.umask(0o077)
        try:
            super().__init__(str(path), Handler)
            path.chmod(0o600)
        finally:
            os.umask(old)


def load_adapter(path, policy):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('Adapter must be an absolute real file')
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise PermissionError('Adapter must be owned and not group/world writable')
    spec = importlib.util.spec_from_file_location('_project_work_private_binding', path)
    if spec is None or spec.loader is None:
        raise ValueError('Cannot load native adapter')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ports = module.build_ports(policy, NativeBridge)
    required = ('create_session', 'recover_session', 'prompt', 'recover_prompt', 'create_card',
        'get_session', 'archive', 'get_card', 'verify_evidence', 'post_result', 'recover_result', 'read_result')
    if not all(callable(getattr(ports, name, None)) for name in required):
        raise TypeError('Incomplete native binding')
    return ports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', required=True)
    parser.add_argument('--adapter')
    parser.add_argument('--reconcile', action='store_true')
    parser.add_argument('--interval', type=float, default=30)
    args = parser.parse_args()
    policy, endpoint, ledger = load_config(args.home)
    if args.reconcile:
        print(json.dumps(Client(endpoint).call('reconcile')))
        return
    if not args.adapter or args.interval < 1:
        parser.error('--adapter and interval >= 1 required for serving')
    service = Service(policy, Store(ledger), load_adapter(args.adapter, policy))
    stop = threading.Event()
    def reconcile():
        while not stop.is_set():
            try:
                service.reconcile()
            except Exception as exc:
                print('Reconciliation failed: ' + type(exc).__name__, file=sys.stderr, flush=True)
            stop.wait(args.interval)
    with Server(endpoint, Controller(service)) as server:
        worker = threading.Thread(target=reconcile, daemon=True)
        worker.start()
        try:
            server.serve_forever()
        finally:
            stop.set()
            endpoint.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
