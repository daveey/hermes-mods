"""OS evidence, not failed probes, establishes daemon retirement."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import chrome_agent_daemons as daemons
import chrome_agent_process as processes
from chrome_agent_lifecycle import Ledger, Lifecycle, LifecycleError


class NativeRetirementTests(unittest.TestCase):
    identity = dict(pid=12345, started='ps:Sat Oct 24 03:05:32 2026')

    def probe(self, output=None, error=None):
        # Exercise the ps fallback on any host without pretending to change OS.
        with mock.patch.object(daemons, '_fingerprint', return_value=None), \
             mock.patch.object(Path, 'exists', return_value=False), \
             mock.patch.object(processes.subprocess, 'check_output', return_value=output, side_effect=error) as ps, \
             mock.patch.object(daemons.os, 'kill'):
            result = daemons._retired(self.identity, time.monotonic() + .3)
        return result, ps

    def test_explicit_ps_zombie_is_retired(self):
        for state in ('Z', 'ZN', 'Z+', 'ZNs'):
            with self.subTest(state=state):
                result, ps = self.probe('12345 ' + state + '\n')
                self.assertTrue(result)
                self.assertEqual(ps.call_args.args[0], ['ps', '-p', '12345', '-o', 'pid=', '-o', 'stat='])
                self.assertGreater(ps.call_args.kwargs['timeout'], 0)
                self.assertLessEqual(ps.call_args.kwargs['timeout'], .3)

    def test_unavailable_timeout_permission_and_garbled_are_not_exit(self):
        for error in (FileNotFoundError(), PermissionError(), subprocess.TimeoutExpired('ps', .1),
                      subprocess.CalledProcessError(1, 'ps')):
            with self.subTest(error=type(error).__name__):
                self.assertFalse(self.probe(error=error)[0])
        for output in ('', 'garbled', 'Z', '12345 Zombie', '12345 Z garbage', '999 Z',
                       '12345 Z\n999 Z', '12345 ?', '12345 S', '12345 SN', '12345 R+', '12345 T'):
            with self.subTest(output=output):
                self.assertFalse(self.probe(output)[0])

    def test_generation_checks_precede_zombie_fallback(self):
        for current, expected in ((self.identity['started'], False), ('ps:different-generation', True)):
            with self.subTest(current=current), \
                 mock.patch.object(daemons, '_fingerprint', return_value=current), \
                 mock.patch.object(processes.subprocess, 'check_output') as ps:
                self.assertEqual(daemons._retired(self.identity, time.monotonic() + 1), expected)
                ps.assert_not_called()

    def test_exhausted_budget_does_not_launch_another_probe(self):
        with mock.patch.object(daemons, '_fingerprint', return_value=None), \
             mock.patch.object(Path, 'exists', return_value=False), \
             mock.patch.object(processes.subprocess, 'check_output') as ps:
            with self.assertRaises(TimeoutError):
                daemons._retired(self.identity, time.monotonic() - 1)
            ps.assert_not_called()

    def test_os_missing_pid_is_exit_but_permission_is_not(self):
        for error, expected in ((ProcessLookupError(), True), (PermissionError(), False)):
            with self.subTest(error=type(error).__name__), \
                 mock.patch.object(daemons, '_fingerprint', return_value=None), \
                 mock.patch.object(Path, 'exists', return_value=False), \
                 mock.patch.object(processes.subprocess, 'check_output', side_effect=FileNotFoundError()), \
                 mock.patch.object(daemons.os, 'kill', side_effect=error):
                self.assertEqual(daemons._retired(self.identity, time.monotonic() + 1), expected)

    def test_real_unreaped_child_is_retired(self):
        child = subprocess.Popen(['sleep', '60'])
        self.addCleanup(child.wait, timeout=5)
        self.addCleanup(child.terminate)
        identity = dict(pid=child.pid, started=processes.fingerprint(child.pid))
        self.assertIsNotNone(identity['started'])
        child.terminate()
        deadline = time.monotonic() + 3
        while True:
            state = subprocess.check_output(['ps', '-p', str(child.pid), '-o', 'stat='], text=True).strip()
            if state.startswith('Z'):
                break
            self.assertLess(time.monotonic(), deadline)
            threading.Event().wait(.01)
        # No poll()/wait() before this assertion: kill(pid, 0) still succeeds.
        os.kill(child.pid, 0)
        self.assertIsNone(processes.fingerprint(child.pid))
        self.assertTrue(daemons._retired(identity, deadline))
        # Also exercise real ps output on Linux; no fake platform and no reaping.
        with mock.patch.object(Path, 'exists', return_value=False):
            self.assertTrue(processes.is_zombie(child.pid, timeout=1))
        self.assertEqual(child.wait(timeout=3), -15)


class SocketPathTests(unittest.TestCase):
    def test_chrome_fixture_isolates_launcher_and_crash_cache_writes(self):
        from test_chrome_endpoint_ownership import EndpointOwnershipTests
        fixture = EndpointOwnershipTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for variable in ('HOME', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'CHROME_CONFIG_HOME'):
            self.assertTrue(Path(os.environ.get(variable, '/unisolated')).is_relative_to(fixture.root), variable)

    def test_socket_fixture_avoids_system_temp_without_global_override(self):
        from chrome_test_fixtures import socket_directory
        original_tempdir = tempfile.tempdir
        for configured, expected in (('/var/folders/native/T/', '/managed/cache/scratch'),
                                     ('/private/var/folders/native/T', '/managed/cache/scratch'),
                                     ('/tmp', '/managed/cache/scratch'),
                                     ('/selected/scratch', '/selected/scratch')):
            with self.subTest(configured=configured), \
                 mock.patch.dict(os.environ, TMPDIR=configured, HERMES_HOME='/managed'), \
                 mock.patch.object(Path, 'mkdir'), \
                 mock.patch.object(tempfile, 'TemporaryDirectory') as allocate:
                allocate.return_value.name = expected + '/abcdefgh'
                socket_directory()
                allocate.assert_called_once_with(prefix='', dir=Path(expected))
                self.assertEqual(tempfile.tempdir, original_tempdir)
                self.assertEqual(os.environ['TMPDIR'], configured)

    def test_socket_fixture_rejects_long_managed_root_and_cleans_allocation(self):
        from chrome_test_fixtures import socket_directory
        configured = '/managed/' + 'x' * 100
        with mock.patch.dict(os.environ, TMPDIR=configured), \
             mock.patch.object(Path, 'mkdir'), \
             mock.patch.object(tempfile, 'TemporaryDirectory') as allocate:
            allocate.return_value.name = configured + '/abcdefgh'
            with self.assertRaisesRegex(ValueError, 'scratch root too long'):
                socket_directory()
            allocate.return_value.cleanup.assert_called_once()
            allocate.assert_called_once_with(prefix='', dir=Path(configured))

    def test_capability_fixture_binds_in_managed_scratch(self):
        from chrome_test_fixtures import socket_directory
        import socket
        with socket_directory() as directory:
            path = str(Path(directory) / ('bu-lease-' + 'x' * 43 + '.sock'))
            self.assertLessEqual(len(os.fsencode(path)), 103)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(path)

    def test_long_encoded_runtime_path_refuses_before_wake_or_lease_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / 'ledger.json')
            wake = mock.Mock()
            lifecycle = Lifecycle(ledger, 'test', wake)
            with ledger.transaction(readonly=True) as data:
                before = dict(data['leases'])
            for directory in ('/' + 'x' * 200, '/' + 'é' * 60):
                with self.subTest(directory=directory), self.assertRaisesRegex(LifecycleError, 'socket path'):
                    lifecycle.control('acquire', dict(installation_id='test', owner=['p', 't', 's'],
                                      timeout_s=10, daemon_dir=directory), 'http://127.0.0.1:19001')
            wake.assert_not_called()
            with ledger.transaction(readonly=True) as data:
                self.assertEqual(data['leases'], before)

    def test_native_socket_path_boundary(self):
        maximum = 107 if sys.platform == 'linux' else 103
        # token_urlsafe(32) yields 43 chars; bu-lease- + token + .sock is 57 bytes.
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / 'ledger.json')
            directory = '/' + 'x' * (maximum - 59)
            grant = ledger.acquire(['p', 't', 's'], 10, daemon_dir=directory)
            with ledger.transaction(readonly=True) as data:
                self.assertEqual(len(os.fsencode(data['leases'][grant['capability']]['daemon_socket'])), maximum)
            with self.assertRaisesRegex(LifecycleError, 'socket path'):
                ledger.acquire(['p', 't', 's'], 10, daemon_dir=directory + 'x')
