"""Real Electron renderer -> production serve -> local provider guard acceptance.
Requires a built core desktop and its installed Electron/Playwright dependencies.
No WebSocket client stands in for the renderer. Missing build/Xvfb skips; skipped is not acceptance.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import shutil
from http.server import ThreadingHTTPServer
import yaml
import pytest
from test_goal_disposition_transport import isolated_env, TEXTS
from test_goal_serve_acceptance import _ready
from electron_control_model import Model


@pytest.mark.parametrize('control', ['initial', 'stop', 'queue', 'reconnect', 'reload', 'all-sockets'])
def test_real_electron_guard(tmp_path, control):
    import hermes_constants
    core = Path(hermes_constants.__file__).resolve().parent
    if sys.platform != 'linux' or not shutil.which('xvfb-run') or not (core / 'apps/desktop/dist/electron-main.mjs').exists():
        pytest.skip('Optional Electron acceptance requires a built Linux Desktop and Xvfb')
    home = tmp_path / 'home'
    env = isolated_env(home)
    fence = Path(__file__).with_name('electron_loopback_fence.c')
    library = home/'loopback-fence.so'
    subprocess.run(['cc','-shared','-fPIC','-O2',str(fence),'-o',str(library),'-ldl'],check=True)
    env['LD_PRELOAD'] = str(library)
    token = 'synthetic-electron-acceptance-token'
    env.update(HERMES_DASHBOARD_SESSION_TOKEN=token, HERMES_DESKTOP_READY_FILE=str(home/'ready.json'))
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    provider.calls = []
    provider.release = threading.Event()
    provider.home = home
    provider.control = control
    if control == 'initial':
        provider.release.set()
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    endpoint = f'http://127.0.0.1:{provider.server_port}/v1'
    config = yaml.safe_load((home/'config.yaml').read_text())
    config['model'] = dict(default='acceptance-primary', provider='custom', base_url=endpoint)
    config['auxiliary'] = {name:dict(provider='custom',model=model,base_url=endpoint,api_key='local-test') for name,model in [('coach_goal_disposition','acceptance-judge'),('title','acceptance-title')]}
    (home/'config.yaml').write_text(yaml.safe_dump(config))
    errors = home/'serve-stderr.log'
    with errors.open('w') as err, (home/'serve-stdout.log').open('w') as out:
        process = subprocess.Popen([sys.executable,str(Path(__file__).with_name('test_goal_serve_acceptance.py')),'--child'],env=env,cwd=home,stdout=out,stderr=err)
        try:
            port = _ready(home/'ready.json',process,errors)
            env.update(HERMES_DESKTOP_HERMES_ROOT=str(core),HERMES_DESKTOP_PYTHON=sys.executable,HERMES_DESKTOP_ISOLATED_BACKEND='1')
            driver=Path(__file__).with_name('electron_guard_driver.mjs')
            result=subprocess.run(['xvfb-run','-a','node',str(driver),str(core),str(home),f'http://127.0.0.1:{port}',token,control],env=env,cwd=home,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=260)
            (home/'electron.log').write_text(result.stdout)
            assert result.returncode == 0, result.stdout + repr([(str(p),p.read_text()[-14000:]) for p in home.rglob('desktop.log')])
            receipt=json.loads((home/'electron-receipt.json').read_text())
            assert receipt['passed'] and receipt['electron']
            with sqlite3.connect(home/'state.db') as db:
                persisted='\n'.join(row[0] or '' for row in db.execute("SELECT content FROM messages WHERE role='assistant'"))
                # Queued sends retain an inactive accept-time row; stop adds a typed runtime marker.
                user_rows = db.execute("SELECT session_id, content FROM messages WHERE role='user' AND active=1 AND COALESCE(display_kind, '') != 'turn_stopped' ORDER BY timestamp").fetchall()
            assert len(user_rows) == 2, user_rows
            assert len({r[0] for r in user_rows}) == 1, user_rows
            assert user_rows[0][1].startswith('Repair example app'), user_rows
            assert user_rows[1][1].startswith('Explain HTTP 503'), user_rows
            assert TEXTS['abandoned'] not in persisted
            assert TEXTS['analysis'] in persisted
            if control != 'stop':
                assert 'Unresolved goal' in persisted
            else:
                assert 'Unresolved goal' not in persisted
            assert sum(c.get('model')=='acceptance-judge' for c in provider.calls)==(1 if control == 'stop' else 4)
        finally:
            provider.release.set()
            process.terminate()
            try: process.wait(timeout=20)
            except subprocess.TimeoutExpired: process.kill();process.wait(timeout=10)
            provider.shutdown();provider.server_close();thread.join(timeout=5)
            (home/'provider.json').write_text(json.dumps(provider.calls,indent=2))
            if destination := os.environ.get('GUARD_ELECTRON_ARTIFACTS'):
                shutil.copytree(home, destination, dirs_exist_ok=True, symlinks=True)
