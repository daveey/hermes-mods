"""Actual process/listener ownership; disposable Linux Chrome only."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request
import urllib.error
from unittest import mock

from test_hermes_chrome_agent import hca
from chrome_agent_lifecycle import Ledger, Lifecycle, LifecycleError
from chrome_agent_process import fingerprint
from chrome_test_fixtures import (
    CHROME_READY_S, chrome_environment, cleanup_disposable_tree, reap_profile_processes,
    wait_until_ready)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    assert port not in (9333, 9334)
    return port


def _log_tail(handle, limit=800):
    handle.flush()
    try:
        text = Path(handle.name).read_text(errors='replace')
    except OSError as exc:
        return 'log unreadable: %s' % exc
    return text[-limit:]


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('google-chrome'), 'isolated Linux Chrome required')
class EndpointOwnershipTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='astra-endpoint-')
        self.addCleanup(lambda: cleanup_disposable_tree(tmp))
        self.root = Path(tmp.name)
        environment = mock.patch.dict(os.environ, chrome_environment(self.root))
        environment.start()
        self.addCleanup(environment.stop)
        self.port = free_port()
        self.log = (self.root / 'chrome.log').open('w')
        self.addCleanup(self.log.close)
        self.wrapper = self.root / 'chrome'
        self.wrapper.write_text('#!/bin/sh\nexec /usr/bin/google-chrome --headless=new "$@"\n')
        self.wrapper.chmod(0o700)
        self.supervisor = hca.Supervisor()
        self.ledger = Ledger(self.root / 'ledger.json')
        self.supervisor.lifecycle = Lifecycle(self.ledger, 'test', self.supervisor.wake_managed)
        patch = mock.patch.multiple(hca, PROFILE=str(self.root / 'managed'), CHROME_PORT=self.port,
                                    CHROME=str(self.wrapper))
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.supervisor.idle_managed)

    def foreign(self):
        proc = subprocess.Popen(['/usr/bin/google-chrome', '--headless=new', '--no-startup-window',
            '--no-first-run', '--no-default-browser-check', '--user-data-dir=' + str(self.root / 'foreign'),
            '--remote-debugging-port=' + str(self.port)], stdout=self.log, stderr=self.log)
        def cleanup():
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            reap_profile_processes(self.root / 'foreign')
            self.assertIsNone(fingerprint(proc.pid))
        self.addCleanup(cleanup)
        wait_until_ready(lambda: hca.cdp_version(port=self.port), proc, CHROME_READY_S,
                         lambda: _log_tail(self.log))
        return proc

    def test_real_foreign_chrome_refused_before_launch_or_commands(self):
        foreign = self.foreign()
        url = hca.cdp_version()['webSocketDebuggerUrl']
        client = hca.CDP(url)
        self.addCleanup(client.close)
        target = client.call('Target.createTarget', {'url': 'about:blank'})['targetId']
        with mock.patch.object(self.supervisor, 'start_vdisplay') as display:
            with self.assertRaises(LifecycleError):
                self.supervisor.wake_managed()
            display.assert_not_called()
        self.assertIsNone(self.supervisor.chrome)
        self.assertIsNone(foreign.poll())
        self.assertEqual(hca.cdp_version()['webSocketDebuggerUrl'], url)
        self.assertIn(target, [t['targetId'] for t in client.call('Target.getTargets')['targetInfos']])
        with self.ledger.transaction(readonly=True) as data:
            self.assertNotIn('browser', data)

    def test_non_cdp_occupied_listener_is_refused(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', self.port))
            listener.listen()
            with mock.patch.object(self.supervisor, 'start_vdisplay') as display:
                with self.assertRaises(LifecycleError):
                    self.supervisor.wake_managed()
                display.assert_not_called()
            self.assertIsNone(self.supervisor.chrome)

    def test_foreign_wins_launch_race_never_remembered_or_connected(self):
        real_popen = subprocess.Popen
        foreign = []
        def racing_launch(args, **kwargs):
            # Port was free at preflight. A real foreign Chrome binds at Popen.
            with mock.patch.object(hca.subprocess, 'Popen', real_popen):
                foreign.append(self.foreign())
                return real_popen(args, **kwargs)
        with mock.patch.object(self.supervisor, 'start_vdisplay'), \
             mock.patch.object(hca.subprocess, 'Popen', side_effect=racing_launch):
            with self.assertRaises(LifecycleError):
                self.supervisor.wake_managed()
        self.assertIsNone(self.supervisor.chrome)
        self.assertIsNone(foreign[0].poll())
        with self.ledger.transaction(readonly=True) as data:
            self.assertNotIn('browser', data)

    def test_real_owned_child_and_exact_record_recovery(self):
        with mock.patch.object(self.supervisor, 'start_vdisplay'):
            self.supervisor.wake_managed()
            self.assertIsNotNone(self.supervisor.connect())
            pid = self.supervisor.chrome.pid
            with self.ledger.transaction(readonly=True) as data:
                url = data['browser']['websocket']
            self.supervisor.cdp.close()
            self.supervisor.cdp = None
            recovered = hca.Supervisor()
            recovered.lifecycle = Lifecycle(Ledger(self.ledger.path), 'test', recovered.wake_managed)
            with mock.patch.object(recovered, 'start_vdisplay'):
                self.assertTrue(recovered.recover_managed())
            self.assertEqual(recovered.chrome.pid, pid)
            self.assertEqual(hca.cdp_version()['webSocketDebuggerUrl'], url)
            self.assertIsNotNone(recovered.connect())
            recovered.cdp.close()

    def test_pid_socket_mismatch_recovery_and_connect_refuse(self):
        foreign = self.foreign()
        unrelated = subprocess.Popen(['sleep', '60'])
        self.addCleanup(lambda: unrelated.wait(timeout=5))
        self.addCleanup(unrelated.terminate)
        from chrome_agent_process import record
        with self.ledger.transaction() as data:
            data['browser'] = dict(record(unrelated), profile=os.path.realpath(hca.PROFILE),
                                   websocket=hca.cdp_version()['webSocketDebuggerUrl'])
        with mock.patch.object(self.supervisor, 'start_vdisplay') as display:
            self.assertFalse(self.supervisor.recover_managed())
            display.assert_not_called()
        self.supervisor.chrome = unrelated
        with self.assertRaises(LifecycleError):
            self.supervisor.connect()
        self.assertIsNone(foreign.poll())
        self.supervisor.chrome = None

    def test_scoped_listener_refuses_replacement_after_owned_browser_exits(self):
        with mock.patch.object(self.supervisor, 'start_vdisplay'):
            self.supervisor.wake_managed()
        grant = self.ledger.acquire(['profile', 'task', 'session'], 30)
        proxy = hca.CDPProxy(0, self.port, lifecycle=self.supervisor.lifecycle,
                             show=lambda: False, logf=lambda _: None).start()
        self.addCleanup(proxy.close)
        self.supervisor.idle_managed()
        foreign = self.foreign()
        base = 'http://127.0.0.1:%d/lease/%s' % (proxy.port, grant['capability'])
        for path, method in [('/json/version', 'GET'), ('/json/list', 'GET'), ('/json/new?about:blank', 'PUT')]:
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(base + path, method=method), timeout=3)
            error.exception.close()
        self.assertIsNone(foreign.poll())
