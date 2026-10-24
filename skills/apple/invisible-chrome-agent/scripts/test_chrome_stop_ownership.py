"""Explicit supervisor stop must retire separately spawned owned harnesses."""

import os
from pathlib import Path
import shutil

import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from test_hermes_chrome_agent import hca
from chrome_agent_lifecycle import Ledger, Lifecycle, Config
from chrome_agent_daemons import exchange
from chrome_agent_process import fingerprint
from test_chrome_endpoint_ownership import free_port
from chrome_test_fixtures import chrome_environment, socket_directory


class IdleRetirementTests(unittest.TestCase):
    def test_idle_supervisor_retries_daemon_cleanup_without_wake(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / 'ledger.json')
            supervisor = hca.Supervisor()
            supervisor.lifecycle = Lifecycle(ledger, 'test', supervisor.wake_managed)
            with mock.patch.object(ledger, 'sweep_daemons') as sweep, \
                 mock.patch.object(supervisor, 'wake_managed') as wake:
                supervisor.managed_step()
                if supervisor.lifecycle.daemon_sweep:
                    supervisor.lifecycle.daemon_sweep.join(3)
                supervisor.managed_step()
                if supervisor.lifecycle.daemon_sweep:
                    supervisor.lifecycle.daemon_sweep.join(3)
                self.assertGreaterEqual(sweep.call_count, 2)
                wake.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('browser-harness') and shutil.which('google-chrome'),
                     'real Linux Chrome and harness required')
class SupervisorStopTests(unittest.TestCase):
    def test_supervisor_only_stop_drains_then_verifies_daemon_exit(self):
        self.exercise_stop(finish_requests=True)

    def test_explicit_stop_wins_only_after_real_thirty_second_drain(self):
        self.exercise_stop(finish_requests=False)

    def test_supervisor_only_stop_reconciles_repeated_same_capability_reload(self):
        self.exercise_stop(finish_requests=True, reloads=2)

    def exercise_stop(self, finish_requests, reloads=0):
        with socket_directory() as tmp, mock.patch.dict(os.environ, chrome_environment(tmp)):
            root = Path(tmp)
            port, upstream = free_port(), free_port()
            self.assertNotEqual(port, upstream)
            wrapper = root / 'chrome'
            wrapper.write_text('#!/bin/sh\nexec /usr/bin/google-chrome --headless=new "$@"\n')
            wrapper.chmod(0o700)
            supervisor = hca.Supervisor()
            ledger = Ledger(root / 'ledger.json', Config(orphan_s=1))
            supervisor.lifecycle = Lifecycle(ledger, 'test', supervisor.wake_managed, supervisor.idle_managed)
            env = dict(os.environ, HERMES_HOME=tmp, BH_HOME=str(root / 'bh'), BH_RUNTIME_DIR=tmp,
                       BH_RUNTIME_DIR_SHARED='1', BH_TMP_DIR=tmp, BH_TMP_DIR_SHARED='1', BU_AUTOSPAWN='0')
            env.pop('BU_CDP_WS', None)
            env.pop('BU_CDP_URL', None)
            grants, pids, current_pids, errors = [], [], [], []
            def run():
                try:
                    supervisor._run()
                except BaseException as exc:
                    errors.append(repr(exc))
            def display():
                supervisor.vd = subprocess.Popen(['sleep', '300'])
            with mock.patch.multiple(hca, PROFILE=str(root / 'profile'), STATE_DIR=str(root / 'state'),
                    CHROME_PORT=upstream, PORT=port, CHROME=str(wrapper), FOCUS_GUARD=False), \
                 mock.patch.object(hca.signal, 'signal'), \
                 mock.patch.object(hca, 'notifier_status', return_value='authorized'), \
                 mock.patch.object(supervisor, 'start_vdisplay', side_effect=display), \
                 mock.patch.object(supervisor, 'rehome'), mock.patch.object(supervisor, 'evict'), \
                 mock.patch.object(supervisor, 'write_state'):
                thread = threading.Thread(target=run)
                thread.start()
                try:
                    deadline = time.monotonic() + 5
                    while supervisor.proxy is None or supervisor.proxy.srv is None:
                        self.assertLess(time.monotonic(), deadline)
                        self.assertFalse(errors)
                        threading.Event().wait(.05)
                    # Two real independently spawned owned daemons, not supervisor children.
                    for owner in ('one', 'two'):
                        grant = supervisor.lifecycle.control('acquire', dict(installation_id='test',
                            owner=[tmp, owner, 'default'], timeout_s=30, worker_pid=os.getpid(), daemon_dir=tmp),
                            'http://127.0.0.1:%d' % port)
                        grants.append(grant)
                        env.update(BU_NAME=grant['daemon_name'], BU_CDP_URL=grant['endpoint'])
                        result = subprocess.run(['browser-harness'], input="new_tab('about:blank'); print('owned ready')",
                            text=True, env=env, capture_output=True, timeout=35)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        path = str(root / ('bu-' + grant['daemon_name'] + '.sock'))
                        pids.append(exchange(path, {'meta': 'ping'})['pid'])
                        for _ in range(reloads):
                            ledger.sweep_daemons()
                            with ledger.transaction(readonly=True) as data:
                                self.assertEqual(data['leases'][grant['capability']]['daemon']['pid'], pids[-1])
                            result = subprocess.run(['browser-harness', '--reload'], env=env,
                                                    capture_output=True, timeout=20)
                            self.assertEqual(result.returncode, 0, result.stderr)
                            result = subprocess.run(['browser-harness'], input="new_tab('about:blank')",
                                text=True, env=env, capture_output=True, timeout=35)
                            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                            pids.append(exchange(path, {'meta': 'ping'})['pid'])
                        current_pids.append(pids[-1])
                    stop_started = time.monotonic()
                    supervisor.request_stop()
                    # Drain has outstanding requests: neither daemon may be stopped yet.
                    thread.join(timeout=2)
                    self.assertTrue(thread.is_alive())
                    self.assertTrue(all(fingerprint(pid) for pid in current_pids))
                    if finish_requests:
                        for grant in grants:
                            ledger.complete(grant['capability'], grant['request_id'])
                    thread.join(timeout=45)
                    self.assertFalse(thread.is_alive())
                    self.assertFalse(errors, errors)
                    if not finish_requests:
                        self.assertGreaterEqual(time.monotonic() - stop_started, 30)
                        self.assertLess(time.monotonic() - stop_started, 45)
                    self.assertTrue(all(fingerprint(pid) is None for pid in pids), pids)
                    with ledger.transaction(readonly=True) as data:
                        self.assertFalse(any(lease.get('daemon_socket') for lease in data['leases'].values()))
                    print('STOP VERIFIED owned daemon pids exited', pids, flush=True)
                finally:
                    for grant in grants:
                        ledger.complete(grant['capability'], grant['request_id'])
                    supervisor.request_stop()
                    thread.join(timeout=45)
                    for grant in grants:
                        env.update(BU_NAME=grant['daemon_name'], BU_CDP_URL=grant['endpoint'])
                        subprocess.run(['browser-harness', '--reload'], env=env, capture_output=True, timeout=20)
                    supervisor.idle_managed()
                    if supervisor.proxy:
                        supervisor.proxy.close()
                    self.assertFalse(thread.is_alive())
                    self.assertTrue(all(fingerprint(pid) is None for pid in pids))
