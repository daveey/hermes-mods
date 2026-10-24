"""Real same-capability harness reload followed by quiet/worker expiry."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from chrome_agent_daemons import exchange
from chrome_agent_lifecycle import Config, Ledger, Lifecycle
from chrome_agent_process import fingerprint
import test_chrome_endpoint_ownership as endpoints
from chrome_test_fixtures import socket_directory

hca = endpoints.hca


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('browser-harness') and shutil.which('google-chrome'),
                     'real Linux Chrome and harness required')
class HarnessRetirementTests(unittest.TestCase):
    def test_same_capability_reload_then_quiet_expiry_preserves_active_sibling(self):
        self.exercise_expiry(worker_exit=False)

    def test_same_capability_reload_then_worker_expiry_preserves_active_sibling(self):
        self.exercise_expiry(worker_exit=True)

    def exercise_expiry(self, worker_exit):
        fixture = endpoints.EndpointOwnershipTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        supervisor = fixture.supervisor
        def stop_browser_tree():
            # Chrome's network child can recreate its cache after the parent
            # exits. Wait for captured descendants before removing our profile.
            rows = subprocess.check_output(['ps', '-eo', 'pid=,ppid='], text=True)
            parents = {int(pid): int(parent) for pid, parent in (row.split() for row in rows.splitlines())}
            owned = {supervisor.chrome.pid} if supervisor.chrome else set()
            while True:
                children = {pid for pid, parent in parents.items() if parent in owned}
                if children <= owned:
                    break
                owned.update(children)
            supervisor.idle_managed()
            deadline = time.monotonic() + 10
            while any(fingerprint(pid) for pid in owned) and time.monotonic() < deadline:
                threading.Event().wait(.02)
            self.assertFalse([pid for pid in owned if fingerprint(pid)])
        self.addCleanup(stop_browser_tree)
        now = [1000]
        ledger = Ledger(fixture.root / 'retirement.json',
                        Config(idle_lease_s=90 if worker_exit else 2, orphan_s=1), clock=lambda: now[0])
        life = Lifecycle(ledger, 'test', supervisor.wake_managed, supervisor.idle_managed)
        supervisor.lifecycle = life
        runtime = socket_directory()
        self.addCleanup(runtime.cleanup)
        worker = subprocess.Popen(['sleep', '180'])
        def stop_worker():
            if worker.poll() is None:
                worker.terminate()
            worker.wait(timeout=5)
        self.addCleanup(stop_worker)
        environments, pids = [], []
        def cleanup_harnesses():
            for env in environments:
                subprocess.run(['browser-harness', '--reload'], env=env, capture_output=True, timeout=20)
            deadline = time.monotonic() + 5
            while any(fingerprint(pid) for pid in pids) and time.monotonic() < deadline:
                threading.Event().wait(.02)
            self.assertFalse([pid for pid in pids if fingerprint(pid)])
            print('HARNESS EXPIRY CLEANUP', pids, flush=True)
        self.addCleanup(cleanup_harnesses)
        def display():
            supervisor.vd = subprocess.Popen(['sleep', '180'])
        proxy = hca.CDPProxy(0, fixture.port, lifecycle=life, show=lambda: False, logf=lambda _: None).start()
        self.addCleanup(proxy.close)
        with mock.patch.object(supervisor, 'start_vdisplay', side_effect=display), \
             mock.patch.object(supervisor, 'rehome'), mock.patch.object(supervisor, 'evict'):
            def acquire(owner, pid):
                grant = life.control('acquire', dict(installation_id='test',
                    owner=[str(fixture.root), owner, 'default'], timeout_s=60,
                    worker_pid=pid, daemon_dir=runtime.name), 'http://127.0.0.1:%d' % proxy.port)
                env = dict(os.environ, HERMES_HOME=str(fixture.root), BH_HOME=str(fixture.root / 'bh'),
                           BH_RUNTIME_DIR=runtime.name, BH_RUNTIME_DIR_SHARED='1', BH_TMP_DIR=runtime.name,
                           BH_TMP_DIR_SHARED='1', BU_AUTOSPAWN='0', BU_NAME=grant['daemon_name'],
                           BU_CDP_URL=grant['endpoint'])
                env.pop('BU_CDP_WS', None)
                environments.append(env)
                path = str(Path(runtime.name) / ('bu-' + grant['daemon_name'] + '.sock'))
                return grant, env, path
            def run(env, path):
                result = subprocess.run(['browser-harness'], input="new_tab('about:blank'); print('owned')",
                                        text=True, env=env, capture_output=True, timeout=40)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                pid = exchange(path, {'meta': 'ping'})['pid']
                pids.append(pid)
                return pid
            grant, env, path = acquire('expiring', worker.pid)
            old = run(env, path)
            ledger.sweep_daemons()
            with ledger.transaction(readonly=True) as data:
                self.assertEqual(data['leases'][grant['capability']]['daemon']['pid'], old)
            result = subprocess.run(['browser-harness', '--reload'], env=env, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            replacement = run(env, path)
            self.assertNotEqual(old, replacement)
            sibling, sibling_env, sibling_path = acquire('active', os.getpid())
            sibling_pid = run(sibling_env, sibling_path)
            browser_pid = supervisor.chrome.pid
            ledger.complete(grant['capability'], grant['request_id'])
            if worker_exit:
                stop_worker()
                ledger.reap_workers()
            now[0] += 3
            with mock.patch.object(life, 'wake', side_effect=AssertionError('expiry woke browser')):
                deadline = time.monotonic() + 10
                while True:
                    supervisor.managed_step()
                    if life.daemon_sweep:
                        life.daemon_sweep.join(2)
                    with ledger.transaction(readonly=True) as data:
                        registered = bool(data['leases'].get(grant['capability'], {}).get('daemon_socket'))
                    if fingerprint(replacement) is None and not registered:
                        break
                    self.assertLess(time.monotonic(), deadline, 'restarted harness not retired by expiry')
                    threading.Event().wait(.02)
                self.assertEqual(supervisor.chrome.pid, browser_pid)
                self.assertIsNone(supervisor.chrome.poll())
                self.assertEqual(exchange(sibling_path, {'meta': 'ping'})['pid'], sibling_pid)
                with ledger.transaction(readonly=True) as data:
                    self.assertIn(sibling['request_id'], data['leases'][sibling['capability']]['requests'])
            print('HARNESS EXPIRY VERIFIED', dict(worker_exit=worker_exit, old=old,
                  replacement=replacement, sibling=sibling_pid, browser=browser_pid), flush=True)
