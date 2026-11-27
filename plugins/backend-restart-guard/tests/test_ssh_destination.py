"""Destination-slot regressions: never execute SSH or a supplied signal payload."""
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ssh_destination_guard_test', HERE / '__init__.py')
assert spec and spec.loader
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class SSHDestination(unittest.TestCase):
    def test_option_destination_refused_before_hook_discovery(self):
        for destination in ('-oProxyCommand=fixture', '-Efixture.log', '-Ffixture.conf', '-'):
            with self.subTest(destination=destination), patch.object(guard, 'protected_processes') as lookup:
                command = 'ssh -- ' + shlex.quote(destination) + ' kill 4242'
                verdict = guard._pre_tool_call('terminal', {'command': command})
                self.assertIsNotNone(verdict)
                self.assertEqual(verdict['action'], 'block')
                self.assertIn('unsafe SSH destination', verdict['message'])
                lookup.assert_not_called()

    def test_option_destination_cannot_spawn_inspection(self):
        for destination in ('-oProxyCommand=fixture', '-Efixture.log', '-Ffixture.conf', '-'):
            with self.subTest(destination=destination), patch.object(guard.subprocess, 'run') as run:
                self.assertEqual(guard.protected_processes(([], destination)), {})
                run.assert_not_called()

    def test_valid_destination_has_option_terminator(self):
        rows = {4242: {'ppid': 1, 'pgid': 4242, 'args': 'hermes gateway run'}}
        for destination in ('example.com', 'you@example.com', '[::1]'):
            with self.subTest(destination=destination), patch.object(
                    guard.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(rows))) as run:
                self.assertEqual(guard.protected_processes((['-p', '2222'], destination)), rows)
                self.assertEqual(run.call_args.args[0][-3:], ['--', destination, 'python3 -'])


if __name__ == '__main__':
    unittest.main()
