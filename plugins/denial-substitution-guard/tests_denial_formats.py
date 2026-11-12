"""Denial text formats (cumulative B4), real post-hook result envelopes (B5) and
strict row schema version (B9). Inert data only; nothing is executed.

Envelope shapes follow review-envelopes/RESULT-SHAPES.md (static audit of the
installed core and tailnet/user-terminal plugins): handler results reach
post_tool_call as JSON strings; process_manage poll stores output in
output_preview; tailnet_exec splits stdout/stderr; the registry also admits a
multimodal dict {"_multimodal": true, "content": [...]}.
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
SSM_CMD = 'aws ssm send-command --instance-ids i-0123 --document-name AWS-RunShellScript'


def terminal_result(text, code=254):
    return json.dumps({'output': text, 'exit_code': code, 'error': None})


class DenialFormatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='denial-fmt-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        spec = importlib.util.spec_from_file_location('fmt_guard', HERE / 'guard.py')
        self.g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.g)

    def _fresh(self):
        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory(prefix='denial-fmt-', dir=os.environ.get('TMPDIR'))
        self.root = Path(self.tmp.name)

    def rows(self):
        return self.g.read_ledger(root=self.root).rows

    def held(self, tool, args):
        return self.g.classify(tool, args, root=self.root) is not None

    # B4 -----------------------------------------------------------------
    def test_api_error_code_formats_are_recorded_and_held(self):
        cases = [
            ('terminal', {'command': 'aws s3 cp ./f s3://example-bucket/k'},
             terminal_result('upload failed: ./f to s3://example-bucket/k An error occurred (AccessDenied) when '
                             'calling the PutObject operation: Access Denied', 1),
             's3:PutObject', ('terminal', {'command': 'aws --profile p2 s3api put-object --bucket example-bucket --key z'})),
            ('terminal', {'command': 'aws ec2 terminate-instances --instance-ids i-0abc'},
             terminal_result('An error occurred (UnauthorizedOperation) when calling the TerminateInstances operation: '
                             'You are not authorized to perform this operation. Encoded authorization failure '
                             'message: AbCdEf0123'),
             'ec2:TerminateInstances',
             ('terminal', {'command': 'aws ec2 terminate-instances --instance-ids i-0def --region us-west-2'})),
            ('terminal', {'command': 'aws s3api head-object --bucket example-bucket --key k'},
             terminal_result('An error occurred (403) when calling the HeadObject operation: Forbidden'),
             's3:HeadObject', ('terminal', {'command': 'aws s3api get-object --bucket example-bucket --key k2 out'})),
            ('execute_code', {'code': "import boto3\nboto3.client('sns').publish(TopicArn='t', Message='m')"},
             json.dumps({'status': 'error', 'output': 'Traceback (most recent call last):\n--- stderr ---\n'
                         'botocore.exceptions.ClientError: An error occurred (AuthorizationError) when calling the '
                         'Publish operation: not authorized', 'exit_code': 1}),
             'sns:Publish', ('terminal', {'command': 'aws sns publish --topic-arn t2 --message x'})),
            ('tailnet_exec', {'host': 'example', 'command': 'aws dynamodb delete-table --table-name t'},
             json.dumps({'ok': False, 'ran': True, 'host': 'example', 'rc': 254, 'stdout': '',
                         'stderr': 'An error occurred (AccessDeniedException) when calling the DeleteTable '
                                   'operation: Access denied', 'truncated': False}),
             'dynamodb:DeleteTable', ('execute_code', {'code': "import boto3\nboto3.client('dynamodb').delete_table(TableName='u')"})),
        ]
        for tool, args, result, action, (rtool, rargs) in cases:
            with self.subTest(action=action):
                self._fresh()
                self.g.record_from_result(tool, args, result, root=self.root)
                rows = self.rows()
                self.assertEqual([r['action'] for r in rows], [action])
                self.assertEqual(rows[0]['resource'], 'UNKNOWN')
                self.assertTrue(self.held(rtool, rargs))
                self.assertTrue(self.held(tool, args))

    def test_api_error_controls_not_recorded(self):
        for text in ['An error occurred (ThrottlingException) when calling the SendCommand operation: Rate exceeded',
                     'An error occurred (ExpiredToken) when calling the SendCommand operation: token expired',
                     'An error occurred (UnrecognizedClientException) when calling the SendCommand operation: bad token',
                     'An error occurred (InvalidInstanceId) when calling the SendCommand operation: not found']:
            with self.subTest(text=text):
                self._fresh()
                self.g.record_from_result('terminal', {'command': SSM_CMD}, terminal_result(text), root=self.root)
                self.assertEqual(self.rows(), [])
        # Reader output that merely quotes a denial is not an AWS observation.
        self._fresh()
        self.g.record_from_result('terminal', {'command': 'cat notes.txt'},
                                  terminal_result('An error occurred (AccessDenied) when calling the PutObject '
                                                  'operation: Access Denied'), root=self.root)
        self.assertEqual(self.rows(), [])

    def test_iam_text_line_is_not_double_recorded(self):
        text = 'An error occurred (AccessDeniedException) when calling the SendCommand operation: ' + IAM
        self.g.record_from_result('terminal', {'command': SSM_CMD}, terminal_result(text), root=self.root)
        rows = self.rows()
        self.assertEqual([(r['action'], r['resource']) for r in rows],
                         [('ssm:SendCommand', 'arn:aws:ec2:us-east-1:111122223333:instance/i-0123')])

    def test_api_operation_without_unique_service_is_unknown_action(self):
        cmd = 'aws s3 ls; aws ec2 describe-instances; aws sns publish --topic-arn t --message m'
        self.g.record_from_result('terminal', {'command': cmd}, terminal_result(
            'An error occurred (AccessDenied) when calling the Frobnicate operation: Access Denied'), root=self.root)
        rows = self.rows()
        self.assertEqual([(r['action'], r['resource']) for r in rows], [('UNKNOWN', 'UNKNOWN')])
        self.assertTrue(self.held('terminal', {'command': cmd}))
        self.assertFalse(self.held('terminal', {'command': 'aws sns publish --topic-arn t --message other'}))

    # B5 -----------------------------------------------------------------
    def test_result_envelopes_decoded(self):
        envelopes = {
            'terminal_json': terminal_result(IAM),
            'multimodal_dict': {'_multimodal': True, 'content': [{'type': 'text', 'text': IAM}]},
            'nested_error_message': json.dumps({'error': {'code': 'AccessDenied', 'message': IAM}}),
            'json_array_string': json.dumps(['prefix', IAM]),
            'list_result': ['x', {'text': IAM}],
            'content_key': json.dumps({'content': [{'type': 'text', 'text': IAM}]}),
            'result_key': json.dumps({'result': IAM}),
            'detail_key': json.dumps({'ok': False, 'ran': False, 'error': 'runner failed', 'detail': IAM}),
            'plain_string': 'Error executing tool: ' + IAM,
            'json_in_output': terminal_result(json.dumps({'Error': {'Message': IAM}})),
        }
        for name, result in envelopes.items():
            with self.subTest(envelope=name):
                self._fresh()
                self.g.record_from_result('terminal', {'command': SSM_CMD}, result, root=self.root)
                self.assertEqual([r['action'] for r in self.rows()], ['ssm:SendCommand'])

    def test_process_manage_poll_uses_result_command(self):
        cmd = 'aws ssm send-command --instance-ids i-0123 --comment background'
        for action, key in (('poll', 'output_preview'), ('log', 'output'), ('wait', 'output')):
            with self.subTest(action=action):
                self._fresh()
                result = json.dumps({'session_id': 'proc_1', 'command': cmd, 'status': 'exited', key: IAM})
                self.g.record_from_result('process_manage', {'action': action, 'session_id': 'proc_1'}, result,
                                          root=self.root)
                rows = self.rows()
                self.assertEqual([r['action'] for r in rows], ['ssm:SendCommand'])
                self.assertEqual(rows[0]['fingerprint'], self.g._fingerprint('terminal', {'command': cmd}))
                self.assertTrue(self.held('terminal', {'command': cmd}))
                self.assertTrue(self.held('terminal', {'command': 'aws --profile p2 ssm send-command --instance-ids i-0123'}))

    def test_process_manage_without_aws_command_not_recorded(self):
        result = json.dumps({'session_id': 'proc_1', 'command': 'cat log.txt', 'output_preview': IAM})
        self.g.record_from_result('process_manage', {'action': 'poll', 'session_id': 'proc_1'}, result, root=self.root)
        self.assertEqual(self.rows(), [])

    def test_decoder_is_bounded(self):
        deep = IAM
        for _ in range(40):
            deep = {'x': deep}
        self.g.record_from_result('terminal', {'command': SSM_CMD}, deep, root=self.root)  # must not recurse unbounded
        wide = [IAM] * 100000
        with self.assertRaises(ValueError):
            list(self.g.iter_result_texts(wide))

    # Review-formats B1/B2/B3 -------------------------------------------
    def test_budget_failure_keeps_replay_hold(self):
        big = 'x' * (self.g.MAX_LEDGER_BYTES // 2 + 100)
        cases = [('bash ./aws-job.sh', IAM.replace('ssm:SendCommand', 'ssm:GetCommandInvocation')),
                 ('aws ssm get-command-invocation --command-id c --instance-id i-0123',
                  IAM.replace('ssm:SendCommand', 'ssm:GetCommandInvocation'))]
        for cmd, text in cases:
            with self.subTest(cmd=cmd, shape='half-bound output still parsed'):
                self._fresh()
                self.g.record_from_result('terminal', {'command': cmd},
                                          json.dumps({'output': text + '\n' + big, 'exit_code': 254, 'error': None}),
                                          root=self.root)
                self.assertEqual(self.g.read_ledger(root=self.root).status, 'VALID')
                self.assertTrue(self.held('terminal', {'command': cmd}))
            with self.subTest(cmd=cmd, shape='over-bound output'):
                self._fresh()
                over = [text] + [big, big, big]
                self.g.record_from_result('terminal', {'command': cmd}, over, root=self.root)
                self.assertTrue(self.held('terminal', {'command': cmd}))
                self.assertTrue(any(r.get('observation', '').startswith('incomplete') for r in self.rows()))

    def test_unhealthy_states_keep_durable_rows(self):
        cmd = 'bash ./aws-job.sh'
        read = 'aws ssm get-command-invocation --command-id c --instance-id i-0123'
        self.g.record_from_result('terminal', {'command': cmd}, terminal_result(IAM), root=self.root)
        self.g.record_from_result('terminal', {'command': read},
                                  terminal_result(IAM.replace('ssm:SendCommand', 'ssm:GetCommandInvocation')),
                                  root=self.root)
        for name, damage in (('cap marker', lambda: (self.root / 'denial-ledger.cap').write_text('')),
                             ('latch', lambda: self.g._UNHEALTHY.__setitem__(str(self.root.resolve()), 'test')),
                             ('invalid line', lambda: open(self.root / 'denial-ledger.jsonl', 'a').write('{bad\n'))):
            with self.subTest(state=name):
                damage()
                self.assertEqual(self.g.read_ledger(root=self.root).status, 'UNHEALTHY')
                self.assertTrue(self.held('terminal', {'command': cmd}))
                self.assertTrue(self.held('terminal', {'command': read}))
                self.assertTrue(self.held('terminal', {'command': 'aws --region us-west-2 ssm get-command-invocation --command-id d --instance-id i-0123'}))
        self.g._UNHEALTHY.pop(str(self.root.resolve()), None)

    def test_capacity_keeps_prior_and_overflow_rows(self):
        old_rows = self.g.MAX_LEDGER_ROWS
        self.g.MAX_LEDGER_ROWS = 3
        self.addCleanup(setattr, self.g, 'MAX_LEDGER_ROWS', old_rows)
        first = 'bash ./aws-job.sh'
        self.g.record_from_result('terminal', {'command': first}, terminal_result(IAM), root=self.root)
        for n in range(4):
            self.g.record_from_result('terminal', {'command': f'aws s3api put-object --bucket b --key {n}'},
                                      json.dumps({'message': 'An error occurred (AccessDenied) when calling the '
                                                  'PutObject operation: Access Denied'}), root=self.root)
        later = 'aws ssm get-command-invocation --command-id c --instance-id i-0123'
        self.g.record_from_result('terminal', {'command': later},
                                  terminal_result(IAM.replace('ssm:SendCommand', 'ssm:GetCommandInvocation')),
                                  root=self.root)
        self.assertEqual(self.g.read_ledger(root=self.root).status, 'UNHEALTHY')
        self.assertTrue(self.held('terminal', {'command': first}))
        self.assertTrue(self.held('terminal', {'command': later}))  # overflow sample, this process

    def test_api_attribution_uses_candidate_service(self):
        self.g.record_from_result('terminal', {'command': 'aws cloudformation deploy --template-file x --stack-name y'},
                                  json.dumps({'output': 'An error occurred (AccessDenied) when calling the PutObject '
                                              'operation: Access Denied'}), root=self.root)
        self.assertIn('s3:PutObject', [r['action'] for r in self.rows()])
        self.assertTrue(self.held('terminal', {'command': 'aws s3api put-object --bucket b --key z'}))
        self.assertFalse(self.held('terminal', {'command': 'aws s3api list-buckets'}))

    # B9 -----------------------------------------------------------------
    def test_row_version_is_strict_int(self):
        base = {'action': 'ssm:SendCommand', 'resource': 'UNKNOWN', 'principal': 'p', 'at': 1.0}
        for v in (True, 1.0, False, 0.0, '1'):
            with self.subTest(v=v):
                with self.assertRaises(ValueError):
                    self.g._validate(dict(base, v=v))
        self.g._validate(dict(base, v=0))


if __name__ == '__main__':
    unittest.main()
