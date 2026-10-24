#!/usr/bin/env python3
"""Real isolated CDP acquire/use/release -> idle exit -> request wake smoke.

Never uses an installed agent/profile. --headful is opt-in and may show a window;
this harness does not prove virtual-display, launchd, passkeys or focus-guard behavior.
"""
import argparse
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request

from chrome_agent_lifecycle import Config, Ledger, Lifecycle


def validate_isolation(port, upstream, profile, root):
    if port in (9333, 9334) or upstream in (9333, 9334) or port == upstream:
        raise ValueError('refusing live/reserved ports')
    if not (1024 <= port <= 65535 and 1024 <= upstream <= 65535):
        raise ValueError('invalid smoke ports')
    if Path(profile).resolve() != Path(root).resolve() / 'profile' or Path(profile).exists():
        raise ValueError('refusing existing or non-isolated profile')


def reserve_port():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    return sock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chrome', required=True, type=Path)
    parser.add_argument('--headful', action='store_true')
    parser.add_argument('--test-idle-seconds', type=int, default=1)
    args = parser.parse_args()
    if not args.chrome.is_absolute() or not os.access(args.chrome, os.X_OK):
        parser.error('--chrome must be an absolute executable path')
    if not 1 <= args.test_idle_seconds <= 10:
        parser.error('--test-idle-seconds must be 1..10')
    loader = importlib.machinery.SourceFileLoader('smoke_hca', str(Path(__file__).with_name('hermes-chrome-agent')))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    api = importlib.util.module_from_spec(spec)
    loader.exec_module(api)
    with tempfile.TemporaryDirectory(prefix='chrome-lease-smoke-') as directory:
        root = Path(directory)
        profile = root / 'profile'
        reserved = [reserve_port(), reserve_port()]
        port, upstream = [sock.getsockname()[1] for sock in reserved]
        validate_isolation(port, upstream, profile, root)
        children, events = [], []
        proxy = client = None
        ledger = Ledger(root / 'leases.json', Config(orphan_s=1, idle_browser_s=args.test_idle_seconds))
        log = (root / 'chrome.log').open('w+')

        def stop():
            for child in children:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=10)
                    events.append(('stop', child.pid))

        def wake():
            assert not api.cdp_version(port=upstream), 'refusing occupied upstream port'
            ledger.new_generation()
            argv = [str(args.chrome), '--no-first-run', '--no-default-browser-check',
                    '--user-data-dir=' + str(profile), '--remote-debugging-address=127.0.0.1',
                    '--remote-debugging-port=' + str(upstream), '--no-startup-window']
            if not args.headful:
                argv.append('--headless=new')
            child = subprocess.Popen(argv, stdout=log, stderr=log)
            children.append(child)
            deadline = time.monotonic() + 30
            while not api.cdp_version(port=upstream):
                if child.poll() is not None or time.monotonic() > deadline:
                    log.flush()
                    raise RuntimeError('isolated Chrome did not start; inspect smoke Chrome output')
                time.sleep(0.1)
            events.append(('wake', child.pid))

        try:
            for sock in reserved:
                sock.close()
            lifecycle = Lifecycle(ledger, 'smoke-installation', wake=wake, stop=stop)
            proxy = api.CDPProxy(port, upstream, lifecycle=lifecycle, show=lambda: False).start()
            base = 'http://127.0.0.1:%d' % port

            def request(path, data=None):
                req = urllib.request.Request(base + path, data=None if data is None else json.dumps(data).encode(),
                                             headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=40) as response:
                    return json.load(response)

            def acquire():
                return request('/lifecycle/acquire', dict(installation_id='smoke-installation',
                               owner=[str(root), 'smoke-task', 'smoke-session'], timeout_s=60))

            lease = acquire()
            scope = '/lease/' + lease['capability']
            client = api.CDP(request(scope + '/json/version')['webSocketDebuggerUrl'])
            tid = client.call('Target.createTarget', {'url': 'about:blank'})['targetId']
            sid = client.call('Target.attachToTarget', {'targetId': tid, 'flatten': True})['sessionId']
            answer = client.call('Runtime.evaluate', {'expression': '6 * 7', 'returnByValue': True}, session=sid)
            assert answer['result']['value'] == 42
            assert [t['id'] for t in request(scope + '/json/list')] == [tid]
            request('/lifecycle/complete', dict(installation_id='smoke-installation',
                    capability=lease['capability'], request_id=lease['request_id']))
            request('/lifecycle/release', dict(installation_id='smoke-installation', capability=lease['capability']))
            client.close()
            client = None
            deadline = time.monotonic() + 20
            while lifecycle.running and time.monotonic() < deadline:
                version = api.cdp_version(port=upstream)
                assert version, 'browser exited before lifecycle stop'
                cdp = api.CDP(version['webSocketDebuggerUrl'])
                try:
                    targets = [t['targetId'] for t in cdp.call('Target.getTargets')['targetInfos'] if t['type'] == 'page']
                    lifecycle.tick(targets, lambda target: cdp.call('Target.closeTarget', {'targetId': target})['success'])
                finally:
                    cdp.close()
                time.sleep(0.1)
            assert not lifecycle.running, 'browser did not become idle (unknown tabs block shutdown)'
            assert children[0].poll() is not None
            assert not api.cdp_version(port=upstream), 'CDP still reachable after idle exit'
            next_lease = acquire()
            assert next_lease['capability'] != lease['capability']
            assert len(children) == 2 and children[1].poll() is None
            assert api.cdp_version(port=upstream)
            request('/lifecycle/release', dict(installation_id='smoke-installation', capability=next_lease['capability']))
            assert [event for event, _ in events] == ['wake', 'stop', 'wake']
            print('PASS real isolated Chrome: create/use/release -> idle process/CDP exit -> request wake; events=%r' % events)
        finally:
            for sock in reserved:
                sock.close()
            if client:
                client.close()
            if proxy:
                proxy.close()
            stop()
            log.close()
            assert all(child.poll() is not None for child in children), 'smoke child survived cleanup'


if __name__ == '__main__':
    main()
