"""Reviewer regressions: all command strings are inert fixture data."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('m2_fixture', Path(__file__).with_name('tests_m2.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class ReviewedRegressions(fixture.M2Behavioral):
    def test_lexical_boundaries(self):
        for prefix in ('true\n', 'true\n ', 'true # comment\n', 'true;'):
            with self.subTest(prefix=prefix):
                self.assertIsNotNone(self.g.classify('terminal', {'command': prefix + fixture.COMMAND}, root=self.root))
        for punct in (';', '(', '\n'):
            with self.subTest(punct=punct):
                self.assertIsNone(self.g.classify('terminal', {'command': "printf '%s' '" + punct + "' " + fixture.COMMAND}, root=self.root))

    def test_reassigned_sdk_binding_cannot_erase_denial(self):
        code = "import boto3\ns=boto3.client('ssm')\ns.send_command(InstanceIds=['" + fixture.INSTANCE + "'])\ns=boto3.client('ec2')"
        self.assertIsNotNone(self.g.classify('execute_code', {'code': code}, root=self.root))

    def test_document_and_wildcard_not_disproved_by_instance(self):
        for resource in ('arn:aws:ssm:us-west-2:111122223333:document/AWS-RunShellScript', '*'):
            with self.subTest(resource=resource):
                self.g.record_from_result('terminal', {'command': fixture.COMMAND}, fixture.DENIAL.replace(fixture.RESOURCE, resource), root=self.root)
                different = fixture.COMMAND.replace(fixture.INSTANCE, 'i-0fff999eee8887776') + ' --document-name AWS-RunShellScript'
                self.assertIsNotNone(self.g.classify('terminal', {'command': different}, root=self.root))

    def test_parser_failure_has_nonthrowing_hold(self):
        for tool, args in [('terminal', {'command': fixture.COMMAND + " '"}), ('terminal', {'command': fixture.COMMAND + ' ' * 262145}), ('execute_code', {'code': 'import boto3\n('})]:
            with self.subTest(tool=tool):
                self.assertTrue(self.g.is_mutating(tool, args))
                self.assertIsNotNone(self.g.classify(tool, args, root=self.root))

    def test_unrelated_globs_and_docs(self):
        for command in ("rm -f '/example/*'", 'rm -f /unrelated/example/*', "printf '%s' 'rm -f /example/*'"):
            with self.subTest(command=command):
                self.assertIsNone(self.g.classify('terminal', {'command': command}, root=self.root))

    def test_later_scalar_assignment_does_not_erase_target(self):
        self.assertIsNotNone(self.g.classify('terminal', {'command': 'F=denial-; printf x > ~/.hermes/${F}grants.json; F=other'}, root=self.root))

    def test_reader_prefix_records_denial(self):
        root = self.root / 'new-ledger'
        self.g.record_from_result('terminal', {'command': 'echo status; ' + fixture.COMMAND}, fixture.DENIAL, root=root)
        self.assertEqual(len(self.g.read_ledger(root).rows), 1)
