"""Offline focus selector/route contracts; never enumerate or activate OS processes."""
import importlib.util
import os
import sys
import types
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('route_focus', Path(__file__).with_name('__init__.py'))
assert spec is not None and spec.loader is not None
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)
HOME = '/fixture/hermes'
EXE = '/fixture/chrome'
PID = 424242
ARGV = (EXE, '--user-data-dir=' + HOME + '/chrome-agent', '--remote-debugging-port=9555')
IDENTITY = (os.getuid(), EXE, ARGV, 123.0)
PS = str(PID) + ' ' + ' '.join(ARGV)


class RouteCustody(unittest.TestCase):
    def setUp(self):
        # Explicit no-core adapter double, in core's centisecond units. These
        # fixtures do not certify the transitive custom-core implementation.
        module = types.SimpleNamespace(start_time_fingerprints_match=lambda a, b: abs(int(a) - int(b)) <= 200)
        patch = mock.patch.dict(sys.modules, {'gateway.status': module})
        patch.start()
        self.addCleanup(patch.stop)

    def selector(self, identity, text=PS):
        with mock.patch.dict(os.environ, {'HERMES_CHROME_AGENT_CHROME': EXE}), \
                mock.patch.object(bf, '_process_identity', return_value=identity, create=True):
            return bf.agent_chromes(HOME, text)

    def test_healthy_owned_browser_is_selected(self):
        self.assertEqual(self.selector(IDENTITY), [(PID, 9555)])

    def test_wrong_executable_is_never_owned_by_command_text(self):
        wrong = (os.getuid(), '/fixture/not-chrome', ARGV, 123.0)
        self.assertEqual(self.selector(wrong), [])

    def test_sibling_prefix_is_not_profile_containment(self):
        args = (EXE, '--user-data-dir=' + HOME + '-other', '--remote-debugging-port=9555')
        identity = (os.getuid(), EXE, args, 123.0)
        self.assertEqual(self.selector(identity, str(PID) + ' ' + ' '.join(args)), [])

    def test_unknown_uid_role_and_ambiguous_argv_are_not_selected(self):
        variants = [None, (os.getuid() + 1, EXE, ARGV, 123.0),
                    (os.getuid(), EXE, ARGV + ('--type=renderer',), 123.0),
                    (os.getuid(), EXE, ARGV + ('--user-data-dir=/fixture/other',), 123.0),
                    (os.getuid(), EXE, ARGV + ('--remote-debugging-port=9556',), 123.0),
                    (os.getuid(), EXE, ARGV + ('--',), 123.0)]
        for identity in variants:
            with self.subTest(identity=identity):
                self.assertEqual(self.selector(identity), [])

    def test_tls_and_scoped_http_prefix_preserved(self):
        for raw, expected in [('https://example.invalid/prefix', 'https://example.invalid/prefix'),
                              ('wss://example.invalid/lease/CAP/devtools/browser/id', 'https://example.invalid/lease/CAP'),
                              ('ws://example.invalid/devtools/browser/id', 'http://example.invalid')]:
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {'BROWSER_CDP_URL': raw}):
                self.assertEqual(bf.remote_cdp(), expected)

    def test_configured_remote_is_authoritative_over_local_order(self):
        calls = []
        def get(url, timeout=3):
            calls.append(url)
            return [{'id': 'one', 'type': 'page', 'title': 'Example', 'url': 'https://example.invalid'}] if url.endswith('/json/list') else 'ok'
        with mock.patch.object(bf, '_quiet', return_value=False), \
                mock.patch.object(bf, 'agent_chromes', return_value=[(PID, 9555)]), \
                mock.patch.object(bf, 'remote_cdp', return_value='https://example.invalid/lease/CAP'), \
                mock.patch.object(bf, '_get', side_effect=get), \
                mock.patch.object(bf, '_visible', return_value=False), \
                mock.patch.object(bf, 'hold_for_user', return_value=None), \
                mock.patch.object(bf, 'activate_app') as activate, \
                mock.patch.object(bf, '_chrome_agent') as show:
            result = bf.focus('example')
        self.assertTrue(result['focused'])
        self.assertTrue(all(u.startswith('https://example.invalid/lease/CAP/') for u in calls), calls)
        activate.assert_not_called()
        show.assert_not_called()

    def test_remote_failure_never_falls_back_to_unrelated_local_browser(self):
        with mock.patch.object(bf, '_quiet', return_value=False), \
                mock.patch.object(bf, 'agent_chromes', return_value=[(PID, 9555)]), \
                mock.patch.object(bf, 'remote_cdp', return_value='https://example.invalid/prefix'), \
                mock.patch.object(bf, '_get', side_effect=OSError('fixture refusal')) as get, \
                mock.patch.object(bf, 'activate_app') as activate, \
                mock.patch.object(bf, '_chrome_agent') as show:
            result = bf.focus('example')
        self.assertFalse(result['focused'])
        self.assertTrue(all(call.args[0].startswith('https://example.invalid/prefix/') for call in get.call_args_list))
        activate.assert_not_called()
        show.assert_not_called()

    def test_local_generation_is_rechecked_before_each_focus_effect(self):
        replacement = (*IDENTITY[:3], 130.0)
        for values in ([IDENTITY, replacement], [IDENTITY, IDENTITY, replacement], [IDENTITY, IDENTITY, IDENTITY, replacement]):
            with self.subTest(values=values), \
                    mock.patch.object(bf, '_quiet', return_value=False), \
                    mock.patch.object(bf, 'agent_chromes', return_value=[bf._Chrome(PID, 9555, IDENTITY)]), \
                    mock.patch.object(bf, 'remote_cdp', return_value=None), \
                    mock.patch.object(bf, '_chrome_identity', side_effect=values), \
                    mock.patch.object(bf, '_get', side_effect=lambda u: [{'id':'one','type':'page','url':'https://example.invalid'}] if u.endswith('/json/list') else 'ok') as get, \
                    mock.patch.object(bf, '_chrome_agent', return_value=True) as show, \
                    mock.patch.object(bf, 'activate_app') as activate, \
                    mock.patch.object(bf, '_watch_login'), \
                    mock.patch.object(bf, 'hold_for_user', return_value=None), \
                    mock.patch.object(bf, '_visible', return_value=False):
                result = bf.focus('example')
            activate.assert_not_called()
            if len(values) == 2:
                show.assert_not_called()
            if len(values) < 4:
                self.assertFalse(result['focused'])
                self.assertFalse([c for c in get.call_args_list if '/json/activate/' in c.args[0]])

    def test_creation_fingerprint_uses_core_drift_tolerance(self):
        self.assertTrue(bf._same_chrome_identity(IDENTITY, (*IDENTITY[:3], 124.0)))
        self.assertFalse(bf._same_chrome_identity(IDENTITY, (*IDENTITY[:3], 130.0)))
        self.assertFalse(bf._same_chrome_identity(IDENTITY, None))
        self.assertFalse(bf._same_chrome_identity(IDENTITY, (os.getuid() + 1, *IDENTITY[1:])))

    def test_os_adapter_brackets_image_with_live_process_custody(self):
        from collections import namedtuple
        uids = namedtuple('uids', 'real effective saved')
        process = mock.Mock()
        process.create_time.return_value = 123.0
        process.uids.return_value = uids(os.getuid(), os.getuid(), os.getuid())
        process.exe.return_value = EXE
        process.cmdline.return_value = list(ARGV)
        process.is_running.return_value = True
        module = types.SimpleNamespace(Process=mock.Mock(return_value=process), Error=RuntimeError)
        with mock.patch.dict(sys.modules, {'psutil': module}):
            self.assertEqual(bf._process_identity(PID), IDENTITY)
            process.is_running.return_value = False
            self.assertIsNone(bf._process_identity(PID))
            process.is_running.return_value = True
            process.exe.side_effect = RuntimeError('fixture AccessDenied')
            self.assertIsNone(bf._process_identity(PID))

    def test_normalization_empty_endpoint_refuses_before_local_discovery(self):
        for raw in ('/', '///', '#', '?'):
            for match in (None, 'example'):
                with self.subTest(raw=raw, match=match), \
                        mock.patch.dict(os.environ, {'BROWSER_CDP_URL': raw}), \
                        mock.patch.object(bf, '_quiet', return_value=False), \
                        mock.patch.object(bf, 'agent_chromes', return_value=[bf._Chrome(PID, 9555, IDENTITY)]) as local, \
                        mock.patch.object(bf, '_chrome_identity', return_value=IDENTITY), \
                        mock.patch.object(bf, '_get') as get, \
                        mock.patch.object(bf, '_chrome_agent') as show, \
                        mock.patch.object(bf, 'activate_app') as activate:
                    result = bf.focus(match)
                    self.assertFalse(result['focused'])
                    local.assert_not_called()
                    get.assert_not_called()
                    show.assert_not_called()
                    activate.assert_not_called()

    def test_invalid_config_endpoint_is_not_absent_configuration(self):
        for raw in ('/', '///', '#', '?'):
            yaml = types.SimpleNamespace(safe_load=lambda stream: {'browser': {'cdp_url': raw}})
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {'BROWSER_CDP_URL': ''}), \
                    mock.patch.dict(sys.modules, {'yaml': yaml}), \
                    mock.patch('builtins.open', mock.mock_open(read_data='fixture')), \
                    mock.patch.object(bf, '_quiet', return_value=False), \
                    mock.patch.object(bf, 'agent_chromes') as local, \
                    mock.patch.object(bf, '_get') as get:
                result = bf.focus()
                self.assertFalse(result['focused'])
                local.assert_not_called()
                get.assert_not_called()

    def remote_focus(self, endpoint, transform=False):
        def get(url, timeout=3):
            return [{'id': 'one', 'type': 'page', 'title': 'Sign in', 'url': 'https://example.invalid/login'}] if '/json/list' in url else 'ok'
        with mock.patch.dict(os.environ, {'BROWSER_CDP_URL': endpoint}), \
                mock.patch.object(bf, '_quiet', return_value=False), \
                mock.patch.object(bf, 'agent_chromes') as local, \
                mock.patch.object(bf, '_get', side_effect=get), \
                mock.patch.object(bf, 'hold_for_user', return_value=None), \
                mock.patch.object(bf, 'activate_app') as activate, \
                mock.patch.object(bf, '_chrome_agent') as show, \
                mock.patch.object(bf, '_unattended', return_value=False), \
                mock.patch.object(bf, '_throttled', return_value=False), \
                mock.patch.object(bf, '_remember'):
            result = (bf.transform_tool_result('browser_exec', result='Sign in https://example.invalid/login', session_id='fixture')
                      if transform else bf.focus('example'))
        local.assert_not_called()
        activate.assert_not_called()
        show.assert_not_called()
        return result

    def test_scoped_loopback_activation_is_not_user_visibility(self):
        for endpoint in ('http://localhost/prefix', 'https://127.0.0.1/prefix?cap=fixture', 'http://[::1]:9555/prefix'):
            with self.subTest(endpoint=endpoint):
                result = self.remote_focus(endpoint)
                self.assertTrue(result['focused'])
                self.assertFalse(result['visible_to_user'])

    def test_visibility_note_preserves_loopback_and_remote_controls(self):
        for endpoint, visible in (('http://localhost/prefix', False),
                                  ('https://127.0.0.1/prefix?cap=fixture', False),
                                  ('https://example.invalid/prefix?cap=fixture', True)):
            with self.subTest(endpoint=endpoint):
                result = self.remote_focus(endpoint, transform=True)
                self.assertIsInstance(result, str)
                self.assertEqual('The Chrome window is now in front of the user' in result, visible)
                self.assertEqual("The user can't see this page" in result, not visible)

    def test_ordinary_remote_and_bare_loopback_visibility_controls(self):
        self.assertTrue(self.remote_focus('https://example.invalid/prefix?cap=fixture')['visible_to_user'])
        self.assertFalse(self.remote_focus('http://localhost:9555')['visible_to_user'])

    def test_query_scope_is_preserved(self):
        self.assertEqual(bf._cdp_url('https://example.invalid/prefix?cap=fixture', '/json/list'),
                         'https://example.invalid/prefix/json/list?cap=fixture')

    def test_invalid_remote_never_performs_an_unscoped_read(self):
        for raw in ('file:///fixture/secret', 'https://example.invalid/#fragment', 'https://user:pass@example.invalid', 'wss://['):
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {'BROWSER_CDP_URL': raw}), \
                    mock.patch.object(bf, '_quiet', return_value=False), \
                    mock.patch.object(bf, 'agent_chromes') as local, \
                    mock.patch.object(bf, '_get') as get:
                result = bf.focus('example')
            self.assertFalse(result['focused'])
            local.assert_not_called()
            get.assert_not_called()
