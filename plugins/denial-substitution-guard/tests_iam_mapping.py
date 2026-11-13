"""CLI/SDK -> IAM action mapping and AWS CLI option placement (cumulative B2/B3).

Every check records a denial with one invocation and classifies a *different*
invocation string (other profile/region/key/transport), so the exact-replay
fingerprint cannot satisfy it: only the semantic mapping can. Commands are inert
data, never executed.
"""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ACCOUNT = '111122223333'


def denial(action, resource):
    return (f'An error occurred (AccessDeniedException): User: arn:aws:iam::{ACCOUNT}:role/example is not '
            f'authorized to perform: {action} on resource: {resource} with an explicit deny')


S3_OBJ = 'arn:aws:s3:::example-bucket/k'
CASES = [
    # (iam action, resource, recorded invocation, changed replay that must hold)
    ('s3:PutObject', S3_OBJ, ('terminal', 'aws s3api put-object --bucket example-bucket --key k --body f'),
     ('terminal', 'aws --profile other s3api put-object --bucket example-bucket --key k2 --body g')),
    ('s3:PutObject', S3_OBJ, ('terminal', 'aws s3 cp f s3://example-bucket/k'),
     ('terminal', 'aws s3 cp --region us-west-2 g s3://example-bucket/k2')),
    ('s3:PutObject', S3_OBJ, ('terminal', 'aws s3api put-object --bucket example-bucket --key k'),
     ('terminal', 'aws s3 sync ./d s3://example-bucket/')),
    ('s3:PutObject', S3_OBJ, ('terminal', 'aws s3 cp f s3://example-bucket/k'),
     ('execute_code', "import boto3\nboto3.client('s3').upload_file('f', 'example-bucket', 'k')")),
    ('s3:DeleteObject', S3_OBJ, ('terminal', 'aws s3api delete-object --bucket example-bucket --key k'),
     ('terminal', 'aws s3 rm s3://example-bucket/k --profile p2')),
    ('s3:DeleteObject', S3_OBJ, ('terminal', 'aws s3 rm s3://example-bucket/k'),
     ('execute_code', "import boto3\nboto3.client('s3').delete_objects(Bucket='example-bucket', Delete={})")),
    ('lambda:InvokeFunction', f'arn:aws:lambda:us-east-1:{ACCOUNT}:function:fn',
     ('terminal', 'aws lambda invoke --function-name fn out.json'),
     ('terminal', 'aws --region us-east-1 lambda invoke --function-name fn --payload x o2.json')),
    ('lambda:InvokeFunction', f'arn:aws:lambda:us-east-1:{ACCOUNT}:function:fn',
     ('terminal', 'aws lambda invoke --function-name fn out.json'),
     ('execute_code', "import boto3\nboto3.client('lambda').invoke(FunctionName='fn')")),
    ('states:StartExecution', f'arn:aws:states:us-east-1:{ACCOUNT}:stateMachine:sm',
     ('terminal', 'aws stepfunctions start-execution --state-machine-arn a'),
     ('terminal', 'AWS_PROFILE=p2 aws stepfunctions start-execution --state-machine-arn b')),
    ('states:StartExecution', f'arn:aws:states:us-east-1:{ACCOUNT}:stateMachine:sm',
     ('terminal', 'aws stepfunctions start-execution --state-machine-arn a'),
     ('execute_code', "import boto3\nboto3.client('stepfunctions').start_execution(stateMachineArn='a')")),
    ('elasticloadbalancing:DeleteLoadBalancer', f'arn:aws:elasticloadbalancing:us-east-1:{ACCOUNT}:loadbalancer/app/x/1',
     ('terminal', 'aws elbv2 delete-load-balancer --load-balancer-arn a'),
     ('tailnet_exec', 'aws elbv2 delete-load-balancer --load-balancer-arn b --profile p2')),
    ('elasticloadbalancing:DeleteLoadBalancer', f'arn:aws:elasticloadbalancing:us-east-1:{ACCOUNT}:loadbalancer/app/x/1',
     ('terminal', 'aws elbv2 delete-load-balancer --load-balancer-arn a'),
     ('terminal', 'aws elb delete-load-balancer --load-balancer-name classic')),
    ('ses:SendEmail', f'arn:aws:ses:us-east-1:{ACCOUNT}:identity/example.com',
     ('terminal', 'aws ses send-email --from a@example.com'),
     ('terminal', 'aws sesv2 send-email --from-email-address b@example.com')),
    ('es:DeleteDomain', f'arn:aws:es:us-east-1:{ACCOUNT}:domain/d',
     ('terminal', 'aws opensearch delete-domain --domain-name d'),
     ('execute_code', "import boto3\nboto3.client('opensearch').delete_domain(DomainName='e')")),
    ('codedeploy:CreateDeployment', f'arn:aws:codedeploy:us-east-1:{ACCOUNT}:deploymentgroup:a/b',
     ('terminal', 'aws deploy create-deployment --application-name a'),
     ('execute_code', "import boto3\nboto3.client('codedeploy').create_deployment(applicationName='b')")),
    ('elasticmapreduce:RunJobFlow', f'arn:aws:elasticmapreduce:us-east-1:{ACCOUNT}:cluster/*',
     ('execute_code', "import boto3\nboto3.client('emr').run_job_flow(Name='a')"),
     ('terminal', 'aws emr create-cluster --name b')),
]

# B3: AWS CLI global options are accepted anywhere (argparse intersperses
# optionals). The option table comes from the installed AWS CLI v2 data
# awscli/data/cli.json; a value option consumes the next word, flags do not.
OPTION_PLACEMENT = [
    'aws ssm --region us-east-1 send-command --instance-ids i-0123',
    'aws ssm --profile p2 --region us-east-1 send-command --instance-ids i-0123',
    'aws --cli-error-format json ssm send-command --instance-ids i-0123',
    'aws ssm --cli-error-format json send-command --instance-ids i-0123',
    'aws ssm --debug send-command --instance-ids i-0123',
    'aws ssm --region=us-east-1 send-command --instance-ids i-0123',
    'aws ssm --reg us-east-1 send-command --instance-ids i-0123',
    'aws --no-cli-pager ssm --output json send-command --instance-ids i-0123',
]
SSM = denial('ssm:SendCommand', f'arn:aws:ec2:us-east-1:{ACCOUNT}:instance/i-0123')


class IamMappingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='denial-iam-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        spec = importlib.util.spec_from_file_location('iam_guard', HERE / 'guard.py')
        self.g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.g)

    def _args(self, tool, text):
        return {'code': text} if tool == 'execute_code' else {'command': text}

    def _fresh(self):
        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory(prefix='denial-iam-', dir=os.environ.get('TMPDIR'))
        self.root = Path(self.tmp.name)

    def test_changed_invocation_held_by_iam_mapping(self):
        for action, resource, (rtool, rtext), (tool, text) in CASES:
            with self.subTest(action=action, replay=text):
                self._fresh()
                self.g.record_from_result(rtool, self._args(rtool, rtext), denial(action, resource), root=self.root)
                self.assertEqual(len(self.g.read_ledger(root=self.root).rows), 1)
                self.assertNotEqual(self.g._fingerprint(rtool, self._args(rtool, rtext)),
                                    self.g._fingerprint(tool, self._args(tool, text)))
                self.assertIsNotNone(self.g.classify(tool, self._args(tool, text), root=self.root))

    def test_mapping_keeps_unrelated_operations_allowed(self):
        controls = [
            ('s3:PutObject', S3_OBJ, 'aws s3api put-object --bucket example-bucket --key k',
             ['aws s3api get-bucket-location --bucket example-bucket', 'aws lambda list-functions',
              'aws sts get-caller-identity']),
            ('lambda:InvokeFunction', f'arn:aws:lambda:us-east-1:{ACCOUNT}:function:fn',
             'aws lambda invoke --function-name fn o', ['aws lambda get-function --function-name fn',
                                                         'aws s3api put-object --bucket b --key k']),
            ('states:StartExecution', f'arn:aws:states:us-east-1:{ACCOUNT}:stateMachine:sm',
             'aws stepfunctions start-execution --state-machine-arn a',
             ['aws stepfunctions describe-execution --execution-arn e']),
        ]
        for action, resource, recorded, allowed in controls:
            self._fresh()
            self.g.record_from_result('terminal', {'command': recorded}, denial(action, resource), root=self.root)
            for text in allowed:
                with self.subTest(action=action, allowed=text):
                    self.assertIsNone(self.g.classify('terminal', {'command': text}, root=self.root))

    def test_global_options_between_service_and_operation(self):
        self.g.record_from_result('terminal', {'command': 'aws ssm send-command --instance-ids i-0123 --comment rec'},
                                  SSM, root=self.root)
        for text in OPTION_PLACEMENT:
            with self.subTest(text=text):
                self.assertIsNotNone(self.g.classify('terminal', {'command': text}, root=self.root))
        # The instance adapter still distinguishes an explicit unrelated target.
        self.assertIsNone(self.g.classify(
            'terminal', {'command': 'aws ssm --region us-east-1 send-command --instance-ids i-0fff'}, root=self.root))

    def test_option_parser_extracts_operation(self):
        for text in OPTION_PLACEMENT:
            with self.subTest(text=text):
                self.assertIn(('ssm', 'sendcommand'), self.g._shell_calls(text))

    def test_option_values_never_supply_resource_selectors(self):
        # Review-iam B1: consumed or uncertain pre-operation words are not
        # operation arguments; an unrelated selector there must not ALLOW.
        self.g.record_from_result('terminal', {'command': 'aws ssm send-command --instance-ids i-0123 --comment rec'},
                                  SSM, root=self.root)
        for text in ['aws --profile --instance-ids=i-0fff ssm send-command',
                     'aws --query --instance-ids=i-0fff ssm send-command --instance-id i-0123',
                     'aws --profile --instance-ids=i-0fff ssm send-command --document-name doc',
                     'aws --profile p --instance-ids i-0fff ssm send-command',
                     'aws --instance-ids i-0fff ssm send-command',
                     'aws ssm --instance-ids i-0fff send-command',
                     'aws ssm --unknown x send-command --instance-ids i-0fff',
                     'aws -x ssm send-command --instance-ids i-0fff',
                     'aws -- ssm send-command --instance-ids i-0fff']:
            for tool in ('terminal', 'tailnet_exec', 'run_in_user_terminal'):
                with self.subTest(tool=tool, text=text):
                    self.assertIsNotNone(self.g.classify(tool, {'command': text}, root=self.root))
            with self.subTest(text=text, check='no explicit unrelated selector'):
                self.assertNotIn(['i-0fff'], [r for _, _, r in self.g._shell_invocations(text)])
        self.assertIsNone(self.g.classify(
            'terminal', {'command': 'aws --region us-east-1 --output json ssm send-command --instance-ids i-0fff'},
            root=self.root))

    def test_option_parsing_is_linear(self):
        import time
        rows = [denial('ssm:SendCommand', f'arn:aws:ec2:us-east-1:{ACCOUNT}:instance/i-0{n}') for n in range(5)]
        for n, text in enumerate(rows):
            self.g.record_from_result('terminal', {'command': f'aws ssm send-command --instance-ids i-0{n} --comment {n}'},
                                      text, root=self.root)
        cmd = 'aws ' + ' '.join(['-x'] * 40000) + ' ssm send-command --instance-ids i-0fff'
        start = time.thread_time()
        self.g.classify('terminal', {'command': cmd}, root=self.root)
        self.assertLess(time.thread_time() - start, 3.0)

    def test_iam_wildcard_denial_action(self):
        self.g.record_from_result('terminal', {'command': 'aws s3api put-object --bucket b --key k'},
                                  denial('s3:Put*', S3_OBJ), root=self.root)
        self.assertIsNotNone(self.g.classify('terminal', {'command': 'aws s3api put-object-acl --bucket b --key k2'},
                                             root=self.root))
        self.assertIsNone(self.g.classify('terminal', {'command': 'aws s3api get-object --bucket b --key k o'},
                                          root=self.root))


if __name__ == '__main__':
    unittest.main()
