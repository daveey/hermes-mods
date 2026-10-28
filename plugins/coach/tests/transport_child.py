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


def network_guard(event, args):
    if event == 'socket.getaddrinfo' and args[0] not in ('127.0.0.1', '::1', None):
        record('blocked_dns', str(args[0]))
        raise RuntimeError('transport acceptance forbids external DNS')
    if event == 'socket.connect':
        address = args[1]
        if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', '::1'):
            record('blocked_network', str(address))
            raise RuntimeError('transport acceptance forbids non-loopback connections')


sys.addaudithook(network_guard)


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
    record('auxiliary', {'task': task, 'messages': kwargs.get('messages')})
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


def main():
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    import hermes_yaml as yaml
    config = yaml.safe_load((HOME / 'config.yaml').read_text())
    config['model'] = {'default': 'transport-test', 'provider': 'custom',
                       'base_url': f'http://127.0.0.1:{provider.server_port}/v1'}
    (HOME / 'config.yaml').write_text(yaml.safe_dump(config))
    import agent.auxiliary_client
    agent.auxiliary_client.call_llm = auxiliary
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
