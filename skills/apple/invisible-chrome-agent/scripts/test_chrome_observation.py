"""Pure-memory unknown/death and HTTP routing regressions; no OS operations."""
import contextlib
import importlib.machinery
import importlib.util
from pathlib import Path
import types
import unittest
from unittest import mock
import chrome_agent_process as processes
import chrome_agent_lifecycle as lifecycle

_loader = importlib.machinery.SourceFileLoader('observation_helper', str(Path(__file__).with_name('hermes-chrome-agent')))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
helper = importlib.util.module_from_spec(_spec)
_loader.exec_module(helper)


class Observation(unittest.TestCase):
    def ledger(self):
        data = {'leases': {'cap': {'workers': {'424242': 'fixture:live'}, 'activity': 1000, 'released': None}}}
        @contextlib.contextmanager
        def transaction(**kwargs):
            yield data
        return types.SimpleNamespace(transaction=transaction, clock=lambda: 1000,
                                     config=types.SimpleNamespace(orphan_s=30)), data['leases']['cap']

    def test_unknown_worker_keeps_custody_and_deadline(self):
        ledger, lease = self.ledger()
        with mock.patch.object(processes, 'fingerprint', return_value=None), \
                mock.patch.object(processes, 'is_zombie', return_value=False), \
                mock.patch.object(processes.os, 'kill', side_effect=PermissionError):
            lifecycle.Ledger.reap_workers(ledger)
        self.assertEqual(lease['workers'], {'424242': 'fixture:live'})
        self.assertNotIn('restart_until', lease)

    def test_live_worker_keeps_custody(self):
        ledger, lease = self.ledger()
        with mock.patch.object(processes, 'fingerprint', return_value='fixture:live'):
            lifecycle.Ledger.reap_workers(ledger)
        self.assertEqual(lease['workers'], {'424242': 'fixture:live'})
        self.assertNotIn('restart_until', lease)

    def test_reused_worker_retires_original_generation(self):
        ledger, lease = self.ledger()
        with mock.patch.object(processes, 'fingerprint', return_value='fixture:replacement'):
            lifecycle.Ledger.reap_workers(ledger)
        self.assertEqual(lease['workers'], {})
        self.assertEqual(lease['restart_until'], 1030)

    def test_positive_absence_retires_worker(self):
        ledger, lease = self.ledger()
        with mock.patch.object(processes, 'fingerprint', return_value=None), \
                mock.patch.object(processes, 'is_zombie', return_value=False), \
                mock.patch.object(processes.os, 'kill', side_effect=ProcessLookupError):
            lifecycle.Ledger.reap_workers(ledger)
        self.assertEqual(lease['workers'], {})

    def test_recovered_unknown_then_healthy_does_not_latch_exit(self):
        process = processes.RecoveredProcess({'pid': 424242, 'started': 'fixture:live'})
        with mock.patch.object(processes, 'fingerprint', side_effect=[None, 'fixture:live']), \
                mock.patch.object(processes, 'is_zombie', return_value=False), \
                mock.patch.object(processes.os, 'kill', side_effect=PermissionError):
            self.assertEqual([process.poll(), process.poll()], [None, None])

    def test_recovered_unknown_never_authorizes_signal(self):
        for method in ('terminate', 'kill'):
            with self.subTest(method=method):
                process = processes.RecoveredProcess({'pid': 424242, 'started': 'fixture:live'})
                with mock.patch.object(processes, 'fingerprint', return_value=None), \
                        mock.patch.object(processes, 'is_zombie', return_value=False), \
                        mock.patch.object(processes.os, 'kill') as kill:
                    getattr(process, method)()
                self.assertFalse([call for call in kill.call_args_list if call.args[1] != 0])
                self.assertIsNone(process.returncode)

    def test_recovered_positive_death_and_reuse_latch_exit(self):
        for current, zombie, error in [(None, True, None), (None, False, ProcessLookupError), ('fixture:replacement', False, None)]:
            with self.subTest(current=current, zombie=zombie):
                process = processes.RecoveredProcess({'pid': 424242, 'started': 'fixture:live'})
                with mock.patch.object(processes, 'fingerprint', return_value=current), \
                        mock.patch.object(processes, 'is_zombie', return_value=zombie), \
                        mock.patch.object(processes.os, 'kill', side_effect=error):
                    self.assertEqual(process.poll(), 0)

    def test_recovered_changed_generation_before_signal_is_not_signalled(self):
        process = processes.RecoveredProcess({'pid': 424242, 'started': 'fixture:live'})
        with mock.patch.object(processes, 'fingerprint', side_effect=['fixture:live', 'fixture:replacement']), \
                mock.patch.object(processes.os, 'kill') as kill:
            process.terminate()
        kill.assert_not_called()

    def test_unknown_wait_times_out_without_claiming_exit(self):
        process = processes.RecoveredProcess({'pid': 424242, 'started': 'fixture:live'})
        with mock.patch.object(processes, 'fingerprint', return_value=None), \
                mock.patch.object(processes, 'is_zombie', return_value=False), \
                mock.patch.object(processes.os, 'kill', side_effect=PermissionError), \
                mock.patch.object(processes.time, 'monotonic', side_effect=[1000, 1002]):
            with self.assertRaises(processes.subprocess.TimeoutExpired):
                process.wait(timeout=1)
        self.assertIsNone(process.returncode)

    def test_recovered_owned_signal_still_works(self):
        process = processes.RecoveredProcess({'pid': 424242, 'started': 'fixture:live'})
        with mock.patch.object(processes, 'fingerprint', return_value='fixture:live'), \
                mock.patch.object(processes.os, 'kill') as kill:
            process.terminate()
        kill.assert_called_once_with(424242, processes.signal.SIGTERM)


class HttpNew(unittest.TestCase):
    def test_chromium_new_command_variants_are_not_forwarded(self):
        import http.client
        for target in ('/json/new?about:blank', '/json/new#x?about:blank', '/json/new/anything?about:blank', '/json/new/?about:blank'):
            with self.subTest(target=target):
                proxy = helper.CDPProxy(0, 1, show=lambda: False, logf=lambda value: None)
                proxy.peers[123] = 'fixture'
                with mock.patch.object(proxy, '_new_background', return_value={'id': 'safe'}) as new, \
                        mock.patch.object(http.client, 'HTTPConnection') as connection:
                    proxy._http(mock.Mock(), 'PUT', target, [], b'', ('fixture', 123))
                connection.assert_not_called()
                new.assert_called_once()

    def test_noncommands_shown_and_nonput_preserved(self):
        for method, target, show in [('PUT', '/json/newspaper?x', False), ('PUT', '/json/new?x', True), ('GET', '/json/new?x', False)]:
            with self.subTest(method=method, target=target, show=show):
                self.assertIsNone(helper.filter_http(method, target, show))
