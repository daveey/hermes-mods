"""All dangerous commands are classifier input, never executed."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('broadcast_guard', Path(__file__).resolve().parents[1] / '__init__.py')
assert spec and spec.loader
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class Broadcast(unittest.TestCase):
    def test_shell_no_discovery(self):
        commands = [
            'kill -9 -1', 'kill -TERM -- -1', 'kill -9 -- -45678',
            'kill -s KILL 0', 'kill --signal=SIGKILL -- -45678',
            "bash -c 'kill -9 -1'", "ssh example.com 'kill -9 -1'",
            'python3 ~/.hermes/sync-runner/hermes-sync pause-turns -- kill -9 -1',
            'pkill -9 -u 1000', 'pkill -KILL --uid=1000',
            'pkill -9 -fu 1000', 'pkill -9 -fU 1000', 'pkill -9 -U1000',
            'pkill --signal=9 --euid 1000',
        ]
        with patch.object(guard, 'protected_processes', return_value={}):
            for command in commands:
                with self.subTest(command=command):
                    self.assertIsNotNone(guard.classify('terminal', {'command': command}))

    def test_python_broadcast(self):
        with patch.object(guard, 'protected_processes', return_value={}):
            for code in ('os.kill(-1, 9)', 'os.killpg(1, signal.SIGKILL)',
                         'os.killpg(1, signal.SIGTERM)',
                         'os.killpg(0, 9)', 'os.killpg(45678, 9)',
                         'os.kill(-45678, signal.SIGKILL)'):
                with self.subTest(code=code):
                    self.assertIsNotNone(guard.classify('execute_code', {'code': code}))
                    self.assertIsNotNone(guard.classify('terminal', {'command': f"python3 -c '{code}'"}))

    def test_probes_and_specific_cleanup(self):
        with patch.object(guard, 'protected_processes', return_value={}):
            for command in ('kill -0 -1', 'kill -0 -- -45678', 'kill -9 45678',
                            'kill -TERM -- -45678', 'pkill -0 -u 1000', 'kill -l',
                            "echo 'kill -9 -1'", 'systemctl status user@1000.service'):
                self.assertIsNone(guard.classify('terminal', {'command': command}), command)
            for code in ('os.killpg(1, 0)', 'os.kill(45678, 9)', 'os.killpg(45678, signal.SIGTERM)'):
                self.assertIsNone(guard.classify('execute_code', {'code': code}), code)

    def test_user_manager(self):
        with patch.object(guard, 'protected_processes', return_value={}):
            for command in ('sudo systemctl kill user@1000.service',
                            'systemctl stop user-1000.slice', 'loginctl terminate-user example',
                            'systemctl --user exit', 'systemctl stop user.slice',
                            'loginctl kill-user --signal=SIGKILL example',
                            "ssh example.com 'systemctl --user exit'",
                            'python3 ~/.hermes/sync-runner/hermes-sync pause-turns -- systemctl stop user.slice'):
                self.assertIsNotNone(guard.classify('terminal', {'command': command}), command)
            for command in ('systemctl status stop user@1000.service',
                            'systemctl --signal=0 kill user@1000.service',
                            'loginctl -s 0 kill-user example',
                            'systemctl --host example.com status user.slice'):
                self.assertIsNone(guard.classify('terminal', {'command': command}), command)

    def test_nested_hook(self):
        with patch.object(guard, 'protected_processes', return_value={}):
            result = guard._pre_tool_call('tool_call', {'calls': [
                {'name': 'terminal', 'arguments': {'command': 'kill -9 -1'}}]})
            self.assertEqual(result['action'], 'block')
