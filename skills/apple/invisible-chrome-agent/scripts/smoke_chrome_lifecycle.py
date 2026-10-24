#!/usr/bin/env python3
"""Opt-in real, headless CDP smoke. Never uses the installed agent/profile.

Run only with explicit permission: python3 smoke_chrome_lifecycle.py --chrome /absolute/path/to/chrome
This does not verify virtual-display, launchd, focus, passkeys, or browser-use compatibility.
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

from chrome_agent_lifecycle import Ledger, Lifecycle


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
    args = parser.parse_args()
    if not args.chrome.is_absolute() or not os.access(args.chrome, os.X_OK):
        parser.error('--chrome must be an absolute executable path')
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
        child = proxy = client = None
        try:
            for sock in reserved:
                sock.close()
            child = subprocess.Popen([str(args.chrome), '--headless=new', '--no-first-run',
                                      '--no-default-browser-check', '--user-data-dir=' + str(profile),
                                      '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=' + str(upstream),
                                      'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 30
            while not api.cdp_version(port=upstream):
                if child.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('isolated Chrome did not start')
                time.sleep(0.1)
            lifecycle = Lifecycle(Ledger(root / 'leases.json'), 'smoke-installation', wake=lambda: None)
            proxy = api.CDPProxy(port, upstream, lifecycle=lifecycle, show=lambda: False).start()
            base = 'http://127.0.0.1:%d' % port

            def request(path, data=None):
                req = urllib.request.Request(base + path, data=None if data is None else json.dumps(data).encode(),
                                             headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=10) as response:
                    return json.load(response)

            lease = request('/lifecycle/acquire', dict(installation_id='smoke-installation',
                            owner=[str(root), 'smoke-task', 'smoke-session'], timeout_s=60))
            scope = '/lease/' + lease['capability']
            assert request(scope + '/json/list') == [], 'startup tab must remain unowned'
            client = api.CDP(request(scope + '/json/version')['webSocketDebuggerUrl'])
            tid = client.call('Target.createTarget', {'url': 'about:blank'})['targetId']
            sid = client.call('Target.attachToTarget', {'targetId': tid, 'flatten': True})['sessionId']
            answer = client.call('Runtime.evaluate', {'expression': '6 * 7', 'returnByValue': True}, session=sid)
            assert answer['result']['value'] == 42
            assert [t['id'] for t in request(scope + '/json/list')] == [tid]
            client.call('Target.closeTarget', {'targetId': tid})
            request('/lifecycle/complete', dict(installation_id='smoke-installation',
                    capability=lease['capability'], request_id=lease['request_id']))
            request('/lifecycle/release', dict(installation_id='smoke-installation', capability=lease['capability']))
            print('PASS: isolated real Chrome acquire/create/attach/evaluate/close/complete/release')
        finally:
            for sock in reserved:
                sock.close()
            if client:
                client.close()
            if proxy:
                proxy.close()
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)


if __name__ == '__main__':
    main()
