"""Production serve startup/token auth and same-session next-turn acceptance.

The child installs only a network audit fence, then executes the supported CLI
module with serve arguments. No server, auth, plugin, or auxiliary code is
patched. Deterministic primary and judge replies travel over loopback HTTP.
This is the private loopback session-token mode, not public OAuth or Electron.
"""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from test_goal_disposition_transport import Frames, PROMPTS, TEXTS, isolated_env


def _child():
    import runpy
    def fence(event, args):
        if event == 'socket.getaddrinfo' and args[0] not in ('127.0.0.1', '::1', None):
            raise RuntimeError('acceptance forbids external DNS')
        if event == 'socket.connect':
            address = args[1]
            if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', '::1'):
                raise RuntimeError('acceptance forbids non-loopback connections')
    sys.addaudithook(fence)
    sys.argv = ['hermes', 'serve', '--isolated', '--host', '127.0.0.1', '--port', '0']
    runpy.run_module('hermes_cli.main', run_name='__main__')


class Model(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.calls.append(body)
        primary_count = sum(c.get('model') == 'acceptance-primary' and bool(c.get('stream'))
                            for c in self.server.calls)
        if body.get('model') == 'acceptance-primary' and body.get('stream') and primary_count == 1:
            assert self.server.release.wait(timeout=30)
        analysis = primary_count > 3
        if body.get('model') == 'acceptance-judge':
            content = json.dumps(dict(goal='Repair example app', actionable=not analysis,
                unfinished=not analysis, disposition='analysis_only' if analysis else 'abandoned',
                evidence_refs=['user:0', 'candidate'], boundary_kind=None,
                continuation_ref=None, confidence=.99))
        elif body.get('model') == 'acceptance-primary' and body.get('stream'):
            content = TEXTS['analysis' if analysis else 'abandoned']
        else:
            content = 'Local acceptance title'
        chunk = dict(id='local-acceptance', object='chat.completion', model=body.get('model'),
            choices=[dict(index=0, message=dict(role='assistant', content=content), finish_reason='stop')],
            usage=dict(prompt_tokens=10, completion_tokens=10, total_tokens=20))
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream' if body.get('stream') else 'application/json')
        self.end_headers()
        if body.get('stream'):
            for part in (content[:20], content[20:]):
                frame = dict(chunk, object='chat.completion.chunk', choices=[
                    dict(index=0, delta=dict(content=part), finish_reason=None)])
                self.wfile.write(('data: ' + json.dumps(frame) + '\n\n').encode())
                self.wfile.flush()
            frame = dict(chunk, object='chat.completion.chunk', choices=[dict(index=0, delta={}, finish_reason='stop')])
            self.wfile.write(('data: ' + json.dumps(frame) + '\n\ndata: [DONE]\n\n').encode())
        else:
            self.wfile.write(json.dumps(chunk).encode())


def _ready(path, process, errors):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        assert process.poll() is None, errors.read_text()
        if path.exists():
            return json.loads(path.read_text())['port']
        threading.Event().wait(.05)
    raise AssertionError(errors.read_text())


def _turn(frames, sid, text=None, *, start=None):
    if start is None:
        start = len(frames.frames)
    if text is not None:
        frames.rpc('prompt.submit', dict(session_id=sid, text=text))
    for _ in range(500):
        completed = [f for f in frames.frames[start:] if f.get('params', {}).get('type') == 'message.complete']
        if completed:
            return frames.frames[start:], completed[-1]['params']['payload']
        frames.receive()
    raise AssertionError(frames.frames[start:])


def _idle(frames, sid):
    # message.complete precedes worker teardown; a next turn is not a queued turn.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        rows = frames.rpc('session.active_list', {'current_session_id': sid})['sessions']
        if any(row['id'] == sid and row['status'] == 'idle' for row in rows):
            return
        threading.Event().wait(.05)
    raise AssertionError(rows)


def _visible(frames):
    channels = {}
    for frame in frames:
        event = frame.get('params', {})
        channels.setdefault(event.get('type', ''), []).append(str(event.get('payload', {}).get('text', '')))
    return json.dumps(frames) + '\n'.join(''.join(parts) for parts in channels.values())


@pytest.mark.parametrize('queued', [False, True])
def test_production_serve_auth_and_next_turn(tmp_path, queued):
    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect
    import yaml
    home = tmp_path / 'home'
    env = isolated_env(home)
    token = 'synthetic-serve-acceptance-token'
    env.update(HERMES_DASHBOARD_SESSION_TOKEN=token, HERMES_DESKTOP_READY_FILE=str(home / 'ready.json'))
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    provider.calls = []
    provider.release = threading.Event()
    if not queued:
        provider.release.set()
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    endpoint = f'http://127.0.0.1:{provider.server_port}/v1'
    config = yaml.safe_load((home / 'config.yaml').read_text())
    config['model'] = dict(default='acceptance-primary', provider='custom', base_url=endpoint)
    config['auxiliary'] = {name: dict(provider='custom', model=model, base_url=endpoint, api_key='local-test')
        for name, model in [('coach_goal_disposition', 'acceptance-judge'), ('title', 'acceptance-title')]}
    (home / 'config.yaml').write_text(yaml.safe_dump(config))
    errors = home / 'serve-stderr.log'
    receipt = dict(denied=[])
    frames = None
    with errors.open('w') as err, (home / 'serve-stdout.log').open('w') as out:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--child'],
            env=env, cwd=home, stdout=out, stderr=err, text=True)
        try:
            port = _ready(home / 'ready.json', process, errors)
            url = f'ws://127.0.0.1:{port}/api/ws'
            for suffix in ('', '?token=incorrect-synthetic-token'):
                with pytest.raises(InvalidStatus) as rejected:
                    connect(url + suffix, open_timeout=15)
                assert rejected.value.response.status_code == 403
                receipt['denied'].append(rejected.value.response.status_code)
            assert not provider.calls
            with connect(url + '?token=' + token, open_timeout=30) as ws:
                frames = object.__new__(Frames)
                frames.frames = []
                frames.ws = ws
                frames.until(lambda f: f.get('params', {}).get('type') == 'gateway.ready')
                created = frames.rpc('session.create', dict(source='desktop', cwd=str(home), title='Serve acceptance'))
                sid = created['session_id']
                if queued:
                    start = len(frames.frames)
                    frames.rpc('prompt.submit', dict(session_id=sid, text=PROMPTS['abandoned']))
                    ack = frames.rpc('prompt.submit', dict(session_id=sid, text=PROMPTS['analysis']))
                    assert ack['status'] == 'queued', ack
                    provider.release.set()
                    first, complete = _turn(frames, sid, start=start)
                else:
                    first, complete = _turn(frames, sid, PROMPTS['abandoned'])
                assert TEXTS['abandoned'] not in _visible(first)
                assert 'Unresolved goal' in _visible(first)
                assert complete['status'] == 'error', complete
                receipt['first_status'] = complete['status']
                # A queued turn may begin before the client consumes completion.
                assert 3 <= sum(c.get('model') == 'acceptance-primary' and bool(c.get('stream')) for c in provider.calls) <= (4 if queued else 3)
                assert 3 <= sum(c.get('model') == 'acceptance-judge' for c in provider.calls) <= (4 if queued else 3)
                if queued:
                    second, complete = _turn(frames, sid)
                else:
                    _idle(frames, sid)
                    second, complete = _turn(frames, sid, PROMPTS['analysis'])
                assert TEXTS['analysis'] in _visible(second)
                assert 'Unresolved goal' not in _visible(second)
                assert complete['status'] == 'complete', complete
                receipt['second_status'] = complete['status']
                assert sum(c.get('model') == 'acceptance-primary' and bool(c.get('stream')) for c in provider.calls) == 4
                assert sum(c.get('model') == 'acceptance-judge' for c in provider.calls) == 4
                assert not any(f.get('params', {}).get('type') == 'tool.start' for f in frames.frames)
                _idle(frames, sid)
                frames.rpc('session.close', dict(session_id=sid))
            with sqlite3.connect(home / 'state.db') as db:
                persisted = '\n'.join(r[0] or '' for r in db.execute("SELECT content FROM messages WHERE role='assistant'"))
            assert TEXTS['abandoned'] not in persisted
            assert 'Unresolved goal' in persisted
            assert TEXTS['analysis'] in persisted
            receipt['persisted'] = persisted
            assert TEXTS['abandoned'] not in (home / 'serve-stdout.log').read_text()
            assert TEXTS['abandoned'] not in errors.read_text()
        finally:
            provider.release.set()
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)
            provider.shutdown()
            provider.server_close()
            thread.join(timeout=5)
            assert process.poll() is not None
            assert not thread.is_alive()
            receipt['child_exit'] = process.returncode
            (home / 'wire.json').write_text(json.dumps(frames.frames if frames else [], indent=2))
            (home / 'provider.json').write_text(json.dumps(provider.calls, indent=2))
            (home / 'receipt.json').write_text(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    _child()
