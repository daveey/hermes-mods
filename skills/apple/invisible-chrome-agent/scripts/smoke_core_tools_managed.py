#!/usr/bin/env python3
"""Real isolated smoke: Hermes core built-in browser tools through a managed lifecycle proxy.

Drives the installed hermes-agent's browser_navigate / browser_snapshot / browser_click (agent-browser
over the lease-scoped CDP root), browser_cdp and browser_console against a disposable headless Chrome
behind this repo's CDPProxy + Lifecycle, then proves: task release -> idle process/CDP exit -> the next
tool call wakes exactly one browser, also when two tasks wake it concurrently.

Never touches the installed agent profile, 9333/9334, launchd or the user's config: the core tools run
in a subprocess with a throwaway HERMES_HOME whose config points at the isolated proxy.
Usage: smoke_core_tools_managed.py --chrome <Chrome binary> --hermes-agent <hermes-agent checkout>
"""
import argparse
import http.server
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

from chrome_agent_lifecycle import Config, Ledger, Lifecycle
from chrome_test_fixtures import chrome_environment, fresh_profile_arguments
from smoke_chrome_lifecycle import reserve_port, validate_isolation

PAGE = (b'<!doctype html><title>start</title><h1>Lease smoke</h1>'
        b'<button id="b" onclick="document.title=\'clicked\'">Press me</button>')
INSTALLATION = 'smoke-installation'

CLIENT = r'''
import json, re, sys
import tools.browser_tool, tools.browser_cdp_tool  # registers the built-in browser tools
from tools.registry import registry
from tools.browser_tool_lifecycle import cleanup_browser
phase, task, url = sys.argv[1:4]


def call(name, **args):
    # The model's dispatch path: registry handler -> routed handler -> owner lease -> tool.
    raw = registry.dispatch(name, args, task_id=task)
    return json.loads(raw) if isinstance(raw, str) else raw


out = {}
try:
    nav = call("browser_navigate", url=url)
    out["navigate"] = {k: nav.get(k) for k in ("success", "url", "title", "error")}
    if phase == "full":
        snap = call("browser_snapshot")
        text = snap.get("snapshot", "")
        ref = re.search(r'button "Press me" \[ref=(e\d+)\]', text)
        out["snapshot_has_button"] = bool(ref)
        click = call("browser_click", ref="@" + ref.group(1)) if ref else {}
        out["click"] = {k: click.get(k) for k in ("success", "error")}
        title = call("browser_console", expression="document.title")
        out["title_after_click"] = title.get("result")
        targets = call("browser_cdp", method="Target.getTargets")
        infos = (targets.get("result") or {}).get("targetInfos") or []
        out["cdp_page_urls"] = sorted(t["url"] for t in infos if t.get("type") == "page")
        if not infos:
            out["cdp_raw"] = str(targets)[:500]
finally:
    cleanup_browser(task)
    out["released"] = True
print("RESULT " + json.dumps(out))
'''


def ledger_snapshot(ledger):
    with ledger.transaction(readonly=True) as data:
        return json.loads(json.dumps(data, default=str))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chrome', required=True, type=Path)
    parser.add_argument('--hermes-agent', required=True, type=Path)
    parser.add_argument('--idle-seconds', type=int, default=3)
    args = parser.parse_args()
    core = args.hermes_agent.resolve()
    runner = core / 'scripts' / 'run-in-hermes-env'
    if not os.access(args.chrome, os.X_OK) or not os.access(runner, os.X_OK):
        parser.error('need an executable --chrome and <hermes-agent>/scripts/run-in-hermes-env')
    loader = importlib.machinery.SourceFileLoader('smoke_hca', str(Path(__file__).with_name('hermes-chrome-agent')))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    api = importlib.util.module_from_spec(spec)
    loader.exec_module(api)

    allocation = tempfile.TemporaryDirectory(prefix='core-lease-smoke-')
    with allocation as directory:
        root = Path(directory)
        profile = root / 'profile'
        profile_args = fresh_profile_arguments(allocation, profile)
        reserved = [reserve_port(), reserve_port()]
        port, upstream = [sock.getsockname()[1] for sock in reserved]
        validate_isolation(port, upstream, profile, root)
        children, events, stop_tick = [], [], threading.Event()
        log = (root / 'chrome.log').open('w+')
        ledger = Ledger(root / 'leases.json', Config(orphan_s=2, idle_browser_s=args.idle_seconds))
        wake_lock = threading.Lock()

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
            with wake_lock:
                assert not api.cdp_version(port=upstream), 'refusing occupied upstream port'
                ledger.new_generation()
                argv = [str(args.chrome), '--no-first-run', '--no-default-browser-check', *profile_args,
                        '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=' + str(upstream),
                        '--no-startup-window', '--headless=new']
                child = subprocess.Popen(argv, stdout=log, stderr=log, env=dict(os.environ, **chrome_environment(root)))
                children.append(child)
                deadline = time.monotonic() + 30
                while not api.cdp_version(port=upstream):
                    if child.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('isolated Chrome did not start')
                    time.sleep(0.1)
                events.append(('wake', child.pid))
                from chrome_agent_process import owns_listener, record
                identity = record(child)
                assert owns_listener(identity, upstream)
                with ledger.transaction() as data:
                    data['browser'] = dict(identity, profile=str(profile.resolve()),
                                           websocket=api.cdp_version(port=upstream)['webSocketDebuggerUrl'])

        class Page(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(PAGE)

            def log_message(self, *a):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Page)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        page_url = 'http://127.0.0.1:%d/smoke' % server.server_address[1]
        lifecycle = Lifecycle(ledger, INSTALLATION, wake=wake, stop=stop)
        for sock in reserved:
            sock.close()
        proxy = api.CDPProxy(port, upstream, lifecycle=lifecycle, show=lambda: False).start()
        endpoint = 'http://127.0.0.1:%d' % port

        def ticker():
            # Mirrors Supervisor.lifecycle_tick: one observation connection, reap, idle stop.
            while not stop_tick.is_set():
                try:
                    if lifecycle.running and not wake_lock.locked():
                        version = api.cdp_version(port=upstream)
                        if version:
                            cdp = api.CDP(version['webSocketDebuggerUrl'])
                            try:
                                targets = [t['targetId'] for t in cdp.call('Target.getTargets')['targetInfos']
                                           if t.get('type') in ('page', 'webview')]
                                lifecycle.tick(targets, lambda tid: cdp.call('Target.closeTarget', {'targetId': tid}).get('success', False))
                            finally:
                                cdp.close()
                except Exception as exc:  # noqa: BLE001 - the real supervisor logs and keeps looping too
                    events.append(('tick-error', repr(exc)[:200]))
                stop_tick.wait(0.5)

        home = root / 'hermes-home'
        home.mkdir()
        (home / 'config.yaml').write_text(
            'browser:\n  allow_private_urls: true\n  cdp_url: %s\n  managed_agent:\n    endpoint: %s\n    installation_id: %s\n'
            % (endpoint, endpoint, INSTALLATION))
        env = dict(os.environ, HERMES_HOME=str(home), PYTHONPATH=str(core), TMPDIR=str(root))
        env.pop('BU_CDP_URL', None)
        env.pop('BU_CDP_WS', None)

        def client(phase, task):
            return subprocess.Popen([str(runner), 'python', '-c', CLIENT, phase, task, page_url], cwd=str(core), env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def result(proc):
            out, err = proc.communicate(timeout=240)
            line = [x for x in out.splitlines() if x.startswith('RESULT ')]
            assert proc.returncode == 0 and line, 'client failed rc=%s\n%s\n%s' % (proc.returncode, out[-2000:], err[-4000:])
            return json.loads(line[-1][7:])

        def wait_idle(label):
            deadline = time.monotonic() + 60
            while lifecycle.running and time.monotonic() < deadline:
                time.sleep(0.2)
            assert not lifecycle.running, label + ': browser did not go idle; ledger=%s' % json.dumps(ledger_snapshot(ledger))[:1500]
            assert all(c.poll() is not None for c in children), label + ': Chrome process still alive'
            assert not api.cdp_version(port=upstream), label + ': CDP still reachable'
            print('OK %s: idle exit (process %s gone, CDP %d closed)' % (label, children[-1].pid, upstream))

        threading.Thread(target=ticker, daemon=True).start()
        try:
            assert not lifecycle.running and not children, 'browser must start idle-off'
            print('OK start: no browser running; proxy %s listening' % endpoint)
            first = result(client('full', 'smoke-task-a'))
            print('phase1 ' + json.dumps(first))
            assert first['navigate']['success'], first
            assert first['snapshot_has_button'] and first['click']['success'], first
            assert first['title_after_click'] == 'clicked', first
            assert page_url in first['cdp_page_urls'], first
            assert len(children) == 1, events
            leases = list(ledger_snapshot(ledger).get('leases', {}).values())
            assert leases and all(l.get('released') is not None for l in leases), 'task release left a live lease: %r' % leases
            assert {l['owner'][1:] == ['smoke-task-a', 'tools'] for l in leases} == {True}, leases
            print('OK phase1: navigate/snapshot/click/console/cdp held one task-a tools lease; released on cleanup=%d/%d'
                  % (sum(l.get('released') is not None for l in leases), len(leases)))
            wait_idle('phase1')
            a, b = client('nav', 'smoke-task-b'), client('nav', 'smoke-task-c')
            second, third = result(a), result(b)
            print('phase2 ' + json.dumps([second, third]))
            assert second['navigate']['success'] and third['navigate']['success'], (second, third)
            wakes = [e for e in events if e[0] == 'wake']
            assert len(wakes) == 2 and len(children) == 2, 'concurrent wake must start exactly one browser: %r' % events
            print('OK phase2: two concurrent tasks woke exactly one browser (pid %s)' % children[1].pid)
            wait_idle('phase2')
            print('events=%r' % [e for e in events if e[0] != 'tick-error'])
            errors = [e for e in events if e[0] == 'tick-error']
            print('tick-errors=%d %s' % (len(errors), errors[:3]))
            print('PASS core tools over managed lease: use/release -> idle exit -> wake (single, concurrent)')
        finally:
            stop_tick.set()
            proxy.close()
            for connection in list(lifecycle.connections):
                connection.close()
            stop()
            server.shutdown()
            log.close()
            subprocess.run(['pkill', '-f', str(root)], check=False)
            assert all(c.poll() is not None for c in children), 'smoke child survived cleanup'


if __name__ == '__main__':
    sys.exit(main())
