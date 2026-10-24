"""Installer safety regressions; all paths and launchctl calls are isolated."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_hermes_chrome_agent import hca
from chrome_agent_lifecycle import LifecycleError


class InstallSafetyTests(unittest.TestCase):
    def install(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Library/LaunchAgents').mkdir(parents=True)
            with mock.patch.multiple(hca, HOME=tmp, TOOLS=str(root / 'tools'),
                                     STATE_DIR=str(root / 'state'), LOG=str(root / 'logs/log')), \
                 mock.patch.object(hca.subprocess, 'check_call') as check, \
                 mock.patch.object(hca.subprocess, 'call') as call, \
                 mock.patch.object(hca, 'build_notifier', return_value='test'), \
                 mock.patch.object(hca, 'notifier_status', return_value='authorized'):
                hca.cmd_install(**kwargs)
                return (root / 'state/lifecycle-config.json').exists(), check.call_args_list, call.call_args_list

    def test_legacy_install_does_not_enable_managed(self):
        exists, _, _ = self.install(no_start=True)
        self.assertFalse(exists)

    def test_managed_install_is_explicit_and_no_start_disables(self):
        exists, checks, _ = self.install(no_start=True, managed=True)
        self.assertTrue(exists)
        self.assertTrue(any(c.args[0][:2] == ['launchctl', 'disable'] for c in checks))
        self.assertFalse(any(c.args[0][:2] == ['launchctl', 'enable'] for c in checks))

    def test_stop_disables_before_bootout(self):
        with mock.patch.object(hca.subprocess, 'check_call') as check:
            hca.cmd_stop()
        self.assertEqual([c.args[0][1] for c in check.call_args_list], ['disable', 'bootout'])

    def test_bad_managed_configuration_exits_cleanly_without_start(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hca, 'STATE_DIR', tmp):
            supervisor = hca.Supervisor()
            with mock.patch.object(supervisor, 'configure_lifecycle', side_effect=LifecycleError('bad config')), \
                 mock.patch.object(supervisor, '_run') as run:
                self.assertEqual(supervisor.run(), 0)
                run.assert_not_called()

    def test_main_frame_download_falls_back_to_target_identity(self):
        supervisor = hca.Supervisor()
        supervisor.lifecycle = mock.Mock()
        supervisor.on_event({'method': 'Browser.downloadWillBegin',
                             'params': {'guid': 'download', 'frameId': 'page'}})
        supervisor.lifecycle.ledger.download.assert_called_once_with(
            'downloadWillBegin', {'guid': 'download', 'frameId': 'page'}, 'page')

    def test_idle_clears_observation_state(self):
        supervisor = hca.Supervisor()
        supervisor.frame_target['frame'] = 'page'
        supervisor.passkey = {'target': 'page'}
        with mock.patch.object(supervisor, 'stop_children'):
            supervisor.idle_managed()
        self.assertEqual(supervisor.frame_target, {})
        self.assertIsNone(supervisor.passkey)

    def test_frame_and_session_detach_prune_observation_maps(self):
        supervisor = hca.Supervisor()
        supervisor.lifecycle = mock.Mock()
        supervisor.frame_target['frame'] = 'page'
        supervisor.sess_target['session'] = 'page'
        supervisor.on_event(dict(method='Page.frameDetached', params=dict(frameId='frame')))
        supervisor.on_event(dict(method='Target.detachedFromTarget', params=dict(sessionId='session')))
        self.assertEqual(supervisor.frame_target, {})
        self.assertEqual(supervisor.sess_target, {})

    def test_reused_singleton_pid_does_not_prevent_wake(self):
        supervisor = hca.Supervisor()
        supervisor.lifecycle = mock.Mock()
        with mock.patch.object(hca.os, 'readlink', return_value='host-123'), \
             mock.patch.object(hca, 'pid_alive', return_value=True), \
             mock.patch.object(hca.subprocess, 'check_output', return_value='unrelated --work'), \
             mock.patch.object(supervisor, 'start_vdisplay') as vd, \
             mock.patch.object(supervisor, 'start_chrome'), \
             mock.patch.object(supervisor, 'remember_browser'), \
             mock.patch.object(hca, 'cdp_version', return_value={'Browser': 'test'}):
            supervisor.wake_managed()
            vd.assert_called_once()

    def test_recovery_requires_exact_browser_instance(self):
        from chrome_agent_lifecycle import Ledger
        from chrome_agent_process import record
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            try:
                ledger = Ledger(Path(tmp) / 'ledger.json')
                with ledger.transaction() as data:
                    data['browser'] = dict(record(child), websocket='ws://127.0.0.1:19002/devtools/browser/known',
                                           profile=tmp)
                supervisor = hca.Supervisor()
                supervisor.lifecycle = mock.Mock(ledger=ledger)
                with mock.patch.object(hca, 'PROFILE', tmp), \
                     mock.patch.object(hca, 'cdp_version', return_value={'webSocketDebuggerUrl': 'different'}), \
                     mock.patch.object(supervisor, 'start_vdisplay') as display:
                    self.assertFalse(supervisor.recover_managed())
                    display.assert_not_called()
                with mock.patch.object(hca, 'PROFILE', tmp), \
                     mock.patch.object(hca, 'cdp_version', return_value={'webSocketDebuggerUrl': 'ws://127.0.0.1:19002/devtools/browser/known'}), \
                     mock.patch.object(supervisor, 'start_vdisplay') as display:
                    # A matching URL plus an unrelated sleep PID is not ownership.
                    # Real Chrome recovery success is exercised by the process suite.
                    self.assertFalse(supervisor.recover_managed())
                    display.assert_not_called()
            finally:
                child.terminate()
                child.wait(timeout=5)

    def test_stop_during_wake_cannot_launch_chrome(self):
        supervisor = hca.Supervisor()
        supervisor.lifecycle = mock.Mock()
        def stopping():
            supervisor.stop = True
        with mock.patch.object(hca.os, 'readlink', side_effect=FileNotFoundError), \
             mock.patch.object(supervisor, 'start_vdisplay', side_effect=stopping), \
             mock.patch.object(supervisor, 'start_chrome') as chrome, \
             mock.patch.object(supervisor, 'idle_managed'), \
             mock.patch.object(supervisor, 'remember_browser'), \
             mock.patch.object(hca, 'cdp_version', return_value={'Browser': 'test'}):
            with self.assertRaises(LifecycleError):
                supervisor.wake_managed()
            chrome.assert_not_called()

    def test_crash_restart_is_backed_off_and_stable_runtime_resets_it(self):
        import threading
        supervisor = hca.Supervisor()
        supervisor.lifecycle = mock.Mock(transition=threading.RLock(), lock=threading.RLock())
        supervisor.vd = mock.Mock()
        supervisor.vd.poll.return_value = None
        supervisor.chrome = mock.Mock()
        supervisor.chrome.poll.return_value = 1
        supervisor.chrome_started = 100
        with mock.patch.object(supervisor, 'start_chrome') as start, \
             mock.patch.object(supervisor, 'remember_browser'), \
             mock.patch.object(supervisor, 'connect', return_value=False), \
             mock.patch.object(supervisor, 'evict'), \
             mock.patch.object(hca.time, 'monotonic', return_value=100) as clock:
            supervisor.managed_step()
            clock.return_value = 101
            supervisor.managed_step()
            self.assertEqual(start.call_count, 1)
            clock.return_value = 103
            supervisor.managed_step()
            self.assertEqual(start.call_count, 2)
            self.assertEqual(supervisor.backoff_until, 107)
            clock.return_value = 200
            supervisor.managed_step()
            self.assertEqual(supervisor.chrome_fails, 1)


if __name__ == '__main__':
    unittest.main()
