"""Isolated lifecycle contracts; no real Chrome or live profiles."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from chrome_agent_lifecycle import Ledger, LifecycleError, Config


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'leases.json'
        self.now = 1000
        self.ledger = Ledger(self.path, clock=lambda: self.now)
        self.owner = ['/profiles/example', 'task-example', 'session-example']

    def acquire(self, owner=None, timeout=60):
        return self.ledger.acquire(owner or self.owner, timeout)

    def test_owner_isolation_request_tokens_and_persistence(self):
        a = self.acquire()
        b = self.acquire(['/profiles/other', 'task-example', 'session-example'])
        self.assertNotEqual(a['capability'], b['capability'])
        again = self.acquire()
        self.assertEqual(a['capability'], again['capability'])
        self.assertNotEqual(a['request_id'], again['request_id'])
        self.ledger.bind(a['capability'], 'tab-a')
        reloaded = Ledger(self.path, clock=lambda: self.now)
        self.assertTrue(reloaded.owns(a['capability'], 'tab-a'))
        self.assertFalse(reloaded.owns(b['capability'], 'tab-a'))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.ledger.release(a['capability'])
        new = self.acquire()
        self.assertNotEqual(a['capability'], new['capability'])
        self.assertFalse(self.ledger.complete(a['capability'], a['request_id']))

    def test_long_request_idle_expiry_unknown_tabs_generation(self):
        a = self.acquire(timeout=3000)
        self.ledger.bind(a['capability'], 'tab-a')
        self.now += 1900
        self.assertEqual(self.ledger.reap(['tab-a'], lambda t: True), [])
        self.ledger.complete(a['capability'], a['request_id'])
        self.now += 1801
        closed = []
        self.assertEqual(self.ledger.reap(['tab-a', 'unknown'], lambda t: closed.append(t) or True), ['tab-a'])
        self.assertEqual(closed, ['tab-a'])
        self.assertFalse(self.ledger.can_idle(['unknown']))
        self.ledger.new_generation()
        self.assertFalse(self.ledger.owns(a['capability'], 'tab-a'))

    def test_corrupt_ledger_is_not_overwritten(self):
        self.path.write_text('{broken')
        with self.assertRaises(LifecycleError):
            self.acquire()
        self.assertEqual(self.path.read_text(), '{broken')

    def test_protections_survive_reload_and_are_bounded(self):
        a = self.acquire()
        cap = a['capability']
        self.ledger.bind(cap, 'tab-a')
        self.ledger.protect('tab-a', 'handoff')
        self.ledger.release(cap)
        self.now += 301
        self.ledger = Ledger(self.path, clock=lambda: self.now)
        self.assertEqual(self.ledger.reap(['tab-a'], lambda t: True), [])
        self.now += 901
        self.assertEqual(self.ledger.reap(['tab-a'], lambda t: True), ['tab-a'])

    def test_download_progress_and_structural_corruption(self):
        a = self.acquire()
        self.ledger.bind(a['capability'], 'tab-a')
        self.ledger.download('downloadWillBegin', {'guid': 'download-a'}, 'tab-a')
        self.ledger.release(a['capability'])
        for step in range(4):
            self.now += 100
            self.ledger.download('downloadProgress', {'guid': 'download-a', 'receivedBytes': step + 1, 'state': 'inProgress'})
        self.ledger = Ledger(self.path, clock=lambda: self.now)
        self.assertEqual(self.ledger.reap(['tab-a'], lambda t: True), [])
        self.now += 121
        self.assertEqual(self.ledger.reap(['tab-a'], lambda t: True), ['tab-a'])
        data = json.loads(self.path.read_text())
        data['leases'][a['capability']]['activity'] = 'broken'
        self.path.write_text(json.dumps(data))
        with self.assertRaises(LifecycleError):
            self.ledger.reap([], lambda t: self.fail('corruption must stop all sweeping'))

    def test_config_rejects_unknown_nonfinite_and_out_of_bounds(self):
        self.assertEqual(Config().idle_lease_s, 1800)
        for data in ({'idle_lease_s': 0}, {'idle_lease_s': float('nan')}, {'x': 1}, {'orphan_s': True}):
            with self.subTest(data=data), self.assertRaises(LifecycleError):
                Config(**data)


class RuntimeTests(unittest.TestCase):
    def test_request_deadline_starts_after_cold_wake(self):
        from chrome_agent_lifecycle import Lifecycle
        with tempfile.TemporaryDirectory() as tmp:
            now = [1000]
            ledger = Ledger(Path(tmp) / 'ledger.json', clock=lambda: now[0])
            def wake():
                now[0] += 40
            lifecycle = Lifecycle(ledger, 'example', wake)
            grant = lifecycle.control('acquire', dict(installation_id='example',
                owner=['/profiles/example', 'task', 'default'], timeout_s=5), '')
            with ledger.transaction() as state:
                self.assertGreaterEqual(state['leases'][grant['capability']]['requests'][grant['request_id']], now[0] + 35)

    def test_managed_launch_has_no_unowned_bootstrap_page(self):
        from test_hermes_chrome_agent import hca
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.multiple(hca, PROFILE=tmp), mock.patch.object(hca.subprocess, 'Popen') as spawn, \
                 mock.patch.object(hca, 'cdp_version', return_value={'Browser': 'Fake'}), \
                 mock.patch.object(hca.Supervisor, 'owned_version', return_value={'Browser': 'Fake'}):
                supervisor = hca.Supervisor()
                supervisor.lifecycle = mock.Mock()
                spawn.return_value.poll.return_value = None
                supervisor.start_chrome()
                argv = spawn.call_args.args[0]
                self.assertIn('--no-startup-window', argv)
                self.assertNotIn('about:blank', argv)

    def test_serial_wake_idle_shutdown_and_disabled_refusal(self):
        from concurrent.futures import ThreadPoolExecutor
        from chrome_agent_lifecycle import Lifecycle
        with tempfile.TemporaryDirectory() as tmp:
            now = [1000]
            ledger = Ledger(Path(tmp) / 'ledger.json', clock=lambda: now[0])
            calls = []
            lifecycle = Lifecycle(ledger, 'example', lambda: calls.append('wake'), lambda: calls.append('stop'))
            payload = dict(installation_id='example', owner=['/profiles/example', 'task', 'session'], timeout_s=60)
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: lifecycle.control('acquire', payload, 'http://127.0.0.1:1'), range(4)))
            self.assertEqual(calls, ['wake'])
            lifecycle.control('release', dict(installation_id='example', capability=results[0]['capability']), '')
            lifecycle.tick(['unknown'], lambda _: self.fail('must not close unknown'))
            now[0] += 2000
            lifecycle.tick(['unknown'], lambda _: self.fail('must not close unknown'))
            self.assertEqual(calls, ['wake'])
            lifecycle.tick([], lambda _: True)
            now[0] += 121
            lifecycle.tick([], lambda _: True)
            self.assertEqual(calls, ['wake', 'stop'])
            lifecycle.disabled = True
            with self.assertRaises(LifecycleError):
                lifecycle.control('acquire', payload, '')
            self.assertEqual(calls, ['wake', 'stop'])

    def test_supervisor_demand_wiring_and_exclusive_lock(self):
        from test_hermes_chrome_agent import hca
        from unittest import mock
        from chrome_agent_lifecycle import ExclusiveLock
        with tempfile.TemporaryDirectory() as tmp:
            with ExclusiveLock(Path(tmp) / 'supervisor.lock'):
                with self.assertRaises(LifecycleError):
                    ExclusiveLock(Path(tmp) / 'supervisor.lock')
            config = Path(tmp) / 'lifecycle-config.json'
            config.write_text(json.dumps({'installation_id': 'example'}))
            config.chmod(0o600)
            with mock.patch.multiple(hca, HERMES_HOME=tmp, STATE_DIR=tmp, PROFILE=str(Path(tmp) / 'chrome-agent')):
                s = hca.Supervisor()
                s.configure_lifecycle()
                self.assertFalse(s.lifecycle.running)
                with mock.patch.object(s, 'start_vdisplay') as vd, mock.patch.object(s, 'start_chrome') as chrome, \
                     mock.patch.object(s, 'remember_browser'), \
                     mock.patch.object(hca, 'cdp_version', return_value={'Browser': 'Fake'}):
                    s.lifecycle.control('acquire', dict(installation_id='example', owner=['/profiles/example', 'task', 'session'], timeout_s=60), '')
                    vd.assert_called_once()
                    chrome.assert_called_once()

    def test_managed_configuration_refuses_unsafe_file_and_personal_profile(self):
        from test_hermes_chrome_agent import hca
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp, 'lifecycle-config.json')
            config.write_text(json.dumps({'installation_id': 'example'}))
            with mock.patch.multiple(hca, HERMES_HOME=tmp, STATE_DIR=tmp, PROFILE=str(Path(tmp) / 'chrome-agent')):
                config.chmod(0o644)
                with self.assertRaises(LifecycleError):
                    hca.Supervisor().configure_lifecycle()
                config.chmod(0o600)
                with mock.patch.object(hca, 'PROFILE', str(Path(tmp) / 'personal')):
                    with self.assertRaises(LifecycleError):
                        hca.Supervisor().configure_lifecycle()

    def test_supervisor_routes_download_events_and_passkey_holds(self):
        from test_hermes_chrome_agent import hca
        from unittest import mock
        s = hca.Supervisor()
        s.lifecycle = mock.Mock()
        s.sess_target['session-a'] = 'tab-a'
        s.on_event({'method': 'Page.frameNavigated', 'sessionId': 'session-a', 'params': {'frame': {'id': 'frame-a'}}})
        s.on_event({'method': 'Browser.downloadWillBegin', 'params': {'guid': 'download-a', 'frameId': 'frame-a'}})
        s.lifecycle.ledger.download.assert_called_with('downloadWillBegin', {'guid': 'download-a', 'frameId': 'frame-a'}, 'tab-a')
        s.on_event({'method': 'Browser.downloadProgress', 'params': {'guid': 'download-a', 'receivedBytes': 10}})
        s.lifecycle.ledger.download.assert_called_with('downloadProgress', {'guid': 'download-a', 'receivedBytes': 10}, None)

    def test_install_no_start_and_launchd_stop_semantics(self):
        from test_hermes_chrome_agent import hca
        from unittest import mock
        import plistlib
        plist = plistlib.loads(hca.plist_text().encode())
        self.assertEqual(plist['KeepAlive'], {'SuccessfulExit': False})
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'Library', 'LaunchAgents').mkdir(parents=True)
            with mock.patch.multiple(hca, HOME=tmp, TOOLS=tmp, STATE_DIR=tmp, LOG=str(Path(tmp) / 'log')):
                with mock.patch.object(hca.subprocess, 'check_call') as check, \
                     mock.patch.object(hca.subprocess, 'call') as call, \
                     mock.patch.object(hca, 'build_notifier', return_value='test'), \
                     mock.patch.object(hca, 'notifier_status', return_value='authorized'):
                    hca.cmd_install(no_start=True, managed=True)
                    self.assertFalse(any('bootstrap' in c.args[0] for c in check.call_args_list))
                    self.assertTrue(any('bootout' in c.args[0] for c in call.call_args_list))
                    manifest = json.loads(Path(tmp, 'lifecycle-config.json').read_text())
                    self.assertTrue(manifest['installation_id'])


class ScopedCommandTests(unittest.TestCase):
    def test_reconnect_autoattach_is_owned_only_and_polling_does_not_renew(self):
        from chrome_agent_lifecycle import Lifecycle, ScopedCDP
        from test_hermes_chrome_agent import hca
        from unittest import mock
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            now = [1000]
            ledger = Ledger(Path(tmp) / 'ledger.json', clock=lambda: now[0])
            a = ledger.acquire(['/profiles/example', 'task', 'session'], 60)
            cap = a['capability']
            ledger.bind(cap, 'owned')
            upstream = mock.Mock()
            def command(method, params=None, **kw):
                if method == 'Target.getTargets':
                    return {'targetInfos': [{'targetId': 'owned', 'type': 'page'}, {'targetId': 'unknown', 'type': 'page'}]}
                if method == 'Target.attachToTarget':
                    return {'sessionId': 'owned-session'}
                return {}
            upstream.call.side_effect = command
            events = []
            api = SimpleNamespace(CDP=lambda *a, **k: upstream, cdp_version=lambda **kw: {'webSocketDebuggerUrl': 'fake'},
                                  filter_cdp_message=hca.filter_cdp_message)
            connection = ScopedCDP(Lifecycle(ledger, 'example', lambda: None),
                                   SimpleNamespace(upstream=1, show=lambda: False), cap, api, events.append)
            connection.call('Target.setAutoAttach', {'autoAttach': True, 'flatten': True})
            self.assertEqual([e['params']['targetInfo']['targetId'] for e in events], ['owned'])
            connection.call('Runtime.evaluate', {'expression': '1'}, 'owned-session')
            self.assertEqual(connection.call('Target.setAutoAttach', {'autoAttach': True, 'flatten': True,
                              'waitForDebuggerOnStart': False}, 'owned-session'), {})
            with self.assertRaises(LifecycleError):
                connection.call('Runtime.evaluate', {'expression': '1'}, 'unknown-session')
            now[0] += 1801
            with self.assertRaises(LifecycleError):
                connection.call('Runtime.evaluate', {'expression': '1'}, 'owned-session')
            now[0] = 1001
            ledger.new_generation()
            self.assertFalse(ledger.owns(cap, 'owned'))
            ledger.acquire(['/profiles/example', 'task', 'session'], 60)
            ledger.bind(cap, 'owned')
            with self.assertRaises(LifecycleError):
                connection.call('Target.getTargets', {})


class SmokeSafetyTests(unittest.TestCase):
    def test_live_ports_and_profiles_are_refused(self):
        from smoke_chrome_lifecycle import validate_isolation
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            validate_isolation(19001, 19002, root / 'profile', root)
            for port, upstream, profile in [(9333, 19002, root / 'profile'), (19001, 9334, root / 'profile'),
                                             (19001, 19002, Path('/profiles/existing'))]:
                with self.assertRaises(ValueError):
                    validate_isolation(port, upstream, profile, root)


class ProxyTests(unittest.TestCase):
    def setUp(self):
        from test_hermes_chrome_agent import hca, FakeChrome
        from chrome_agent_lifecycle import Lifecycle
        self.hca = hca
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.chrome = FakeChrome()
        self.addCleanup(self.chrome.close)
        self.wakes = []
        self.lifecycle = Lifecycle(Ledger(Path(self.tmp.name) / 'ledger.json'), 'installation-example',
                                   wake=lambda: self.wakes.append(True))
        self.proxy = hca.CDPProxy(0, self.chrome.port, show=lambda: False, logf=lambda m: None,
                                 lifecycle=self.lifecycle).start()
        self.addCleanup(self.proxy.close)
        self.base = 'http://127.0.0.1:%s' % self.proxy.port

    def request(self, path, data=None, method=None):
        import urllib.request
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode() if data is not None else None,
                                     method=method, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.load(response)

    def acquire(self, owner=None):
        return self.request('/lifecycle/acquire', dict(installation_id='installation-example',
                            owner=owner or ['/profiles/example', 'task-example', 'session-example'], timeout_s=60))

    def test_acquire_scoped_discovery_create_and_release(self):
        import urllib.error
        a = self.acquire()
        cap = a['capability']
        path = '/lease/' + cap
        self.assertEqual(a['endpoint'], self.base + path)
        self.assertEqual(self.wakes, [True])
        self.acquire()
        self.assertEqual(self.wakes, [True])
        self.assertEqual(self.request(path + '/json/list'), [])
        version = self.request(path + '/json/version')
        self.assertIn(path + '/', version['webSocketDebuggerUrl'])
        c = self.hca.CDP(version['webSocketDebuggerUrl'])
        self.addCleanup(c.close)
        self.assertEqual(c.call('Target.createTarget', {'url': 'about:blank'}), {'targetId': 'T-NEW'})
        self.assertEqual([t['id'] for t in self.request(path + '/json/list')], ['T-NEW'])
        self.assertTrue(self.chrome.received[-1]['params']['background'])
        self.request('/lifecycle/release', dict(installation_id='installation-example', capability=cap))
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.request(path + '/json/list')
        self.assertEqual(err.exception.code, 403)
        with self.assertRaises(self.hca.CDPError):
            c.call('Target.createTarget', {'url': 'about:blank'})

    def test_invalid_auth_and_targets_fail_closed_no_wake(self):
        import urllib.error
        for path, data in [('/lifecycle/acquire', None),
                           ('/lifecycle/acquire', {'installation_id': 'wrong'}),
                           ('/lease/invalid/json/list', None)]:
            with self.assertRaises(urllib.error.HTTPError):
                self.request(path, data)
        self.assertEqual(self.wakes, [])
        a = self.acquire()
        path = '/lease/' + a['capability']
        c = self.hca.CDP(self.request(path + '/json/version')['webSocketDebuggerUrl'])
        self.addCleanup(c.close)
        for method, params in [('Target.closeTarget', {'targetId': 'unknown'}),
                               ('Target.attachToTarget', {'targetId': 'unknown'}),
                               ('Browser.close', {}), ('Target.sendMessageToTarget', {'message': '{}'}),
                               ('Runtime.evaluate', {'expression': '1'})]:
            with self.subTest(method=method), self.assertRaises(self.hca.CDPError):
                c.call(method, params)
        self.assertEqual(self.chrome.received, [])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/json/list')  # a managed listener is never a legacy tunnel
        self.assertEqual(error.exception.code, 403)


if __name__ == '__main__':
    unittest.main()
