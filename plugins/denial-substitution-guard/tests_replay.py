"""Exact-denial replay regressions. Commands are inert data, never executed."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
DENIAL = ('User: arn:aws:iam::111122223333:role/example is not authorized to perform: '
          'ssm:SendCommand on resource: arn:aws:ec2:us-east-1:111122223333:instance/i-0123 with an explicit deny')

class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='denial-replay-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        spec = importlib.util.spec_from_file_location('replay_guard', HERE / 'guard.py')
        self.g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.g)

    def test_opaque_exact_replay_across_shell_entrypaths(self):
        cmd = "token-broker exec --scope example/write -- python3 /example/script.py"
        self.g.record_from_result('terminal', {'command': cmd}, DENIAL, root=self.root)
        for tool, args in [('terminal', {'command': cmd}), ('tailnet_exec', {'host': 'example', 'command': cmd}),
                           ('run_in_user_terminal', {'command': cmd}),
                           ('tool_call', {'calls': [{'name': 'terminal', 'arguments': {'command': cmd}}]})]:
            with self.subTest(tool=tool):
                self.assertIsNotNone(self.g.classify(tool, args, now=9e18, root=self.root))
        self.assertIsNone(self.g.classify('terminal', {'command': 'git status'}, root=self.root))

    def test_exact_replay_independent_of_iam_cli_mapping(self):
        cmd = 'aws s3api put-object --bucket example --key k --body f'
        result = DENIAL.replace('ssm:SendCommand', 's3:PutObject').replace(
            'arn:aws:ec2:us-east-1:111122223333:instance/i-0123', 'arn:aws:s3:::example/k')
        self.g.record_from_result('terminal', {'command': cmd}, result, root=self.root)
        self.assertIsNotNone(self.g.classify('terminal', {'command': cmd}, root=self.root))

    def test_opaque_python_replay(self):
        args = {'code': "import boto3\nrun_denied_operation()"}
        self.g.record_from_result('execute_code', args, DENIAL, root=self.root)
        self.assertIsNotNone(self.g.classify('execute_code', args, root=self.root))
        self.assertIsNone(self.g.classify('execute_code', {'code': 'print(1)'}, root=self.root))

    def test_distinct_recorded_fingerprints_survive_dedup(self):
        commands = ['aws ssm send-command --instance-ids i-0123',
                    'token-broker exec --scope example/write -- python3 /example/script.py']
        for cmd in commands + commands:
            self.g.record_from_result('terminal', {'command': cmd}, DENIAL, root=self.root)
        self.assertEqual(len(self.g.read_ledger(root=self.root).rows), 2)
        for cmd in commands:
            self.assertIsNotNone(self.g.classify('terminal', {'command': cmd}, root=self.root))

    def test_file_path_only_is_not_an_executable_fingerprint(self):
        args = {'path': '/example/aws/notes.md', 'content': 'first'}
        self.g.record_from_result('write_file', args, DENIAL, root=self.root)
        self.assertIsNone(self.g.classify('write_file', dict(args, content='different'), root=self.root))

    def test_distinct_fingerprints_respect_existing_cap(self):
        self.g.MAX_LEDGER_ROWS = 2
        for n in range(4):
            self.g.record_from_result('terminal', {'command': 'aws ssm send-command --comment ' + str(n)}, DENIAL, root=self.root)
        self.assertTrue((self.root / 'denial-ledger.cap').exists())
        self.assertEqual(len((self.root / 'denial-ledger.jsonl').read_text().splitlines()), 2)

    def test_empty_extraction_does_not_deny_unrelated_tools(self):
        self.g.record_from_result('unknown_tool', {'x': 1}, DENIAL, root=self.root)
        for tool, args in [('other_tool', {'x': 2}), ('terminal', {}), ('execute_code', {})]:
            with self.subTest(tool=tool):
                self.assertIsNone(self.g.classify(tool, args, root=self.root))

if __name__ == '__main__':
    unittest.main()
