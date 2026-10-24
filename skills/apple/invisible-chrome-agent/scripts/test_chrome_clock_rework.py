"""Dual-clock runtime/persistence regression tests, no machine clock changes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from chrome_agent_lifecycle import Ledger, Config, Lifecycle, LifecycleError


class ClockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.json'
        self.wall = 1000000.0
        self.mono = 100.0
        self.wall_patch = mock.patch('time.time', side_effect=lambda: self.wall)
        self.mono_patch = mock.patch('time.monotonic', side_effect=lambda: self.mono)
        self.wall_patch.start()
        self.mono_patch.start()
        self.addCleanup(self.wall_patch.stop)
        self.addCleanup(self.mono_patch.stop)
        self.config = Config(orphan_s=1, idle_lease_s=2, idle_browser_s=1,
                             handoff_s=20, request_grace_s=1, download_stall_s=10, download_max_s=30)
        self.ledger = Ledger(self.path, self.config)

    def grant(self):
        grant = self.ledger.acquire(['profile', 'task', 'session'], 5)
        self.ledger.bind(grant['capability'], 'page')
        return grant

    def test_rollback_does_not_extend_orphan_or_idle_elapsed_bounds(self):
        grant = self.grant()
        self.ledger.complete(grant['capability'], grant['request_id'])
        self.ledger.release(grant['capability'])
        self.wall -= 86400
        self.mono += 3600
        self.assertEqual(self.ledger.reap(['page', 'unknown'], lambda _: True), ['page'])
        self.assertFalse(self.ledger.can_idle(['unknown']))
        stops = []
        life = Lifecycle(self.ledger, 'test', lambda: None, lambda: stops.append(True))
        life.running = True
        life.tick([], lambda _: True)
        self.wall -= 86400
        self.mono += 2
        life.tick([], lambda _: True)
        self.assertEqual(stops, [True])
        if life.daemon_sweep:
            life.daemon_sweep.join(2)

    def test_forward_jump_preserves_request_handoff_download_and_no_renewal(self):
        grant = self.grant()
        self.ledger.protect('page', 'handoff', episode='human')
        self.ledger.download('downloadWillBegin', {'guid': 'dl'}, 'page')
        self.ledger.release(grant['capability'])
        self.wall += 8640000
        self.mono += 2
        self.assertEqual(self.ledger.reap(['page'], lambda _: True), [])
        with self.ledger.transaction(readonly=True) as data:
            self.assertGreater(data['leases'][grant['capability']]['requests'][grant['request_id']], self.ledger.clock())
            self.assertGreater(data['downloads']['dl']['until'], self.ledger.clock())
            until = data['targets']['page']['protections']['handoff']['until']
        self.ledger.protect('page', 'handoff', episode='human')
        with self.ledger.transaction(readonly=True) as data:
            self.assertEqual(data['targets']['page']['protections']['handoff']['until'], until)
        self.mono += 21
        self.assertEqual(self.ledger.reap(['page'], lambda _: True), ['page'])

    def test_reload_consumes_monotonic_elapsed_despite_both_wall_jump_directions(self):
        grant = self.grant()
        self.ledger.protect('page', 'handoff', episode='human')
        self.ledger.release(grant['capability'])
        self.wall -= 86400
        self.mono += 10
        recovered = Ledger(self.path, self.config)
        recovered.reconcile()
        self.assertEqual(recovered.reap(['page'], lambda _: True), [])
        self.wall += 8640000
        self.mono += 11
        recovered = Ledger(self.path, self.config)
        recovered.reconcile()
        self.assertEqual(recovered.reap(['page'], lambda _: True), ['page'])

    def test_reboot_preserves_remaining_protection_then_persists_new_anchor(self):
        grant = self.grant()
        self.ledger.protect('page', 'handoff', episode='human')
        self.ledger.release(grant['capability'])
        raw = json.loads(self.path.read_text())
        self.assertIn('clock', raw)
        raw['clock']['boot'] = 'a-previous-boot'
        self.path.write_text(json.dumps(raw))
        self.mono = 1
        self.wall += 8640000
        recovered = Ledger(self.path, self.config)
        self.assertEqual(recovered.reap(['page'], lambda _: True), [])
        self.mono += 21
        reloaded = Ledger(self.path, self.config)
        self.assertEqual(reloaded.reap(['page'], lambda _: True), ['page'])

    def test_legacy_unknown_clock_migration_is_conservative_but_bounded(self):
        grant = self.grant()
        self.ledger.protect('page', 'handoff', episode='human')
        self.ledger.complete(grant['capability'], grant['request_id'])
        self.ledger.release(grant['capability'])
        raw = json.loads(self.path.read_text())
        raw.pop('clock', None)
        self.path.write_text(json.dumps(raw))
        self.wall += 8640000
        recovered = Ledger(self.path, self.config)
        self.assertEqual(recovered.reap(['page'], lambda _: True), [])
        self.mono += 21
        recovered = Ledger(self.path, self.config)
        self.assertEqual(recovered.reap(['page'], lambda _: True), ['page'])

    def test_quiet_capability_expires_without_observer_renewal(self):
        grant = self.grant()
        self.ledger.complete(grant['capability'], grant['request_id'])
        for _ in range(4):
            self.wall -= 86400
            self.mono += 1
            self.ledger.owns(grant['capability'], 'page')
        again = self.ledger.acquire(['profile', 'task', 'session'], 5)
        self.assertNotEqual(grant['capability'], again['capability'])

    def test_unavailable_boot_identity_refuses_before_state_mutation(self):
        with mock.patch('chrome_agent_clock.boot_identity', side_effect=OSError('unavailable')):
            with self.assertRaises(LifecycleError):
                Ledger(self.path, self.config)
        self.assertFalse(self.path.exists())

    def test_corrupt_clock_anchor_is_not_overwritten(self):
        self.grant()
        raw = json.loads(self.path.read_text())
        raw['clock']['logical'] = 'not-a-clock'
        self.path.write_text(json.dumps(raw))
        original = self.path.read_bytes()
        with self.assertRaises(LifecycleError):
            Ledger(self.path, self.config).valid('unknown')
        self.assertEqual(self.path.read_bytes(), original)
