"""Inert comment/lexical binding regressions; no fixture code is executed."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
INSTANCE = 'i-0abc123def4567890'
COMMAND = 'aws ssm send-command --instance-ids ' + INSTANCE
DENIAL = ('User: arn:aws:sts::111122223333:assumed-role/example/session '
          'is not authorized to perform: ssm:SendCommand on resource: '
          'arn:aws:ec2:us-west-2:111122223333:instance/' + INSTANCE + ' with an explicit deny')

class LexicalRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='m2-scopes-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        spec = importlib.util.spec_from_file_location('scope_candidate', HERE / 'guard.py')
        self.g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.g)
        self.g.record_from_result('terminal', {'command': COMMAND}, DENIAL, root=self.root)

    def check(self, text, hold, python=False):
        result = self.g.classify('execute_code' if python else 'terminal',
                                {'code' if python else 'command': text}, root=self.root)
        self.assertEqual(result is not None, hold, repr(text))

    def test_comment_state(self):
        self.check("# '\necho \"$(" + COMMAND + ")\"", True)
        self.check('true # $(' + COMMAND + ')', False)
        self.check('echo escaped\\ #$(' + COMMAND + ')', True)
        self.check("echo ''#$(" + COMMAND + ')', True)
        self.check("echo '#$(" + COMMAND + ")'", False)

    def test_annotated_sdk(self):
        for instance, hold in ((INSTANCE, True), ('i-0fff999eee8887776', False)):
            with self.subTest(instance=instance):
                self.check("import boto3\ns: object = boto3.client('ssm')\ns.send_command(InstanceIds=['" + instance + "'])", hold, True)

    def test_separate_function_bindings(self):
        code = ("import boto3\ndef unused():\n s=boto3.client('ssm')\n"
                "class Local:\n def send_command(self, **kwargs): return 'local'\n"
                "def active():\n s=Local()\n s.send_command(InstanceIds=['" + INSTANCE + "'])\nactive()")
        self.check(code, False, True)

    def test_outer_binding_and_shadow(self):
        prefix = "import boto3\ns=boto3.client('ssm')\n"
        self.check(prefix + "def active():\n s.send_command(InstanceIds=['" + INSTANCE + "'])\nactive()", True, True)
        self.check(prefix + "def active(s):\n s.send_command(InstanceIds=['" + INSTANCE + "'])", False, True)

    def test_method_skips_class_namespace(self):
        call = "s.send_command(InstanceIds=['" + INSTANCE + "'])"
        for module, local, hold in (("boto3.client('ssm')", 'object()', True),
                                    ('object()', "boto3.client('ssm')", False)):
            with self.subTest(module=module):
                self.check('import boto3\ns=' + module + '\nclass C:\n s=' + local +
                           '\n def f(self):\n  ' + call + '\nC().f()', hold, True)

    def test_declaration_store_ownership(self):
        call = "s.send_command(InstanceIds=['" + INSTANCE + "'])"
        self.check("import boto3\ns=object()\ndef setup():\n global s\n s=boto3.client('ssm')\nsetup()\n" + call, True, True)
        self.check("import boto3\ndef outer():\n s=object()\n def setup():\n  nonlocal s\n  s=boto3.client('ssm')\n setup()\n " + call + '\nouter()', True, True)

    def test_definition_time_expressions(self):
        call = "s.send_command(InstanceIds=['" + INSTANCE + "'])"
        prefix = "import boto3\ns=boto3.client('ssm')\n"
        for definition in ('def f(s=' + call + '): pass',
                           'def f(*, s=' + call + '): pass',
                           'f = lambda s=' + call + ': None',
                           '@' + call + '\ndef f(s): pass',
                           'class C(' + call + '): pass'):
            with self.subTest(definition=definition): self.check(prefix + definition, True, True)

    def test_inner_substitution_comment_delimiters(self):
        for comment in (')', "'", '$(unexecuted)'):
            with self.subTest(comment=comment):
                self.check('echo "$(true # ' + comment + '\n' + COMMAND + '\n)"', True)

if __name__ == '__main__':
    unittest.main()
