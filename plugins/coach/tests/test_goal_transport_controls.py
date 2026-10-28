"""Real transport stop/reconnect controls; provider-only deterministic fixture.

Run through core/scripts/run_tests.sh with core on PYTHONPATH. All children
use disposable homes and transport_child's loopback-only network audit hook.
"""
import contextlib
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from test_goal_disposition_transport import Frames, PROMPTS, TEXTS, gateway_turn, isolated_env


def wait_for(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        threading.Event().wait(.05)
    raise AssertionError('timed out waiting for isolated transport fixture')


def child_main():
    import transport_child as fixture

    class HeldProvider(fixture.Provider):
        def do_POST(self):
            if self.path.endswith('/chat/completions'):
                (fixture.HOME / 'provider-entered').touch()
                wait_for(lambda: (fixture.HOME / 'provider-release').exists())
            super().do_POST()

    fixture.Provider = HeldProvider
    if sys.argv[1] != 'pty':
        fixture.main()
        return
    from http.server import ThreadingHTTPServer
    import hermes_yaml as yaml
    server = ThreadingHTTPServer(('127.0.0.1', 0), HeldProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = yaml.safe_load((fixture.HOME / 'config.yaml').read_text())
    config['model'] = dict(default='transport-test', provider='custom',
                           base_url=f'http://127.0.0.1:{server.server_port}/v1')
    (fixture.HOME / 'config.yaml').write_text(yaml.safe_dump(config))
    import agent.auxiliary_client
    agent.auxiliary_client.call_llm = fixture.auxiliary
    from hermes_cli.main import main
    sys.argv = ['hermes', 'chat', '--provider', 'custom', '--model', 'transport-test']
    main()


@contextlib.contextmanager
def gateway(home, route, env):
    with (home / f'{route}-stderr.log').open('a') as errors:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), route,
                                    'abandoned', PROMPTS['abandoned']], cwd=home, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=errors, text=True)
        frames = Frames(process)
        try:
            yield frames
        finally:
            if frames.ws:
                frames.ws.close()
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)
            with (home / 'wire.jsonl').open('a') as out:
                out.write(json.dumps(frames.frames) + '\n')


def assert_stopped(home, frames):
    complete = frames.until(lambda f: f.get('params', {}).get('type') == 'message.complete')
    assert complete['params']['payload']['status'] == 'interrupted', complete
    wire = json.dumps(frames.frames)
    assert TEXTS['abandoned'] not in wire
    assert 'Unresolved goal' not in wire
    assert not any(f.get('params', {}).get('type') == 'tool.start' for f in frames.frames)
    with sqlite3.connect(home / 'state.db') as db:
        persisted = '\n'.join(r[0] or '' for r in db.execute(
            "SELECT content FROM messages WHERE role='assistant'"))
    assert TEXTS['abandoned'] not in persisted
    assert 'Unresolved goal' not in persisted


@pytest.mark.parametrize('route', ['stdio', 'ws'])
def test_actual_interrupt_rpc(tmp_path, route):
    home = tmp_path / 'home'
    env = isolated_env(home)
    with gateway(home, route, env) as frames:
        sid = gateway_turn(frames, route, home)
        frames.rpc('prompt.submit', dict(session_id=sid, text=PROMPTS['abandoned']))
        wait_for(lambda: (home / 'provider-entered').exists())
        result = frames.rpc('session.interrupt', dict(session_id=sid))
        assert result['status'] == 'interrupted'
        (home / 'provider-release').touch()
        assert_stopped(home, frames)
        frames.rpc('session.close', dict(session_id=sid))


def test_ws_reconnect_guarded_completion(tmp_path):
    from websockets.sync.client import connect
    home = tmp_path / 'home'
    env = isolated_env(home)
    with gateway(home, 'ws', env) as frames:
        sid = gateway_turn(frames, 'ws', home)
        stored = next(f['result']['stored_session_id'] for f in frames.frames
                      if 'stored_session_id' in f.get('result', {}))
        url = frames.ws.remote_address
        frames.rpc('prompt.submit', dict(session_id=sid, text=PROMPTS['abandoned']))
        wait_for(lambda: (home / 'provider-entered').exists())
        frames.ws.close()
        frames.ws = connect(f'ws://{url[0]}:{url[1]}/api/ws', open_timeout=30).__enter__()
        # Do not let cached gateway.ready satisfy the reconnect barrier.
        frames.frames.clear()
        frames.until(lambda f: f.get('params', {}).get('type') == 'gateway.ready')
        resumed = frames.rpc('session.resume', dict(session_id=stored))
        sid = resumed['session_id']
        (home / 'provider-release').touch()
        frames.until(lambda f: f.get('params', {}).get('type') == 'message.complete')
        wire = json.dumps(frames.frames)
        assert TEXTS['abandoned'] not in wire, resumed
        assert 'Unresolved goal' in wire
        frames.rpc('session.close', dict(session_id=sid))
    with gateway(home, 'stdio', env) as frames:
        frames.until(lambda f: f.get('params', {}).get('type') == 'gateway.ready')
        snapshot = frames.rpc('session.resume', dict(session_id=stored))
        assert 'Unresolved goal' in json.dumps(snapshot)
        assert TEXTS['abandoned'] not in json.dumps(snapshot)
        frames.rpc('session.close', dict(session_id=snapshot['session_id']))


def test_cli_pty_ctrl_c(tmp_path):
    import pty
    home = tmp_path / 'home'
    env = isolated_env(home)
    env['TERM'] = 'xterm-256color'
    pid, master = pty.fork()
    if pid == 0:
        os.chdir(home)
        os.execve(sys.executable, [sys.executable, str(Path(__file__).resolve()),
                                  'pty', 'abandoned', PROMPTS['abandoned']], env)
    output = bytearray()
    done = threading.Event()

    def read():
        while not done.is_set():
            if select.select([master], [], [], .1)[0]:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    return
                if not data:
                    return
                output.extend(data)
                # prompt_toolkit requests the cursor position from real terminals.
                if b'\x1b[6n' in data:
                    os.write(master, b'\x1b[1;1R')

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        # First-run consent ("No thanks") is only offered by installs that have not answered it; the
        # fork default reads as already decided (shared_metrics_consent.consent_state), so wait for
        # whichever comes first and answer the offer only when it is shown.
        wait_for(lambda: b'No thanks' in output or b'\x1b[?2004h' in output)
        if b'\x1b[?2004h' not in output:
            os.write(master, b'\r')
        wait_for(lambda: b'\x1b[?2004h' in output)
        os.write(master, PROMPTS['abandoned'].encode())
        wait_for(lambda: b'fixed it yet?' in output)
        # Native paste/dictation debounce is 50ms; submit a distinct keypress.
        threading.Event().wait(.15)
        os.write(master, b'\r')
        wait_for(lambda: (home / 'provider-entered').exists())
        os.write(master, b'\x03')
        wait_for(lambda: b'Interrupted during API call' in output
                 or b'Operation interrupted:' in output)
        (home / 'provider-release').touch()
        wait_for(lambda: (home / 'provider.jsonl').exists())
        os.write(master, b'/quit')
        threading.Event().wait(.15)
        os.write(master, b'\r')
        wait_for(lambda: os.waitpid(pid, os.WNOHANG)[0] == pid)
        text = output.decode(errors='replace')
        assert TEXTS['abandoned'] not in text
        assert 'Unresolved goal' not in text
        with sqlite3.connect(home / 'state.db') as db:
            persisted = '\n'.join(r[0] or '' for r in db.execute(
                "SELECT content FROM messages WHERE role='assistant'"))
        assert TEXTS['abandoned'] not in persisted
        assert 'Unresolved goal' not in persisted
        calls = [json.loads(line) for line in (home / 'provider.jsonl').read_text().splitlines()]
        assert sum(c['kind'] == 'primary' for c in calls) == 1
        assert not any(c['kind'] == 'auxiliary' and
                       c['value'].get('task') == 'coach_goal_disposition' for c in calls)
    finally:
        done.set()
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            pass
        reader.join(timeout=2)
        os.close(master)
        (home / 'pty-output.txt').write_bytes(output)


if __name__ == '__main__':
    child_main()
