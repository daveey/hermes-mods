"""Cumulative-review documented-class items B6 (root-directory / glob ledger
tamper), B7 (additional transports and SDK shapes) and B8 (tool-name prefixes,
legacy nested tool_call shapes). Inert strings only; nothing is executed.

Each transport case records a denial from one invocation and classifies a
different one (other text/transport), so exact-fingerprint replay cannot pass.
"""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
IAM = ('User: arn:aws:sts::111122223333:assumed-role/example/s is not authorized to perform: '
       'ssm:SendCommand on resource: arn:aws:ec2:us-east-1:111122223333:instance/i-0123 with an explicit deny')
EC2 = ('User: arn:aws:sts::111122223333:assumed-role/example/s is not authorized to perform: '
       'ec2:TerminateInstances on resource: arn:aws:ec2:us-east-1:111122223333:instance/i-0abc with an explicit deny')
SSM = 'aws ssm send-command --instance-ids i-0123 --document-name AWS-RunShellScript'


def result(text, code=254):
    return json.dumps({'output': text, 'exit_code': code, 'error': None})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='denial-b678-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        spec = importlib.util.spec_from_file_location('b678_guard', HERE / 'guard.py')
        self.g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.g)

    def held(self, tool, args):
        return self.g.classify(tool, args, root=self.root)

    def record(self, text=IAM, cmd=SSM):
        self.g.record_from_result('terminal', {'command': cmd}, result(text), root=self.root)
        self.assertTrue(self.g.read_ledger(root=self.root).rows)


class TransportTests(Base):
    def test_shell_transports_hold_denied_operation(self):
        self.record()
        cases = [
            "ssh bastion 'aws ssm send-command --instance-ids i-0123 --document-name d'",
            'ssh -i key.pem ec2-user@h aws ssm send-command --instance-ids i-0123',
            'python3 -m awscli ssm send-command --instance-ids i-0123',
            'python -m awscli --region us-west-2 ssm send-command --instance-ids i-0123',
            'uv run --with awscli aws ssm send-command --instance-ids i-0123',
            'uvx --from awscli aws ssm send-command --instance-ids i-0123',
            'pipx run --spec awscli aws ssm send-command --instance-ids i-0123',
            'python3 -c "import boto3; boto3.client(\'ssm\').send_command(InstanceIds=[\'i-0123\'], DocumentName=\'d\')"',
            # review-b678 B1/B2: a python word never hides a later aws; value options
            'env -u python aws ssm send-command --instance-ids i-0123 --document-name d',
            'sudo -u python aws ssm send-command --instance-ids i-0123',
            'timeout -s python 5 aws ssm send-command --instance-ids i-0123',
            'python3 -W ignore -m awscli ssm send-command --instance-ids i-0123',
            'python3 -X dev -m awscli ssm send-command --instance-ids i-0123',
            'uv run --python python3 aws ssm send-command --instance-ids i-0123',
        ]
        for cmd in cases:
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(self.held('terminal', {'command': cmd}))

    def test_python_transports_hold(self):
        self.record(EC2, 'aws ec2 terminate-instances --instance-ids i-0abc')
        cases = [
            "import boto3\nboto3.resource('ec2').Instance('i-0abc').terminate()",
            "import boto3\nec2 = boto3.Session().resource('ec2')\nfor i in ec2.instances.all(): i.terminate()",
            "from hermes_tools import terminal\nc = 'aws ec2 terminate-' + x\nterminal(c)",
            "from awscli.clidriver import create_clidriver\ncreate_clidriver().main(['ec2', 'terminate-instances'])",
            "import runpy, sys\nsys.argv = ['aws', 'ec2']\nrunpy.run_module('awscli')",
        ]
        for code in cases:
            with self.subTest(code=code):
                self.assertIsNotNone(self.held('execute_code', {'code': code}))

    def test_transport_controls_stay_allowed(self):
        self.record()
        controls = [
            ('terminal', {'command': "ssh bastion 'aws ssm describe-instance-information'"}),
            ('terminal', {'command': 'python3 -m awscli s3 ls'}),
            ('terminal', {'command': 'uv run --with pytest pytest -q'}),
            ('terminal', {'command': 'ssh h uptime'}),
            ('execute_code', {'code': "import boto3\nboto3.resource('s3').Bucket('b').objects.all()"}),
            ('terminal', {'command': 'python3 -c "print(1)"'}),
        ]
        for tool, args in controls:
            with self.subTest(args=args):
                self.assertIsNone(self.held(tool, args))


class ToolNameTests(Base):
    def test_prefixed_and_legacy_nested_shapes(self):
        self.record()
        cmd = {'command': 'aws --region us-west-2 ssm send-command --instance-ids i-0123'}
        cases = [
            ('mcp__terminal', cmd),
            ('mcp__execute_code', {'code': "from hermes_tools import terminal\nterminal('aws ssm send-command --instance-ids i-0123')"}),
            ('mcp__process_manage', {'action': 'submit', 'data': 'aws ssm send-command --instance-ids i-0123'}),
            ('tool_call', {'calls': [{'name': 'mcp__terminal', 'arguments': cmd}]}),
            ('tool_call', {'calls': [{'name': 'terminal', 'arguments': json.dumps(cmd)}]}),
            ('tool_call', {'name': 'terminal', 'arguments': cmd}),
            ('mcp__tool_call', {'name': 'terminal', 'arguments': json.dumps(cmd)}),
            ('mcp__tailnet_exec', {'host': 'h', 'command': cmd['command']}),
            ('mcp__run_in_user_terminal', cmd),
        ]
        for tool, args in cases:
            with self.subTest(tool=tool, args=args):
                self.assertIsNotNone(self.held(tool, args))

    def test_deep_nesting_fails_closed_without_crash(self):
        self.record()
        inner = {'name': 'terminal', 'arguments': {'command': 'aws --region us-west-2 ssm send-command --instance-ids i-0123'}}
        for depth in (5, 16, 40):
            env = inner
            for _ in range(depth):
                env = {'name': 'tool_call', 'arguments': env}
            with self.subTest(depth=depth):
                self.assertIsNotNone(self.held('tool_call', env))
        # Beyond the nesting bound or JSON decode depth: held unconditionally
        # (padding/quoting cannot hide aws or a protected name), never a crash.
        pad = {'pad': 'x' * 300000, 'command': "'a''ws' ssm send-command --instance-ids i-0123"}
        env = {'name': 'terminal', 'arguments': pad}
        for _ in range(17):
            env = {'name': 'tool_call', 'arguments': env}
        self.assertIsNotNone(self.held('tool_call', env))
        bomb = {'name': 'terminal', 'arguments': '[' * 100000 + '0' + ']' * 100000}
        self.assertIsNotNone(self.held('tool_call', bomb))
        self.assertTrue(self.g.is_mutating('tool_call', bomb))
        # The depth bound is explicit, not the C stack: Python 3.12+ decodes the
        # bomb above under a large `ulimit -s`, and 600 levels decode anywhere.
        deep = {'name': 'terminal', 'arguments': '[' * 600 + '0' + ']' * 600}
        self.assertIsNotNone(self.held('tool_call', deep))
        self.assertTrue(self.g.is_mutating('tool_call', deep))
        quoted = {'name': 'terminal', 'arguments': json.dumps({'command': 'ls', 'note': '[' * 600})}
        self.assertFalse(self.g.is_mutating('tool_call', quoted))  # brackets inside strings are not depth

    def test_prefixed_tamper_is_checked(self):
        for tool, args in (('mcp__terminal', {'command': 'rm ~/.hermes/denial-ledger.jsonl'}),
                           ('tool_call', {'name': 'terminal', 'arguments': {'command': 'rm ~/.hermes/denial-ledger.jsonl'}})):
            with self.subTest(tool=tool):
                self.assertIn('ledger/grant mutation', self.held(tool, args) or '')


class RootTamperTests(Base):
    def tamper(self, cmd, tool='terminal'):
        args = {'command': cmd} if tool == 'terminal' else {'code': cmd}
        return 'ledger/grant mutation' in (self.held(tool, args) or '')

    def test_root_directory_and_glob_mutations_hold(self):
        cases = [
            'rm -rf ~/.hermes',
            'rm -rf "$HOME/.hermes/"',
            'mv ~/.hermes ~/.hermes.old && mkdir ~/.hermes',
            'rm -rf $HERMES_HOME',
            'rm ~/.hermes/*.jsonl',
            'rm -f ~/.hermes/denial*',
            'cd ~/.hermes && rm *.jsonl *.cap',
            'cd "$HERMES_HOME"; rm -f ./*',
            'chmod 000 ~/.hermes',
            'find ~/.hermes -maxdepth 1 -name "*.jsonl" -delete',
            'rm -rf ~',
            'rsync -a --delete empty/ ~/.hermes/',
        ]
        for cmd in cases:
            with self.subTest(cmd=cmd):
                self.assertTrue(self.tamper(cmd))

    def test_python_root_mutations_hold(self):
        cases = [
            "import shutil, os\nshutil.rmtree(os.path.expanduser('~/.hermes'))",
            "import os\nos.rename(os.path.expanduser('~/.hermes'), '/tmp/x')",
            "import glob, os\nfor p in glob.glob(os.path.expanduser('~/.hermes/*.jsonl')): os.remove(p)",
        ]
        for code in cases:
            with self.subTest(code=code):
                self.assertTrue(self.tamper(code, 'execute_code'))

    def test_new_scanners_are_linear(self):
        import time
        texts = ['chdir(' * 40000, 'cd ' + 'a' * 250000, ('cd x; ' * 40000)[:262000],
                 'ssh h ' + ' '.join(['"aws x"'] * 30000), 'python3 ' + '-u ' * 80000 + '-c pass']
        for text in texts:
            with self.subTest(n=text[:12]):
                start = time.thread_time()
                self.held('terminal', {'command': text})
                self.assertLess(time.thread_time() - start, 5.0)

    def test_configured_root_path_mutations_hold(self):
        r = str(self.root)
        for cmd in (f'mv {r} {r}.old && mkdir {r}', f'rm -rf {r}', f'cd {r} && rm -f *.jsonl *.cap',
                    f'rm -f "{r}"/*', f'rm -rf {self.root.parent}'):
            with self.subTest(cmd=cmd):
                self.assertTrue(self.tamper(cmd))
        for cmd in (f'rm -rf {r}-other', f'cd {r}.old && rm -f *.jsonl', f'ls {r}'):
            with self.subTest(control=cmd):
                self.assertFalse(self.tamper(cmd))

    def test_root_controls_stay_allowed(self):
        controls = [
            'cat ~/.hermes/denial-ledger.jsonl',
            'ls -la ~/.hermes',
            'rm -rf ~/.hermes/cache/scratch/t_x',
            'rm build/*.jsonl',
            'cd build && rm -rf *',
            'mkdir -p ~/.hermes/skills/x',
            'cp a.md ~/.hermes/skills/x/',
            'rm -rf /tmp/work/.hermes-test',
        ]
        for cmd in controls:
            with self.subTest(cmd=cmd):
                self.assertFalse(self.tamper(cmd))


if __name__ == '__main__':
    unittest.main()
