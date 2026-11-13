"""Gate findings from the external immutable49/native26 run on 8b171b09
(review-evidence denial-substitution-m2-run19/measure-m2.log). Inert strings only.

1. native26 test_other_resource_allowed: an explicit literal SSM instance id that
   is not hex (`i-other`) cannot be the denied instance; M1 allowed it.
2. original49 seam: classify(root=X) must not resolve the account root (the
   explicit root is the protected root), so tests never touch real-home state.
"""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
INST = 'i-0abc123def4567890'
DENY = ('User: arn:aws:sts::111122223333:assumed-role/example/s is not authorized to perform: '
        f'ssm:SendCommand on resource: arn:aws:ec2:us-west-2:111122223333:instance/{INST} with an explicit deny')
CMD = f'aws ssm send-command --instance-ids {INST} --document-name AWS-RunShellScript'


class SeamViolation(BaseException):
    pass


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='denial-gates-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        spec = importlib.util.spec_from_file_location('gates_guard', HERE / 'guard.py')
        self.g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.g)

    def record(self):
        self.g.record_from_result('terminal', {'command': CMD},
                                  json.dumps({'output': DENY, 'exit_code': 254, 'error': None}), root=self.root)

    def held(self, cmd, tool='terminal'):
        return self.g.classify(tool, {'command': cmd}, root=self.root)

    def test_other_literal_instance_ids_are_allowed(self):
        self.record()
        for other in ('i-other', 'i-0fff999eee8887776', 'i-ZZZ1'):
            with self.subTest(other=other):
                self.assertIsNone(self.held(CMD.replace(INST, other)))
                code = (f"import boto3\nboto3.client('ssm').send_command(InstanceIds=['{other}'], "
                        "DocumentName='AWS-RunShellScript')")
                self.assertIsNone(self.g.classify('execute_code', {'code': code}, root=self.root))

    def test_denied_instance_in_any_case_or_mixed_list_is_held(self):
        self.record()
        for cmd in (CMD, CMD.replace(INST, INST.upper().replace('I-', 'i-')),
                    CMD.replace(INST, f'i-other {INST}'), CMD.replace(INST, 'i-0abc*'),
                    CMD.replace(INST, '"$ID"')):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(self.held(cmd))

    def test_abbreviated_selectors_and_targets_are_unknown(self):
        # review-cumulative-2/gates-delta non-delta finding: argparse prefixes.
        self.record()
        other = CMD.replace(INST, 'i-0fff999eee8887776')
        for cmd in (other + ' --target Key=tag:x,Values=y', other + ' --cli-input-js file://r.json',
                    other + ' --cli-input-y file://r.yaml', other + ' --t Key=k,Values=v',
                    other + f' --instance-i {INST}', other + f' --instance-id={INST}'):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(self.held(cmd))
        for code in ("import boto3\nboto3.client('ssm').send_command(InstanceIds=['i-0fff999eee8887776'], "
                     "Targets=[{'Key': 'tag:x', 'Values': ['y']}], DocumentName='d')",
                     "import boto3\nkw = {}\nboto3.client('ssm').send_command(InstanceIds=['i-0fff999eee8887776'], "
                     "DocumentName='d', **kw)"):
            with self.subTest(code=code):
                self.assertIsNotNone(self.g.classify('execute_code', {'code': code}, root=self.root))

    def test_explicit_root_never_resolves_account_root(self):
        def boom(*a, **k):
            raise SeamViolation('un-rooted resolve_root()')
        self.g.resolve_root = boom
        self.record()
        self.assertIsNotNone(self.held(CMD))
        for cmd in (f'rm -rf {self.root}', f'rm -rf {self.root.parent}', f'mv {self.root} /elsewhere',
                    f'rm {self.root}/denial-ledger.jsonl', 'rm ~/.hermes/denial-ledger.jsonl'):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(self.held(cmd))
        self.assertIsNone(self.held('ls -la /var/log'))


if __name__ == '__main__':
    unittest.main()
