"""Real subprocess CLI, stdio RPC, and Desktop's WebSocket handler acceptance.

Run with core on PYTHONPATH; optional transport deps: uvicorn and websockets.
The WebSocket host mounts production handle_ws directly, not serve's auth/startup
stack. No Electron renderer, live provider, remediation, cards or messages run.
"""
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHILD = Path(__file__).with_name('transport_child.py')
PROMPTS = {
    'abandoned': 'Repair example app. Why have we not fixed it yet?',
    'analysis': 'Explain HTTP 503. Analysis only; do not change anything.',
    'stop': 'Do not restart the service',
}
TEXTS = {
    'abandoned': 'The example app remains HTTP 503 because the prerequisite was deferred.',
    'analysis': 'Analysis only: HTTP 503 means the service is unavailable.',
    'stop': 'I will not restart the service without explicit user direction.',
}


def isolated_env(home):
    (home / 'plugins').mkdir(parents=True)
    (home / 'plugins' / 'coach').symlink_to(ROOT, target_is_directory=True)
    (home / 'coach').mkdir()
    (home / 'coach' / 'goal-disposition.json').write_text('{"mode":"enforce"}')
    (home / 'config.yaml').write_text(
        'plugins:\n  enabled: [coach]\nagent:\n  max_turns: 6\n'
        'memory:\n  memory_enabled: false\n  user_profile_enabled: false\n'
        'compression:\n  enabled: false\ncheckpoints:\n  enabled: false\n'
        'terminal:\n  cwd: ' + str(home) + '\n'
        'curator:\n  enabled: false\n')
    # The canonical runner strips PYTHONPATH; children still need this tested core.
    import hermes_constants
    # Allowlist instead of inheriting hub credentials, card routes or proxy URLs.
    env = {k: os.environ[k] for k in ('PATH', 'PYTHONPATH', 'LANG', 'TZ') if k in os.environ}
    env.update(PYTHONPATH=str(Path(hermes_constants.__file__).resolve().parent),
               HOME=str(home), HERMES_HOME=str(home), HERMES_RUNTIME_DIR=str(home / 'runtime'),
               OPENAI_API_KEY='local-test', COACH_LESSONS='0', COACH_REPLY_RULES='none',
               HERMES_VERIFY_ON_STOP='0', PYTHONUNBUFFERED='1', HERMES_TEST_ISOLATION='1')
    return env


class Frames:
    def __init__(self, process):
        self.process = process
        self.queue = queue.Queue()
        self.frames = []
        self.ws = None
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            try:
                self.queue.put(json.loads(line))
            except ValueError:
                self.queue.put({'non_json_stdout': line})
        self.queue.put({'eof': True})

    def receive(self):
        frame = json.loads(self.ws.recv(timeout=90)) if self.ws else self.queue.get(timeout=90)
        self.frames.append(frame)
        assert not frame.get('eof'), self.frames
        return frame

    def until(self, predicate):
        for frame in self.frames:
            if predicate(frame):
                return frame
        for _ in range(500):
            frame = self.receive()
            if predicate(frame):
                return frame
        raise AssertionError(self.frames)

    def rpc(self, method, params):
        rid = len(self.frames) + 1
        message = json.dumps(dict(jsonrpc='2.0', id=rid, method=method, params=params))
        if self.ws:
            self.ws.send(message)
        else:
            self.process.stdin.write(message + '\n')
            self.process.stdin.flush()
        result = self.until(lambda f: f.get('id') == rid)
        assert 'error' not in result, result
        return result['result']


def gateway_turn(frames, route, home):
    if route == 'ws':
        from websockets.sync.client import connect
        port = frames.receive()['port']
        frames.ws_context = connect(f'ws://127.0.0.1:{port}/api/ws', open_timeout=30)
        frames.ws = frames.ws_context.__enter__()
    frames.until(lambda f: f.get('params', {}).get('type') == 'gateway.ready')
    created = frames.rpc('session.create', {'source': 'desktop' if route == 'ws' else 'tui',
                                          'cwd': str(home), 'title': 'Transport acceptance'})
    sid = created['session_id']
    return sid


@pytest.mark.parametrize('route', ['cli', 'stdio', 'ws'])
@pytest.mark.parametrize('case', ['abandoned', 'analysis', 'stop'])
def test_guard_over_transport(tmp_path, route, case):
    home = tmp_path / 'home'
    env = isolated_env(home)
    stderr = home / 'stderr.log'
    with stderr.open('w') as errors:
        process = subprocess.Popen([sys.executable, str(CHILD), route, case, PROMPTS[case]],
                                   cwd=home, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=errors, text=True)
        frames = Frames(process)
        try:
            if route == 'cli':
                frames.until(lambda f: f.get('type') == 'result')
                assert process.wait(timeout=30) == (1 if case == 'abandoned' else 0), stderr.read_text()
            else:
                sid = gateway_turn(frames, route, home)
                frames.rpc('prompt.submit', {'session_id': sid, 'text': PROMPTS[case]})
                frames.until(lambda f: f.get('params', {}).get('type') == 'message.complete')
                frames.rpc('session.close', {'session_id': sid})
        finally:
            if frames.ws:
                frames.ws_context.__exit__(None, None, None)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
            (home / 'wire.json').write_text(json.dumps(frames.frames, indent=2))
    wire = json.dumps(frames.frames)
    channels = {}
    for frame in frames.frames:
        event = frame.get('params', frame)
        payload = event.get('payload', event)
        channels.setdefault(event.get('type', ''), []).append(str(payload.get('text', '')))
    visible_text = '\n'.join(''.join(parts) for parts in channels.values())
    with sqlite3.connect(home / 'state.db') as db:
        persisted = '\n'.join(row[0] or '' for row in db.execute(
            "SELECT content FROM messages WHERE role='assistant'"))
    calls = [json.loads(line) for line in (home / 'provider.jsonl').read_text().splitlines()]
    primary = [c for c in calls if c['kind'] == 'primary']
    judge = [c for c in calls if c['kind'] == 'auxiliary'
             and c['value']['task'] == 'coach_goal_disposition']
    assert primary and judge, (calls, stderr.read_text())
    assert not any(f.get('params', {}).get('type') == 'tool.start' for f in frames.frames)
    if case == 'abandoned':
        assert len(primary) == 3, calls
        assert len(judge) == 3, calls
        assert TEXTS[case] not in persisted
        assert 'Unresolved goal' in wire
        assert 'Unresolved goal' in persisted
        # Check every event, not just message.delta: alternate display channels
        # must not leak a held candidate either. Keep both failures in one receipt.
        complete = [f['params']['payload'] for f in frames.frames
                    if f.get('params', {}).get('type') == 'message.complete']
        observed = {
            'candidate_exposed_on_wire': TEXTS[case] in wire or TEXTS[case] in visible_text,
            'successful_closeout': any(p.get('status') == 'complete' for p in complete),
        }
        assert observed == {'candidate_exposed_on_wire': False, 'successful_closeout': False}
    else:
        assert len(primary) == 1, calls
        assert len(judge) == (2 if case == 'stop' else 1), calls
        if case == 'stop':
            assert 'operation' in json.loads(judge[-1]['value']['messages'][-1]['content'])
        assert TEXTS[case] in wire
        assert TEXTS[case] in persisted
        assert 'Unresolved goal' not in wire
