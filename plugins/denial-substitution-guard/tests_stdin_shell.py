"""review-cumulative-2 B1: a shell that reads its program from stdin, a
here-string, process substitution or eval of command output runs the same
denied operation. Inert strings only; nothing is executed.

The denial is recorded from a plain CLI invocation and the re-attempts use
different text, so exact-fingerprint replay cannot satisfy these tests.
"""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
EC2 = ('User: arn:aws:sts::111122223333:assumed-role/example/s is not authorized to perform: '
       'ec2:TerminateInstances on resource: arn:aws:ec2:us-east-1:111122223333:instance/i-0abc with an explicit deny')
CMD = 'aws ec2 terminate-instances --instance-ids i-0abc'

FED = [
    f"bash <<< '{CMD}'",
    f'bash <<< "{CMD}"',
    f"bash <<<'{CMD}'",
    f"sh <<< '{CMD}'",
    f"zsh <<< '{CMD}'",
    f"dash <<< '{CMD}'",
    f"bash -s <<< '{CMD}'",
    f"echo '{CMD}' | bash",
    f"echo {CMD} | sh",
    f"printf '%s' '{CMD}' | sh",
    f"printf '%s\\n' '{CMD}' | bash -s",
    f"cat <<< '{CMD}' | bash",
    f"echo '{CMD}' | sudo bash",
    f"eval \"$(echo '{CMD}')\"",
    f"eval $(printf '%s' '{CMD}')",
    f"bash <(echo '{CMD}')",
    f"source <(echo '{CMD}')",
    f". <(echo '{CMD}')",
    f"bash -lc \"echo '{CMD}' | bash\"",
    f"cd /tmp && echo '{CMD}' | bash",
]

PRINT_ONLY = [
    f"echo '{CMD}'",
    f"printf '%s' '{CMD}'",
    f"echo '{CMD}' > notes.txt",
    f"grep -c '{CMD}' notes.txt",
]


def result(text, code=254):
    return json.dumps({'output': text, 'exit_code': code, 'error': None})


class StdinShellTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='denial-stdin-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        spec = importlib.util.spec_from_file_location('stdin_guard', HERE / 'guard.py')
        self.g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.g)
        self.g.record_from_result('terminal', {'command': CMD}, result(EC2), root=self.root)
        self.assertTrue(self.g.read_ledger(root=self.root).rows)

    def held(self, tool, args):
        return self.g.classify(tool, args, root=self.root)

    def test_stdin_fed_shells_hold_on_every_transport(self):
        for cmd in FED:
            for tool, args in [('terminal', {'command': cmd}), ('mcp__terminal', {'command': cmd}),
                               ('tailnet_exec', {'host': 'example', 'command': cmd}),
                               ('run_in_user_terminal', {'command': cmd}),
                               ('tool_call', {'calls': [{'name': 'terminal', 'arguments': {'command': cmd}}]})]:
                with self.subTest(tool=tool, cmd=cmd):
                    self.assertIsNotNone(self.held(tool, args))

    def test_python_launching_fed_shell_holds(self):
        code = f"import subprocess; subprocess.run(\"echo '{CMD}' | bash\", shell=True)"
        self.assertIsNotNone(self.held('execute_code', {'code': code}))

    def test_other_instance_through_fed_shell_is_not_a_new_identity(self):
        # Different instance ids are still the same operation; ec2 rows hold
        # conservatively (only the SSM adapter can prove another target).
        self.assertIsNotNone(self.held('terminal', {'command': "bash <<< 'aws ec2 terminate-instances --instance-ids i-0fff'"}))

    def test_print_only_controls_stay_allowed(self):
        for cmd in PRINT_ONLY:
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.held('terminal', {'command': cmd}))

    def test_fed_shell_without_aws_text_stays_allowed(self):
        for cmd in ["echo 'ls -la' | bash", "bash <<< 'git status'", "bash <(echo date)",
                    "aws ec2 describe-instances | bash -c 'cat'"]:
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.held('terminal', {'command': cmd}))


if __name__ == '__main__':
    unittest.main()
