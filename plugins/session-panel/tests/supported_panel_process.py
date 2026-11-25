"""No synthetic cut: real panel lifecycle, legacy lanes, strict census, broker and child."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(sys.argv[1]).resolve()
DASH = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(ROOT))
from hermes_cli.process_identity import register_self
from hermes_cli.serve_producer_admission import supported_launch
from hermes_cli.serve_producer_mode import write_mode

home = Path(os.environ['HERMES_HOME'])
peer_mode = len(sys.argv) > 3 and sys.argv[3] == 'peer'
assert register_self('serve', project_root=ROOT)
supported_launch('serve')
with sqlite3.connect(home / 'state.db') as db:
    db.execute('CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,outcome_json TEXT)')
if not peer_mode:
    write_mode(home, external=False)
name = '_supported_panel_probe'
spec = importlib.util.spec_from_file_location(name, DASH / 'plugin_api.py')
api = importlib.util.module_from_spec(spec)
sys.modules[name] = api
spec.loader.exec_module(api)
lifecycle = api.install_panel_lifecycle({})
if peer_mode:
    original_admit = lifecycle._admit
    def diagnostic_admit():
        gate, cut = original_admit()
        if not cut.complete:
            print('fixture census errors: ' + repr(cut.errors), file=sys.stderr, flush=True)
            for record in cut.census.processes:
                print('fixture process: ' + repr(record), file=sys.stderr, flush=True)
        return gate, cut
    lifecycle._admit = diagnostic_admit

def wait(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        lifecycle._stop.wait(0.05)
    assert predicate(), (lifecycle.state, lifecycle.reason)

try:
    if peer_mode:
        wait(lambda: lifecycle.state in ('external', 'unavailable'))
        assert lifecycle.state == 'external', lifecycle.reason
        (home / 'peer-ready.json').write_text(json.dumps({'pid': os.getpid(), 'broker': lifecycle.relay.broker_pid}))
        sys.stdin.buffer.read()
        lifecycle.stop_watching()
        assert lifecycle.relay.close(timeout=30)  # ordinary client exit is NOT global mode rollback
        lifecycle.relay = None
        raise SystemExit(0)
    wait(lambda: getattr(sys.modules.get(api._FEED_KEY), 'PROCESS_FEED', None) is not None)
    assert lifecycle.state == 'off'
    # A real supported non-hosting peer (a gateway) is enrolled and alive through the whole external window.
    gateway = subprocess.Popen(
        [sys.executable, '-I', '-c',
         'import sys; sys.path.insert(0, sys.argv[1])\n'
         'from hermes_cli.process_identity import register_self\n'
         'from hermes_cli.serve_producer_admission import supported_launch\n'
         "assert register_self('gateway', project_root=sys.argv[1])\n"
         "assert supported_launch('gateway') is not None\n"
         "print('ready', flush=True); sys.stdin.buffer.read()", str(ROOT)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert gateway.stdout is not None and gateway.stdout.readline().strip() == b'ready'
    write_mode(home, external=True)
    wait(lambda: lifecycle.state in ('external', 'unavailable'))
    assert lifecycle.state == 'external', lifecycle.reason
    first = lifecycle.relay
    body = {'_verb': 'status', '_wire_identity': {'feed': api.FEED_ID, 'profile': api.LAUNCH_PROFILE}}
    key = first.submit({'body': body, 'accepted': time.monotonic()})
    wait(lambda: first.read(key) is not None)
    status = json.loads(first.read(key))
    assert status['running'] is True, status
    print(json.dumps({'gate': 'real panel OFF to external', 'pid': os.getpid(), 'broker': first.broker_pid,
                      'status': status, 'legacy_fenced': api._legacy_fenced()}), flush=True)
    # A real serve imported tui_gateway.server (whose import-time update check spawns git/ssh) at its own
    # startup; the fixture's lazy event import must finish that probe before a second client's strict cut.
    import tui_gateway.server  # noqa: F401
    from hermes_cli import banner
    assert banner._update_check_done.wait(60)
    peer = subprocess.Popen([sys.executable, '-I', str(Path(__file__).resolve()), str(ROOT), str(DASH), 'peer'],
                            stdin=subprocess.PIPE)
    try:
        wait(lambda: (home / 'peer-ready.json').exists() or peer.poll() is not None)
        assert peer.poll() is None, peer.returncode
        attached = json.loads((home / 'peer-ready.json').read_text())
        assert attached['broker'] == first.broker_pid
        print(json.dumps({'gate': 'second supported client', **attached}), flush=True)
    finally:
        assert peer.stdin is not None
        peer.stdin.close()
        peer.wait(timeout=60)
    assert peer.returncode == 0
    print('fixture: setting OFF', file=sys.stderr, flush=True)
    write_mode(home, external=False)
    print('fixture: waiting OFF', file=sys.stderr, flush=True)
    wait(lambda: lifecycle.state in ('off', 'unavailable'))
    print(f'fixture: OFF result {lifecycle.state} {lifecycle.reason}', file=sys.stderr, flush=True)
    assert lifecycle.state == 'off', lifecycle.reason
    legacy = sys.modules[api._FEED_KEY].PROCESS_FEED
    assert legacy is not None and not legacy.stopped.is_set()
    assert first.read(key) is None, 'retired generation cannot return held status'
    print(json.dumps({'gate': 'same PID external to OFF', 'pid': os.getpid(),
                      'old_relay_retired': first.retired, 'legacy_fenced': api._legacy_fenced()}), flush=True)
    # A retained callback or released witness must not prevent a second symmetric transition.
    write_mode(home, external=True)
    wait(lambda: lifecycle.state in ('external', 'unavailable'))
    assert lifecycle.state == 'external', lifecycle.reason
    assert legacy.stopped.is_set() and lifecycle.relay is not first
    second = lifecycle.relay
    second_key = second.submit({'body': body, 'accepted': time.monotonic()})
    wait(lambda: second.read(second_key) is not None)
    assert json.loads(second.read(second_key))['running'] is True
    write_mode(home, external=False)
    wait(lambda: lifecycle.state in ('off', 'unavailable'))
    assert lifecycle.state == 'off', lifecycle.reason
    successor = sys.modules[api._FEED_KEY].PROCESS_FEED
    assert successor is not legacy and not successor.stopped.is_set()
    assert second.retired and second.read(second_key) is None
    print(json.dumps({'gate': 'second same PID cycle', 'pid': os.getpid()}), flush=True)
finally:
    gateway_peer = globals().get('gateway')
    if gateway_peer is not None:
        gateway_peer.stdin.close()
        gateway_peer.wait(timeout=60)
    lifecycle.stop_watching()
    if lifecycle.relay is not None:
        api.detach_external(lifecycle.relay)
        assert lifecycle.relay.retire(timeout=30)
    server = api._LOADER.panel_retirement
    assert server.participant.retire(server.drain)
    server.close()
