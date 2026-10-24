"""Lease review regression contracts, isolated persistent state."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from chrome_agent_lifecycle import Ledger, Config


class LedgerReworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.json'
        self.now = 1000
        self.ledger = Ledger(self.path, Config(orphan_s=1), clock=lambda: self.now)
        self.grant = self.ledger.acquire(['/profiles/example', 'task', 'default'], 30)
        self.cap = self.grant['capability']
        self.ledger.bind(self.cap, 'page')

    def test_ownership_events_do_not_parse_or_serialize_ledger(self):
        with mock.patch('chrome_agent_lifecycle.json.load', side_effect=AssertionError('disk parse')), \
             mock.patch('chrome_agent_lifecycle.json.dumps', side_effect=AssertionError('full serialization')):
            for _ in range(20):
                self.assertTrue(self.ledger.owns(self.cap, 'page'))
                self.assertTrue(self.ledger.valid(self.cap))

    def test_oversized_mutation_rolls_back_without_poisoning_valid_state(self):
        from chrome_agent_lifecycle import LifecycleError
        original = self.path.read_bytes()
        with mock.patch('chrome_agent_lifecycle.MAX_LEDGER_BYTES', len(original) + 20, create=True):
            with self.assertRaises(LifecycleError):
                self.ledger.acquire(['/profiles/example', 'x' * 100, 'default'], 30)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertTrue(self.ledger.owns(self.cap, 'page'))
        with self.ledger.transaction(readonly=True) as data:
            self.assertEqual(len(data['leases']), 1)

    def test_safe_historical_leases_are_pruned(self):
        with self.ledger.transaction() as data:
            for i in range(5000):
                data['leases']['old-' + str(i)] = dict(owner=['/profile', str(i), 'default'],
                    activity=0, released=0, requests={})
        self.ledger.acquire(['/profiles/example', 'new-task', 'default'], 30)
        with self.ledger.transaction() as data:
            self.assertLess(len(data['leases']), 10)
        self.assertTrue(self.ledger.owns(self.cap, 'page'))

    def test_download_outlives_closed_page_and_progress_is_throttled(self):
        self.ledger.download('downloadWillBegin', {'guid': 'download'}, 'page')
        self.ledger.complete(self.cap, self.grant['request_id'])
        self.ledger.release(self.cap)
        self.now += 2
        self.ledger.reap([], lambda _: True)
        self.assertFalse(self.ledger.can_idle([]))
        with mock.patch('chrome_agent_lifecycle.atomic_json') as persist:
            for i in range(1, 100):
                self.ledger.download('downloadProgress', {'guid': 'download', 'receivedBytes': i})
            self.assertLessEqual(persist.call_count, 1)
        self.ledger.download('downloadProgress', {'guid': 'download', 'state': 'completed'})
        self.ledger.reap([], lambda _: True)
        self.assertTrue(self.ledger.can_idle([]))

    def test_quiet_task_expires_without_poll_renewal(self):
        self.ledger.complete(self.cap, self.grant['request_id'])
        self.now += 1801
        self.assertFalse(self.ledger.owns(self.cap, 'page'))
        self.assertEqual(self.ledger.reap(['page'], lambda _: True), ['page'])
        next_grant = self.ledger.acquire(['/profiles/example', 'task', 'default'], 30)
        self.assertNotEqual(next_grant['capability'], self.cap)

    def test_unowned_download_events_do_not_write_or_accumulate(self):
        with mock.patch('chrome_agent_lifecycle.atomic_json') as persist:
            for i in range(100):
                self.ledger.download('downloadWillBegin', {'guid': str(i)}, 'unknown')
                self.ledger.download('downloadProgress', {'guid': str(i), 'receivedBytes': i})
            persist.assert_not_called()
        self.assertEqual(self.ledger._progress_saved, {})

    def test_command_io_does_not_hold_lifecycle_lock_and_release_does_not_reap_it(self):
        from concurrent.futures import ThreadPoolExecutor
        from types import SimpleNamespace
        from chrome_agent_lifecycle import Lifecycle, ScopedCDP
        from test_hermes_chrome_agent import hca
        entered, resume = threading.Event(), threading.Event()
        upstream = mock.Mock()

        def call(method, *args, **kwargs):
            if method == 'Runtime.evaluate':
                entered.set()
                self.assertTrue(resume.wait(5))
            return {}

        upstream.call.side_effect = call
        api = SimpleNamespace(CDP=lambda *a, **kw: upstream,
                              cdp_version=lambda **kw: {'webSocketDebuggerUrl': 'fake'},
                              filter_cdp_message=hca.filter_cdp_message)
        lifecycle = Lifecycle(self.ledger, 'test', lambda: None)
        connection = ScopedCDP(lifecycle, SimpleNamespace(upstream=1, show=lambda: False), self.cap, api)
        connection.sessions['session'] = 'page'
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(connection.call, 'Runtime.evaluate', {}, 'session')
            self.assertTrue(entered.wait(2))
            try:
                unlocked = lifecycle.lock.acquire(timeout=2)
                if unlocked:
                    lifecycle.lock.release()
                self.assertTrue(unlocked, 'CDP I/O held lifecycle lock')
                self.ledger.release(self.cap)
                self.now += 2000
                self.assertEqual(self.ledger.reap(['page'], lambda _: True), [])
            finally:
                resume.set()
                pending.result()

    def test_stale_snapshot_cannot_drop_active_new_binding(self):
        self.ledger.reap([], lambda _: self.fail('active target'))
        self.assertTrue(self.ledger.owns(self.cap, 'page'))

    def test_nested_shared_worker_and_browser_detach_events_are_scoped(self):
        from types import SimpleNamespace
        from chrome_agent_lifecycle import Lifecycle, ScopedCDP
        from test_hermes_chrome_agent import hca
        upstream, events = mock.Mock(), []
        api = SimpleNamespace(CDP=lambda *a, **kw: upstream,
            cdp_version=lambda **kw: {'webSocketDebuggerUrl': 'fake'}, filter_cdp_message=hca.filter_cdp_message)
        connection = ScopedCDP(Lifecycle(self.ledger, 'test', lambda: None),
                               SimpleNamespace(upstream=1, show=lambda: False), self.cap, api, events.append)
        connection.sessions['parent'] = 'page'
        child = dict(method='Target.attachedToTarget', sessionId='parent', params=dict(
            sessionId='child', targetInfo=dict(targetId='worker', type='shared_worker'), waitingForDebugger=True))
        connection.event(child)
        self.assertEqual(events, [child])
        connection.call('Runtime.runIfWaitingForDebugger', {}, 'child')
        self.assertEqual(upstream.call.call_args.kwargs['session'], 'child')
        detach = dict(method='Target.detachedFromTarget', params=dict(sessionId='child'))
        connection.event(detach)
        self.assertEqual(events[-1], detach)
        self.assertNotIn('child', connection.sessions)
        connection.event(dict(method='Target.targetDestroyed', params=dict(targetId='stranger')))
        self.assertEqual(len(events), 2)
        connection.close()

    def test_popup_requires_verified_current_opener(self):
        self.assertTrue(self.ledger.popup({'targetId': 'popup', 'openerId': 'page', 'type': 'page'}))
        self.assertTrue(self.ledger.owns(self.cap, 'popup'))
        self.assertFalse(self.ledger.popup({'targetId': 'stranger', 'openerId': 'unknown', 'type': 'page'}))
        self.ledger.new_generation()
        self.assertFalse(self.ledger.popup({'targetId': 'late', 'openerId': 'page', 'type': 'page'}))

    def test_handoff_episodes_not_polling_renew_protection(self):
        self.ledger.protect('page', 'handoff', episode='first')
        self.now += 1201
        self.ledger.protect('page', 'handoff', episode='first')
        with self.ledger.transaction(readonly=True) as data:
            self.assertLess(data['targets']['page']['protections']['handoff']['until'], self.now)
        self.ledger.protect('page', 'handoff', episode='second')
        with self.ledger.transaction(readonly=True) as data:
            self.assertGreater(data['targets']['page']['protections']['handoff']['until'], self.now)

    def test_popup_during_released_handoff_inherits_only_remaining_protection(self):
        self.ledger.protect('page', 'handoff', episode='sign-in')
        self.ledger.complete(self.cap, self.grant['request_id'])
        self.ledger.release(self.cap)
        self.now += 2
        self.assertTrue(self.ledger.popup(dict(targetId='sign-in-popup', openerId='page', type='page')))
        self.ledger.reap(['page'], lambda _: self.fail('stale snapshot closed protected popup'))
        with self.ledger.transaction(readonly=True) as data:
            self.assertEqual(data['targets']['sign-in-popup']['capability'], self.cap)
            self.assertEqual(data['targets']['sign-in-popup']['protections']['handoff'],
                             data['targets']['page']['protections']['handoff'])
        self.assertEqual(self.ledger.reap(['page', 'sign-in-popup'], lambda _: True), [])
        self.now += 1200
        self.assertEqual(set(self.ledger.reap(['page', 'sign-in-popup'], lambda _: True)), {'page', 'sign-in-popup'})

    def test_drain_refuses_acquires_but_allows_completion(self):
        from chrome_agent_lifecycle import Lifecycle, LifecycleError
        lifecycle = Lifecycle(self.ledger, 'test', lambda: None)
        lifecycle.running = True
        self.assertFalse(lifecycle.drain(timeout=0))
        with self.assertRaises(LifecycleError):
            lifecycle.control('acquire', dict(installation_id='test', owner=['p', 't', 's'], timeout_s=1), '')
        lifecycle.control('complete', dict(installation_id='test', capability=self.cap,
                          request_id=self.grant['request_id']), '')
        self.assertTrue(lifecycle.drain(timeout=0))

    def test_restart_grace_is_bounded_and_does_not_renew_on_repeated_crashes(self):
        self.ledger.complete(self.cap, self.grant['request_id'])
        self.now += 1801
        self.ledger.reconcile()
        self.assertTrue(self.ledger.owns(self.cap, 'page'))
        self.now += 2
        self.ledger.reconcile()
        self.assertFalse(self.ledger.owns(self.cap, 'page'))
        self.assertEqual(self.ledger.reap(['page'], lambda _: True), ['page'])


    def test_dead_worker_gets_grace_without_erasing_active_request(self):
        with mock.patch('chrome_agent_process.fingerprint', return_value='process-generation'):
            grant = self.ledger.acquire(['/profiles/example', 'worker', 'default'], 100, worker_pid=123)
        self.ledger.bind(grant['capability'], 'worker-page')
        with mock.patch('chrome_agent_process.fingerprint', return_value=None):
            self.ledger.reap_workers()
        self.now += 2
        self.assertEqual(self.ledger.reap(['page', 'worker-page'], lambda _: True), [])
        self.now += 130
        self.assertEqual(self.ledger.reap(['page', 'worker-page'], lambda _: True), ['worker-page'])

    def test_orphan_daemon_cleanup_waits_for_request_and_handoff(self):
        with self.ledger.transaction() as data:
            data['leases'][self.cap]['daemon_socket'] = '/isolated/bu-lease-' + self.cap + '.sock'
        identity = dict(path='/isolated/socket', pid=123, started='identity', inode=1)
        with mock.patch('chrome_agent_daemons.inspect', return_value=identity), \
             mock.patch('chrome_agent_daemons.shutdown', return_value=True) as shutdown:
            self.ledger.sweep_daemons()
            shutdown.assert_not_called()
            self.ledger.protect('page', 'handoff', episode='one')
            self.ledger.release(self.cap)
            self.now += 2
            self.ledger.sweep_daemons()
            shutdown.assert_not_called()
            self.now += 2000
            self.ledger.sweep_daemons()
            shutdown.assert_called_once_with(identity)


class RawGuardTests(unittest.TestCase):
    def test_managed_proxy_rejects_raw_mutation_without_wake(self):
        from chrome_agent_lifecycle import Lifecycle
        lifecycle = Lifecycle(mock.Mock(), 'test', mock.Mock())
        proxy = mock.Mock()
        for path in ('/json/new?about:blank', '/devtools/browser/raw', '/json/close/unknown'):
            self.assertTrue(lifecycle.route(proxy, None, None, 'GET', path, [], b'', mock.Mock()))
            self.assertEqual(proxy._respond.call_args.args[1], 403)
        lifecycle.wake.assert_not_called()
