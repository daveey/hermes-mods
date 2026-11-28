"""Offline legacy custody and decoded focus regressions; no process or socket IO."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import unittest
from unittest import mock

_path = os.environ.get('HCA_REGRESSION_SOURCE', str(Path(__file__).with_name('hermes-chrome-agent')))
_loader = importlib.machinery.SourceFileLoader('focus_custody_helper', _path)
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
assert _spec is not None
hca = importlib.util.module_from_spec(_spec)
_loader.exec_module(hca)


class LegacyCustody(unittest.TestCase):
    def test_stale_unrelated_pid_still_starts_without_signalling(self):
        import chrome_agent_process as processes
        supervisor = hca.Supervisor()
        child = mock.Mock(pid=424243)
        child.poll.return_value = None
        image = (hca.os.getuid(), '/fixture/unrelated-worker', ['unrelated-worker'])
        with mock.patch.object(hca.os, 'readlink', return_value='fixture-host-424242'), \
                mock.patch.object(hca, '_legacy_process_image', return_value=image, create=True), \
                mock.patch.object(processes, 'fingerprint', return_value='fixture:1'), \
                mock.patch.object(hca.os, 'makedirs'), \
                mock.patch.object(supervisor, '_prefs_off_savers'), \
                mock.patch.object(hca.subprocess, 'Popen', return_value=child) as launch, \
                mock.patch.object(hca, 'cdp_version', return_value={'Browser':'fixture'}), \
                mock.patch.object(hca.time, 'sleep'), \
                mock.patch.object(hca.os, 'kill') as kill:
            supervisor.start_chrome()
        launch.assert_called_once()
        kill.assert_not_called()
        self.assertIs(supervisor.chrome, child)

    def test_ambiguous_stale_reused_and_owned_looking_locks_never_authorize_signals(self):
        for lock in ('fixture-host-424242', 'fixture-host-0', 'fixture-host--1', 'garbage', ''):
            with self.subTest(lock=lock):
                supervisor = hca.Supervisor()
                supervisor.chrome = mock.Mock(pid=424242)
                with mock.patch.object(hca.os, 'readlink', return_value=lock), \
                        mock.patch.object(supervisor, '_legacy_browser_identity', return_value=None), \
                        mock.patch.object(hca.os, 'kill') as kill:
                    supervisor._kill_stale_chrome()
                kill.assert_not_called()

    def test_unreadable_or_non_symlink_lock_supplies_no_signal_authority(self):
        for error in (PermissionError('denied'), OSError('not a symlink')):
            with self.subTest(error=error):
                with mock.patch.object(hca.os, 'readlink', side_effect=error), \
                        mock.patch.object(hca.os, 'kill') as kill:
                    hca.Supervisor()._kill_stale_chrome()
                kill.assert_not_called()

    def test_fresh_legacy_start_without_lock_preserves_owned_child_control(self):
        supervisor = hca.Supervisor()
        child = mock.Mock(pid=424242)
        child.poll.return_value = None
        with mock.patch.object(hca.os, 'readlink', side_effect=FileNotFoundError), \
                mock.patch.object(hca.os, 'makedirs'), \
                mock.patch.object(supervisor, '_prefs_off_savers'), \
                mock.patch.object(hca.subprocess, 'Popen', return_value=child) as launch, \
                mock.patch.object(hca, 'cdp_version', return_value={'Browser':'fixture'}), \
                mock.patch.object(hca.time, 'sleep'), \
                mock.patch.object(hca.os, 'kill') as kill:
            supervisor.start_chrome()
            self.assertIs(supervisor.chrome, child)
            self.assertIn('--user-data-dir=' + hca.PROFILE, launch.call_args.args[0])
            supervisor.stop_children()
            child.terminate.assert_called_once_with()
            child.wait.assert_called_once_with(10)
            kill.assert_not_called()

    def test_unknown_identity_refuses_signals_not_startup(self):
        supervisor = hca.Supervisor()
        with mock.patch.object(hca.os, 'readlink', return_value='fixture-host-424242'), \
                mock.patch.object(supervisor, '_legacy_browser_identity', return_value=None, create=True), \
                mock.patch.object(hca.os, 'kill') as kill:
            supervisor._kill_stale_chrome()
        kill.assert_not_called()

    def test_owned_generation_is_rechecked_before_term_and_kill(self):
        identity = {'pid':424242, 'started':'fixture:1'}
        for later in (False, None, {'pid':424242, 'started':'fixture:2'}):
            with self.subTest(later=later):
                supervisor = hca.Supervisor()
                with mock.patch.object(hca.os, 'readlink', return_value='fixture-host-424242'), \
                        mock.patch.object(supervisor, '_legacy_browser_identity', side_effect=[identity, later], create=True), \
                        mock.patch.object(hca.os, 'kill') as kill:
                    supervisor._kill_stale_chrome()
                kill.assert_not_called()

    def test_owned_browser_term_exit_preserves_no_kill_control(self):
        identity = {'pid':424242, 'started':'fixture:1'}
        supervisor = hca.Supervisor()
        with mock.patch.object(hca.os, 'readlink', return_value='fixture-host-424242'), \
                mock.patch.object(supervisor, '_legacy_browser_identity', side_effect=[identity, identity, False], create=True), \
                mock.patch.object(hca.time, 'sleep'), \
                mock.patch.object(hca.os, 'kill') as kill:
            supervisor._kill_stale_chrome()
        kill.assert_called_once_with(424242, hca.signal.SIGTERM)

    def test_reused_pid_after_term_is_never_escalated(self):
        identity = {'pid':424242, 'started':'fixture:1'}
        replacement = {'pid':424242, 'started':'fixture:2'}
        supervisor = hca.Supervisor()
        with mock.patch.object(hca.os, 'readlink', return_value='fixture-host-424242'), \
                mock.patch.object(supervisor, '_legacy_browser_identity', side_effect=[identity, identity, replacement], create=True), \
                mock.patch.object(hca.time, 'sleep'), \
                mock.patch.object(hca.os, 'kill') as kill:
            supervisor._kill_stale_chrome()
        kill.assert_called_once_with(424242, hca.signal.SIGTERM)

    def test_owned_browser_escalation_revalidates_custody(self):
        identity = {'pid':424242, 'started':'fixture:1'}
        supervisor = hca.Supervisor()
        with mock.patch.object(hca.os, 'readlink', return_value='fixture-host-424242'), \
                mock.patch.object(supervisor, '_legacy_browser_identity', return_value=identity, create=True) as probe, \
                mock.patch.object(hca.time, 'sleep'), \
                mock.patch.object(hca.os, 'kill') as kill:
            supervisor._kill_stale_chrome()
        self.assertEqual(kill.call_args_list, [mock.call(424242, hca.signal.SIGTERM), mock.call(424242, hca.signal.SIGKILL)])
        self.assertGreaterEqual(probe.call_count, 103)


class LegacyImage(unittest.TestCase):
    def test_process_image_unavailable_and_malformed_controls(self):
        import ctypes
        for failure, expected in ((FileNotFoundError(), False), (PermissionError(), None)):
            proc = mock.Mock()
            proc.stat.side_effect = failure
            with mock.patch.object(hca.sys, 'platform', 'linux'), \
                    mock.patch('pathlib.Path', return_value=proc):
                self.assertIs(hca._legacy_process_image(424242), expected)
        with mock.patch.object(hca.sys, 'platform', 'unsupported'):
            self.assertIsNone(hca._legacy_process_image(424242))
        for status in (-1, 0):
            native = mock.Mock()
            native.sysctl.return_value = status  # size stays zero on nominal success
            with mock.patch.object(hca.sys, 'platform', 'darwin'), \
                    mock.patch.object(ctypes, 'CDLL', return_value=native):
                self.assertIsNone(hca._legacy_process_image(424242))

    def test_procargs_truncation_and_invalid_argc_refuse(self):
        import struct
        for data in (b'', struct.pack('=i',0)+b'chrome\0\0', struct.pack('=i',2)+b'chrome\0\0only-one\0'):
            with self.subTest(data=data):
                with self.assertRaises((ValueError, struct.error)):
                    hca._decode_procargs2(data)

    def test_ownership_and_generation_negative_controls(self):
        import chrome_agent_process as processes
        method = hca.Supervisor()._legacy_browser_identity
        uid = hca.os.getuid()
        cases = [
            ((uid, '/fixture/not-chrome', ['other']), False),
            ((uid + 1, hca.CHROME, ['chrome','--user-data-dir='+hca.PROFILE]), False),
            ((uid, hca.CHROME, ['chrome','--user-data-dir='+hca.PROFILE+'-other']), False),
            ((uid, hca.CHROME, ['chrome','--other=--user-data-dir='+hca.PROFILE]), False),
            ((uid, hca.CHROME, ['chrome','--user-data-dir='+hca.PROFILE,'--type=renderer']), False),
            ((uid, hca.CHROME, ['chrome','--user-data-dir='+hca.PROFILE,'--user-data-dir=/other']), None),
            ((uid, hca.CHROME, ['chrome','--user-data-dir']), None),
            ((uid, hca.CHROME, ['chrome','--user-data-dir',hca.PROFILE]), None),
            ((uid, hca.CHROME, ['chrome','--','--user-data-dir='+hca.PROFILE]), None),
            (None, None), (False, False)]
        for image, expected in cases:
            with self.subTest(image=image):
                with mock.patch.object(hca, '_legacy_process_image', return_value=image), \
                        mock.patch.object(processes, 'fingerprint', return_value='fixture:1'):
                    self.assertIs(method(424242), expected)
        image=(uid,hca.CHROME,['chrome','--user-data-dir='+hca.PROFILE])
        for times in ((None, None), ('fixture:1','fixture:2')):
            with self.subTest(times=times):
                with mock.patch.object(hca, '_legacy_process_image', return_value=image), \
                        mock.patch.object(processes, 'fingerprint', side_effect=times):
                    self.assertIsNone(method(424242))

    def test_linux_image_uses_kernel_argv_not_flattened_command(self):
        probe = getattr(hca, '_legacy_process_image', lambda pid: None)
        proc = mock.MagicMock()
        proc.stat.return_value.st_uid = 123
        proc.__truediv__.return_value.read_bytes.return_value = b'chrome\0--user-data-dir=/fixture/profile with spaces\0'
        with mock.patch.object(hca.sys, 'platform', 'linux'), \
                mock.patch('pathlib.Path', return_value=proc) as path, \
                mock.patch.object(hca.os, 'readlink', return_value='/fixture/chrome'):
            self.assertEqual(probe(424242), (123, '/fixture/chrome', ['chrome', '--user-data-dir=/fixture/profile with spaces']))
        path.assert_called_once_with('/proc/424242')

    def test_darwin_image_reads_kernel_vector_with_no_native_ingress(self):
        import ctypes
        import struct
        probe = getattr(hca, '_legacy_process_image', lambda pid: None)
        binary = b'/fixture/Google Chrome'
        args = [binary, b'--user-data-dir=/fixture/profile with spaces']
        data = struct.pack('=i', len(args)) + binary + b'\0\0' + b'\0'.join(args) + b'\0'
        native = mock.Mock()
        def sysctl(mib, count, buf, size, new, newlen):
            self.assertEqual(list(mib), [1,49,424242])
            self.assertEqual(count, 3)
            size._obj.value = len(data)
            if buf is not None:
                buf.raw = data
            return 0
        native.sysctl.side_effect = sysctl
        with mock.patch.object(hca.sys, 'platform', 'darwin'), \
                mock.patch.object(ctypes, 'CDLL', return_value=native) as load, \
                mock.patch.object(hca.subprocess, 'check_output', return_value='123\n'):
            self.assertEqual(probe(424242), (123, binary.decode(), [arg.decode() for arg in args]))
        self.assertEqual(native.sysctl.call_count, 2)
        load.assert_called_once_with(None, use_errno=True)

    def test_kernel_procargs_preserves_argument_boundaries(self):
        import struct
        decode = getattr(hca, '_decode_procargs2', lambda data: None)
        path = b'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        args = [path, b'--user-data-dir=/fixture/profile with spaces', b'--other=--user-data-dir=/elsewhere']
        data = struct.pack('=i', len(args)) + path + b'\0\0' + b'\0'.join(args) + b'\0ENV=ignored\0'
        self.assertEqual(decode(data), (path.decode(), [arg.decode() for arg in args]))

    def test_dedicated_browser_requires_exact_executable_profile_and_generation(self):
        import chrome_agent_process as processes
        identity = getattr(hca.Supervisor(), '_legacy_browser_identity', lambda pid: None)
        image = (hca.os.getuid(), hca.CHROME, [hca.CHROME, '--user-data-dir=' + hca.PROFILE])
        with mock.patch.object(hca, '_legacy_process_image', return_value=image, create=True), \
                mock.patch.object(processes, 'fingerprint', return_value='fixture:1'):
            self.assertEqual(identity(424242), {'pid':424242, 'started':'fixture:1', 'image':image})


class OrdinaryCompatibility(unittest.TestCase):
    def test_unescaped_show_hide_cli_preserves_flag_and_focus_behavior(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            flag = str(Path(directory) / 'show')
            with mock.patch.object(hca, 'STATE_DIR', directory), \
                    mock.patch.object(hca, 'SHOW_FLAG', flag), \
                    mock.patch.object(hca.time, 'sleep'), \
                    mock.patch.object(hca.time, 'time', return_value=1000):
                self.assertEqual(hca.main(['helper','show','--minutes','20']), 0)
                self.assertEqual(float(Path(flag).read_text()), 2200)
                self.assertTrue(hca.show_active())
                plain = {'id':1, 'method':'Target.activateTarget'}
                self.assertIsNone(hca.filter_cdp_message(plain, hca.show_active()))
                self.assertEqual(hca.main(['helper','hide']), 0)
                self.assertFalse(Path(flag).exists())
                self.assertFalse(hca.show_active())
                self.assertEqual(hca.filter_cdp_message(plain, hca.show_active()), ('reply', {'id':1,'result':{}}))
                self.assertEqual(hca.main(['helper','hide']), 0)  # idempotent


class FocusFrames(unittest.TestCase):
    def test_decoded_raising_matrix(self):
        import io
        import json
        for method in ('Target.activateTarget', 'Page.bringToFront', 'Target.createTarget'):
            # Escape every character: no literal raising-method fragment survives.
            spelling = ''.join('\\u%04x' % ord(c) for c in method)
            for fragmented in (False, True):
                for opcode in (1, 2):
                    with self.subTest(method=method, fragmented=fragmented, opcode=opcode):
                        payload = ('{"id":7,"sessionId":"S","method":"%s","params":{"focus":true}}' % spelling).encode()
                        upstream, client, logs = self.pump(payload, fragmented=fragmented, opcode=opcode)
                        if method == 'Target.createTarget':
                            self.assertEqual(client, [])
                            self.assertEqual(len(upstream), 1)
                            reader = io.BytesIO(upstream[0])
                            complete = b''
                            while reader.tell() < len(upstream[0]):
                                complete += hca.read_frame(reader)[3]
                            result = json.loads(complete)
                            self.assertIs(result['params'].get('background'), True)
                            self.assertTrue(result['params']['newWindow'])
                            self.assertNotIn('focus', result['params'])
                        else:
                            self.assertEqual(upstream, [])
                            result = json.loads(hca.read_frame(io.BytesIO(client[0]))[3])
                            self.assertEqual(result, {'id':7, 'sessionId':'S', 'result':{}})
                        self.assertEqual(len(logs), 1)

    def assert_tunnel_denied(self, payload, fragmented=False, opcode=1):
        import io
        import json
        upstream, client, logs = self.pump(payload, fragmented=fragmented, opcode=opcode)
        self.assertEqual(upstream, [], 'hidden tunnel must not reach Chrome unchanged')
        self.assertEqual(len(client), 1)
        reply = json.loads(hca.read_frame(io.BytesIO(client[0]))[3])
        self.assertEqual(reply['id'], 9)
        self.assertEqual(reply['error']['code'], -32000)
        self.assertIn('flat', reply['error']['message'])
        self.assertNotIn('result', reply)
        self.assertEqual(len(logs), 1)

    def test_nested_bring_to_front_denied(self):
        import json
        payload = json.dumps({'id': 9, 'method': 'Target.sendMessageToTarget',
                              'params': {'sessionId': 'inner', 'message': json.dumps(
                                  {'id': 1, 'method': 'Page.bringToFront'})}}).encode()
        self.assert_tunnel_denied(payload)

    def test_nested_escaped_fragmented_create_target_denied(self):
        import json
        spelling = ''.join('\\u%04x' % ord(c) for c in 'Target.createTarget')
        inner = '{"id":1,"method":"%s","params":{"url":"about:blank","focus":true}}' % spelling
        payload = json.dumps({'id': 9, 'method': 'Target.sendMessageToTarget',
                              'params': {'sessionId': 'inner', 'message': inner}}).encode()
        self.assert_tunnel_denied(payload, fragmented=True)

    def test_hidden_tunnel_envelope_matrix(self):
        import io
        import json
        # Deny even malformed/recursive tunnels: no inner parser is trusted here.
        for inner in ('not json', '[]', '{"method":"Target.sendMessageToTarget"}'):
            for opcode in (1, 2):
                with self.subTest(inner=inner, opcode=opcode):
                    payload = json.dumps({'id': 9, 'sessionId': 'outer',
                                          'method': 'Target.sendMessageToTarget',
                                          'params': {'message': inner}}).replace(
                                              'sendMessage', r'send\u004dessage').encode()
                    upstream, client, logs = self.pump(payload, fragmented=True, opcode=opcode)
                    self.assertEqual(upstream, [])
                    reply = json.loads(hca.read_frame(io.BytesIO(client[0]))[3])
                    self.assertEqual(reply['sessionId'], 'outer')
                    self.assertEqual(reply['id'], 9)
                    self.assertEqual(reply['error']['code'], -32000)

    def test_flat_session_routing_preserved(self):
        import io
        import json
        payload = b'{"id":9,"sessionId":"flat","method":"Page.bringToFront"}'
        upstream, client, logs = self.pump(payload)
        self.assertEqual(upstream, [])
        self.assertEqual(json.loads(hca.read_frame(io.BytesIO(client[0]))[3]),
                         {'id': 9, 'sessionId': 'flat', 'result': {}})
        payload = b'{"id":9,"sessionId":"flat","method":"Runtime.enable"}'
        upstream, client, logs = self.pump(payload)
        self.assertEqual(hca.read_frame(io.BytesIO(upstream[0]))[3], payload)
        self.assertEqual(client, [])

    def test_shown_nonraising_tunnel_unchanged(self):
        import io
        import json
        payload = json.dumps({'id': 9, 'method': 'Target.sendMessageToTarget',
                              'params': {'sessionId': 'inner', 'message': json.dumps(
                                  {'id': 1, 'method': 'Runtime.enable'})}}).encode()
        upstream, client, logs = self.pump(payload, show=True)
        self.assertEqual(hca.read_frame(io.BytesIO(upstream[0]))[3], payload)
        self.assertEqual(client, [])
        self.assertEqual(logs, [])

    def test_hidden_undecodable_complete_messages_deny(self):
        for payload in (b'not json', b'{"method":"Target.activateTarget"', b'\xff', b'[]', b'null', b'{"method":7}'):
            for fragmented in (False, True):
                with self.subTest(payload=payload, fragmented=fragmented):
                    upstream, client, logs = self.pump(payload, fragmented=fragmented)
                    self.assertEqual(upstream, [])
                    self.assertEqual(client, [])

    def test_ordinary_nonraising_and_show_controls(self):
        import io
        for show, payload in ((False, b'{"id":3,"method":"Runtime.enable"}'),
                              (True, b'{"id":3,"method":"Target.acti\\u0076ateTarget"}'),
                              (True, b'not json')):
            for fragmented in (False, True):
                with self.subTest(show=show, payload=payload, fragmented=fragmented):
                    upstream, client, logs = self.pump(payload, show=show, fragmented=fragmented)
                    self.assertEqual(len(upstream), 1)
                    reader = io.BytesIO(upstream[0])
                    decoded = b''
                    while reader.tell() < len(upstream[0]):
                        decoded += hca.read_frame(reader)[3]
                    self.assertEqual(decoded, payload)
                    self.assertEqual(client, [])
                    self.assertEqual(logs, [])

    def test_plain_activation_control(self):
        upstream, client, logs = self.pump(b'{"id":1,"method":"Target.activateTarget"}')
        self.assertEqual(upstream, [])
        self.assertEqual(len(client), 1)
        self.assertEqual(len(logs), 1)

    @staticmethod
    def pump(payload, show=False, fragmented=False, opcode=1):
        import io
        import threading

        class Reader:
            def __init__(self, data):
                self.data = io.BytesIO(data)

            def read(self, size):
                result = self.data.read(size)
                if len(result) != size:
                    raise EOFError
                return result

        class Capture:
            def __init__(self):
                self.sent = []

            def sendall(self, data):
                self.sent.append(data)

            def shutdown(self, *_):
                pass

        if fragmented:
            split = len(payload) // 2
            first = bytearray(hca.encode_frame(opcode, payload[:split], mask=True))
            first[0] &= 0x7f
            frames = bytes(first) + hca.encode_frame(0, payload[split:], mask=True)
        else:
            frames = hca.encode_frame(opcode, payload, mask=True)
        upstream, client = Capture(), Capture()
        logs = []
        proxy = hca.CDPProxy(0, 1, show=lambda: show, logf=logs.append)
        proxy.peers[123] = 'fixture'
        proxy._pump_up(Reader(frames), upstream, client, threading.Lock(), ('127.0.0.1', 123))
        return upstream.sent, client.sent, logs

    def test_escaped_activation_is_locally_answered(self):
        payload = b'{"id":1,"method":"Target.acti\\u0076ateTarget","params":{"targetId":"T"}}'
        upstream, client, logs = self.pump(payload)
        self.assertEqual(upstream, [])
        self.assertEqual(len(client), 1)
        self.assertIn('Target.activateTarget', logs[0])

    def test_raising_without_id_never_forwards(self):
        upstream, client, logs = self.pump(b'{"method":"Page.bringToFront"}')
        self.assertEqual(upstream, [])
        self.assertEqual(client, [])
