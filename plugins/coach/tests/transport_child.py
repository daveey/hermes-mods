"""Isolated transport process; only model/auxiliary responses are deterministic.

The CLI parser, RPC dispatcher, plugin discovery, full agent turn, stream gates
and SessionDB are production code. No tool call is returned by this provider.
"""
import json
import os
from pathlib import Path
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

HOME = Path(os.environ['HERMES_HOME'])
CASE = sys.argv[2]
TEXT = {
    'abandoned': 'The example app remains HTTP 503 because the prerequisite was deferred.',
    'analysis': 'Analysis only: HTTP 503 means the service is unavailable.',
    'stop': 'I will not restart the service without explicit user direction.',
}[CASE]


def record(kind, value):
    with (HOME / 'provider.jsonl').open('a') as f:
        f.write(json.dumps({'kind': kind, 'value': value}) + '\n')


def _home_control_socket():
    """The isolated home's own gateway control socket leaf: ``hermes-gw-<hash>/control.sock``.
    Derived from HOME (the core naming rule), never read from the racy ``gateway.sock.path``."""
    import hashlib
    digest = hashlib.sha256(os.path.normcase(str(HOME.expanduser().resolve(strict=False))).encode()).hexdigest()
    return f'hermes-gw-{digest[:16]}', 'control.sock'


CONTROL_SOCKET = _home_control_socket()


def _own_unix_socket(address):
    """AF_UNIX connects are allowed only to this home's gateway: a socket under HOME, or the
    short-path control socket core names for HOME (``connect_private``)."""
    if not isinstance(address, (str, bytes)) or not address:
        return False
    path = Path(os.fsdecode(address))
    if (path.parent.name, path.name) == CONTROL_SOCKET:
        return True
    try:
        return path.resolve().is_relative_to(HOME.resolve())
    except (OSError, ValueError):
        return False


def network_guard(event, args):
    if event == 'socket.getaddrinfo' and args[0] not in ('127.0.0.1', '::1', None):
        record('blocked_dns', str(args[0]))
        raise RuntimeError('transport acceptance forbids external DNS')
    if event == 'socket.connect':
        address = args[1]
        if _own_unix_socket(address):
            return
        if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', '::1'):
            record('blocked_network', str(address))
            raise RuntimeError('transport acceptance forbids non-loopback connections')


sys.addaudithook(network_guard)
ROLE = 'gateway' if sys.argv[1] == 'gateway' else 'client'
# Every process that can run a turn proves the guard and the judge stub were armed in it.
record('guard_armed', {'pid': os.getpid(), 'role': ROLE})


def route_gateway_spawn_through_fixture():
    """Clients now run turns in an auto-started gateway daemon (``ensure_gateway_runtime`` ->
    ``spawn_unmanaged_gateway``), not in-process. Launch that daemon through this file's
    ``gateway`` mode so the network guard and the deterministic judge run where the turn runs;
    the daemon itself is the production ``hermes gateway run``."""
    import subprocess
    from hermes_cli import gateway_runtime_start as start

    class Popen(subprocess.Popen):
        def __init__(self, command, *args, **kwargs):
            if list(command[1:3]) == ['-m', 'hermes_cli.main'] and 'gateway' in command:
                command = [command[0], str(Path(__file__).resolve()), 'gateway', CASE, *command[3:]]
            super().__init__(command, *args, **kwargs)

    start.subprocess = SimpleNamespace(**{**vars(subprocess), 'Popen': Popen})
    # macOS launches through a job helper first; take the Popen path so the rewrite applies.
    start.start_gateway_job = lambda *args, **kwargs: None


def stub_judge():
    import agent.auxiliary_client
    agent.auxiliary_client.call_llm = auxiliary


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        return

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        record('primary' if self.path.endswith('/chat/completions') else 'metadata_probe', body)
        chunk = {'id': 'local-test', 'object': 'chat.completion', 'model': 'transport-test',
                 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': TEXT},
                              'finish_reason': 'stop'}],
                 'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}}
        self.send_response(200)
        if body.get('stream'):
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for text in (TEXT[:20], TEXT[20:]):
                frame = dict(chunk, object='chat.completion.chunk', choices=[{
                    'index': 0, 'delta': {'content': text}, 'finish_reason': None}])
                self.wfile.write(('data: ' + json.dumps(frame) + '\n\n').encode())
                self.wfile.flush()
            frame = dict(chunk, object='chat.completion.chunk', choices=[{
                'index': 0, 'delta': {}, 'finish_reason': 'stop'}])
            self.wfile.write(('data: ' + json.dumps(frame) + '\n\ndata: [DONE]\n\n').encode())
        else:
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(chunk).encode())


def auxiliary(*args, **kwargs):
    task = kwargs.get('task')
    record('auxiliary', {'task': task, 'messages': kwargs.get('messages'), 'pid': os.getpid()})
    verdict = dict(goal='Repair example app', actionable=True, unfinished=True,
                   disposition='abandoned', evidence_refs=['user:0', 'candidate'],
                   boundary_kind=None, continuation_ref=None, confidence=.99)
    if CASE == 'analysis':
        verdict.update(actionable=False, unfinished=False, disposition='analysis_only')
    elif CASE == 'stop':
        verdict.update(disposition='authorization_boundary', boundary_kind='denial',
                       proof={'action': 'restart the service', 'source_ref': 'user:0',
                              'approval_path': 'user_explicit_direction'})
    if task == 'coach_goal_disposition' and 'operation' in json.loads(kwargs['messages'][-1]['content']):
        verdict = dict(relation='required', evidence_refs=['user:0'],
                       rationale='The latest explicit scope prohibits the exact operation.', confidence=.99)
    content = json.dumps(verdict) if task == 'coach_goal_disposition' else 'Local test title'
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def run_gateway():
    """``gateway`` mode: the production daemon, with the guard (armed at import) and judge stub."""
    # Before any core import: hermes_cli.main applies --profile from sys.argv when it loads.
    sys.argv = ['hermes', *sys.argv[3:]]
    stub_judge()
    from hermes_cli.main import main as cli_main
    cli_main()


def main():
    if sys.argv[1] == 'gateway':
        run_gateway()
        return
    route_gateway_spawn_through_fixture()
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    import hermes_yaml as yaml
    config = yaml.safe_load((HOME / 'config.yaml').read_text())
    config['model'] = {'default': 'transport-test', 'provider': 'custom',
                       'base_url': f'http://127.0.0.1:{provider.server_port}/v1'}
    (HOME / 'config.yaml').write_text(yaml.safe_dump(config))
    stub_judge()
    route = sys.argv[1]
    if route == 'cli':
        from hermes_cli.main import main as cli_main
        sys.argv = ['hermes', 'chat', '-q', sys.argv[3], '--provider', 'custom',
                    '--model', 'transport-test', '--format', 'stream-json']
        cli_main()
    elif route == 'stdio':
        from tui_gateway.entry import main as gateway_main
        gateway_main()
    else:
        from fastapi import FastAPI, WebSocket
        from tui_gateway.ws import handle_ws
        import uvicorn
        app = FastAPI()

        @app.websocket('/api/ws')
        async def websocket(ws: WebSocket):
            await handle_ws(ws)

        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        os.write(1, (json.dumps({'port': listener.getsockname()[1]}) + '\n').encode())
        uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off')).run(sockets=[listener])


if __name__ == '__main__':
    main()
