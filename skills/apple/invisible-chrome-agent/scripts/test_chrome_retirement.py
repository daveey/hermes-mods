"""Retirement generations and fairness, using private real Unix peers."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

import chrome_agent_daemons as daemons
from chrome_agent_lifecycle import Lifecycle
from chrome_agent_process import fingerprint
import test_chrome_daemon_processes as peers


class RetirementTests(unittest.TestCase):
    def setUp(self):
        self.fixture = peers.RealDaemonTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ledger = self.fixture.ledger

    def replacement(self, path, target, silent=False, closed=False):
        Path(path).unlink(missing_ok=True)
        command = ([sys.executable, __file__, '--closed' if closed else '--silent', path] if silent or closed else
                   [sys.executable, peers.__file__, '--peer', path, target, '0'])
        process = subprocess.Popen(command)
        def cleanup():
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            self.assertIsNone(fingerprint(process.pid))
        self.addCleanup(cleanup)
        deadline = time.monotonic() + 5
        while not Path(path).exists():
            self.assertIsNone(process.poll())
            self.assertLess(time.monotonic(), deadline)
            threading.Event().wait(.01)
        return process

    def registered(self, grant):
        with self.ledger.transaction(readonly=True) as data:
            return dict(data['leases'][grant['capability']])

    def expired(self, *grants):
        for grant in grants:
            self.ledger.complete(grant['capability'], grant['request_id'])
            self.ledger.release(grant['capability'])
        self.fixture.now += 2

    def test_retired_cache_reconciles_repeated_owned_replacements(self):
        grant, path, process = self.fixture.daemon('owner', 'owner')
        for _ in range(3):
            self.ledger.sweep_daemons()
            self.assertEqual(self.registered(grant)['daemon']['pid'], process.pid)
            daemons.exchange(path, {'meta': 'shutdown'})
            process.wait(timeout=3)
            process = self.replacement(path, 'owner')
        self.expired(grant)
        self.ledger.sweep_daemons()
        process.wait(timeout=3)
        self.assertNotIn('daemon_socket', self.registered(grant))

    def test_retired_stamped_socket_cleans_after_process_exit_between_sweeps(self):
        grant, path, process = self.fixture.daemon('owner', 'owner')
        self.ledger.sweep_daemons()
        daemons.exchange(path, {'meta': 'shutdown'})
        process.wait(timeout=3)
        self.expired(grant)
        self.ledger.sweep_daemons()
        self.assertNotIn('daemon_socket', self.registered(grant))

    def test_legacy_live_identity_upgrades_after_ledger_reload(self):
        grant, path, process = self.fixture.daemon('owner', 'owner')
        self.ledger.sweep_daemons()
        with self.ledger.transaction() as data:
            data['leases'][grant['capability']]['daemon'].pop('socket_stamp', None)
        from chrome_agent_lifecycle import Ledger
        self.ledger = Ledger(self.fixture.root / 'ledger.json', self.ledger.config, clock=lambda: self.fixture.now)
        self.fixture.ledger = self.ledger
        self.ledger.sweep_daemons()
        self.assertEqual(self.registered(grant)['daemon']['pid'], process.pid)
        self.assertIn('socket_stamp', self.registered(grant)['daemon'])
        self.expired(grant)
        self.ledger.sweep_daemons()
        process.wait(timeout=3)
        self.assertNotIn('daemon_socket', self.registered(grant))

    def test_foreign_replacement_is_retained_and_never_shutdown(self):
        grant, path, old = self.fixture.daemon('owner', 'owner')
        self.ledger.sweep_daemons()
        daemons.exchange(path, {'meta': 'shutdown'})
        old.wait(timeout=3)
        foreign = self.replacement(path, 'FOREIGN')
        remaining = Lifecycle(self.ledger, 'test', lambda: None).retire_daemons(timeout=.4)
        self.assertEqual(remaining, 1)
        self.assertIn('daemon_socket', self.registered(grant))
        self.assertIsNone(foreign.poll())
        self.assertEqual(daemons.exchange(path, {'meta': 'current_tab'})['targetId'], 'FOREIGN')

    def test_unverified_refused_replacement_is_not_proof_of_process_exit(self):
        grant, path, old = self.fixture.daemon('owner', 'owner')
        self.ledger.sweep_daemons()
        daemons.exchange(path, {'meta': 'shutdown'})
        old.wait(timeout=3)
        foreign = self.replacement(path, 'FOREIGN', closed=True)
        remaining = Lifecycle(self.ledger, 'test', lambda: None).retire_daemons(timeout=.4)
        self.assertEqual(remaining, 1)
        self.assertIn('daemon_socket', self.registered(grant))
        self.assertIsNone(foreign.poll())

    def test_live_old_identity_survives_replaced_then_missing_endpoint(self):
        grant, path, old = self.fixture.daemon('owner', 'owner', ignore=True)
        self.ledger.sweep_daemons()
        foreign = self.replacement(path, 'FOREIGN')
        life = Lifecycle(self.ledger, 'test', lambda: None)
        self.assertEqual(life.retire_daemons(timeout=.2), 1)
        self.assertEqual(self.registered(grant)['daemon']['pid'], old.pid)
        foreign.terminate()
        foreign.wait(timeout=3)
        Path(path).unlink()
        self.assertEqual(life.retire_daemons(timeout=.2), 1)
        self.assertEqual(self.registered(grant)['daemon']['pid'], old.pid)
        self.assertIsNone(old.poll())
        old.terminate()
        old.wait(timeout=3)
        self.assertEqual(life.retire_daemons(timeout=.4), 0)

    def test_replacement_during_shutdown_is_not_erased(self):
        grant, path, old = self.fixture.daemon('owner', 'owner')
        self.ledger.sweep_daemons()
        original = daemons.shutdown
        replacements = []
        def replace_after_exit(identity, deadline=None):
            result = original(identity, deadline)
            if result and not replacements:
                old.wait(timeout=3)
                replacements.append(self.replacement(path, 'FOREIGN'))
            return result
        with mock.patch.object(daemons, 'shutdown', side_effect=replace_after_exit):
            remaining = Lifecycle(self.ledger, 'test', lambda: None).retire_daemons(timeout=1)
        self.assertEqual(remaining, 1)
        self.assertIn('daemon_socket', self.registered(grant))
        self.assertIsNone(replacements[0].poll())

    def test_endpoint_replaced_between_ping_and_shutdown_is_not_commanded(self):
        grant, path, old = self.fixture.daemon('owner', 'owner')
        self.ledger.sweep_daemons()
        original = daemons.exchange
        replacements = []
        def racing_exchange(path_arg, payload, deadline=None, **kwargs):
            if payload.get('meta') == 'shutdown' and not replacements:
                replacements.append(self.replacement(path, 'FOREIGN'))
            return original(path_arg, payload, deadline, **kwargs)
        with mock.patch.object(daemons, 'exchange', side_effect=racing_exchange):
            remaining = Lifecycle(self.ledger, 'test', lambda: None).retire_daemons(timeout=.4)
        self.assertEqual(remaining, 1)
        self.assertIsNone(old.poll())
        self.assertIsNone(replacements[0].poll())
        self.assertEqual(self.registered(grant)['daemon']['pid'], old.pid)
        self.assertEqual(daemons.exchange(path, {'meta': 'current_tab'})['targetId'], 'FOREIGN')

    def test_endpoint_replaced_after_vacancy_check_is_not_forgotten(self):
        grant, path, old = self.fixture.daemon('owner', 'owner')
        self.ledger.sweep_daemons()
        original = daemons._vacant
        replacements = []
        def racing_vacancy(path_arg, deadline, identity=None):
            vacant = original(path_arg, deadline, identity)
            if vacant and not replacements:
                old.wait(timeout=3)
                replacements.append(self.replacement(path, 'FOREIGN'))
            return vacant
        with mock.patch.object(daemons, '_vacant', side_effect=racing_vacancy):
            remaining = Lifecycle(self.ledger, 'test', lambda: None).retire_daemons(timeout=.5)
        self.assertEqual(remaining, 1)
        self.assertIn('daemon_socket', self.registered(grant))
        self.assertIsNone(replacements[0].poll())

    def test_explicit_stop_reaches_healthy_after_ack_and_silent_peers(self):
        first, _, stubborn = self.fixture.daemon('stubborn', 'stubborn', ignore=True)
        silent, path, old = self.fixture.daemon('silent', 'silent')
        daemons.exchange(path, {'meta': 'shutdown'})
        old.wait(timeout=3)
        silent_process = self.replacement(path, 'silent', silent=True)
        healthy, _, process = self.fixture.daemon('healthy', 'healthy')
        self.expired(first, silent, healthy)
        # Put both blocked peers before healthy in the rotating capability order.
        self.ledger._daemon_cursor = healthy['capability']
        start = time.monotonic()
        remaining = Lifecycle(self.ledger, 'test', lambda: self.fail('wake')).retire_daemons(timeout=2)
        self.assertLess(time.monotonic() - start, 3)
        self.assertIsNotNone(process.poll(), 'healthy peer starved behind blocked peers')
        self.assertEqual(remaining, 2)
        self.assertIsNone(stubborn.poll())
        self.assertIsNone(silent_process.poll())

    def test_repeated_short_sweeps_progress_past_more_owners_than_budget(self):
        blocked = []
        for index in range(5):
            grant, path, old = self.fixture.daemon('silent%d' % index, 'silent%d' % index)
            daemons.exchange(path, {'meta': 'shutdown'})
            old.wait(timeout=3)
            blocked.append((grant, self.replacement(path, 'unused', silent=True)))
        healthy = [self.fixture.daemon('healthy%d' % i, 'healthy%d' % i) for i in range(4)]
        active, active_path, active_process = self.fixture.daemon('active', 'active')
        self.expired(*(g for g, _ in blocked), *(g for g, _, _ in healthy))
        for _ in range(25):
            self.ledger.sweep_daemons(deadline=time.monotonic() + .15)
            if all(p.poll() is not None for _, _, p in healthy):
                break
        self.assertTrue(all(p.poll() is not None for _, _, p in healthy), 'rotation lost later owners')
        self.assertTrue(all(p.poll() is None for _, p in blocked))
        self.assertIsNone(active_process.poll())
        self.assertTrue(daemons.exchange(active_path, {'meta': 'ping'})['pong'])
        self.assertIn('daemon_socket', self.registered(active))

    def test_idle_default_sweeps_do_not_wake_or_starve_healthy(self):
        first, _, stubborn = self.fixture.daemon('stubborn', 'stubborn', ignore=True)
        healthy, _, process = self.fixture.daemon('healthy', 'healthy')
        active, path, sibling = self.fixture.daemon('active', 'active')
        self.expired(first, healthy)
        self.ledger._daemon_cursor = healthy['capability']
        supervisor = peers.hca.Supervisor()
        supervisor.lifecycle = Lifecycle(self.ledger, 'test', lambda: self.fail('idle woke Chrome'))
        for _ in range(2):
            supervisor.managed_step()
            supervisor.lifecycle.daemon_sweep.join(6)
            self.assertFalse(supervisor.lifecycle.daemon_sweep.is_alive())
        self.assertIsNotNone(process.poll())
        self.assertIsNone(stubborn.poll())
        self.assertIsNone(sibling.poll())
        self.assertTrue(daemons.exchange(path, {'meta': 'ping'})['pong'])
        self.assertFalse(supervisor.lifecycle.running)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] in ('--silent', '--closed'):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(sys.argv[2])
            os.chmod(sys.argv[2], 0o600)
            listener.listen(128)
            if sys.argv[1] == '--closed':
                listener.close()
            threading.Event().wait(120)
    else:
        unittest.main()
