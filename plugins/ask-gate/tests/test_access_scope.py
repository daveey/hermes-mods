"""Synthetic reproduction of regional denial being widened into a helper's premise."""
from conftest import llm_returning


def test_regional_deny_cannot_support_blanket_helper_premise(gate):
    gate.LOG.add('s1', 'terminal', 'aws --region us-east-1 ssm send-command',
                 'AccessDeniedException: not authorized to perform ssm:SendCommand on resource '
                 'arn:aws:ec2:us-east-1:000000000000:instance/i-00000000000000000')
    gate.LLM = llm_returning([('ok', 'helper is agent-owned')])
    result = gate.pre_tool_call(tool_name='kanban_create', session_id='s1', args={
        'title': 'Raise the review priority',
        'body': "Checked: terminal aws ssm failed. The gateway cannot reach workers: SSH/SSM are refused. "
                'Run the supported priority operation on the worker.',
    })
    assert result and result['action'] == 'block'
    assert 'regional_access_scope' in result['message']
    assert 'us-east-1' in result['message']
    assert 'read-only' in result['message']
    assert not gate.LLM.seen


import pytest


@pytest.mark.parametrize('text', [
    'SSM is denied in us-east-1; target region is not yet verified.',
    'SSM was previously refused; read-only inventory now locates the intended target.',
    'The old card said "SSM is refused"; that premise was wrong.',
    'SSM is available in us-west-2. The us-east-1 request was denied.',
])
def test_scoped_or_corrected_claims_allow_agent_owned_recovery(gate, text):
    tools = [{'tool': 'terminal', 'args': 'aws --region us-east-1 ssm send-command',
              'result': 'AccessDeniedException in us-east-1'}]
    assert gate.access_scope.regional_claim(text, tools) is None


@pytest.mark.parametrize('text', [
    'SSM is denied in us-west-2.',
    'SSM is refused everywhere. The failed request was us-east-1.',
])
def test_unsupported_scope_stays_unverified(gate, text):
    tools = [{'tool': 'terminal', 'args': 'aws --region us-east-1 ssm send-command',
              'result': 'AccessDeniedException in us-east-1'}]
    assert gate.access_scope.regional_claim(text, tools)


@pytest.mark.parametrize('tools', [[], [
    {'tool': 'kanban_show', 'args': 'aws ssm us-east-1', 'result': 'AccessDeniedException'}], [
    {'tool': 'terminal', 'args': 'aws --region us-east-1 ssm describe-instance-information',
     'result': 'Success'}], [
    {'tool': 'terminal', 'args': 'aws --region us-east-1 ec2 describe-instances',
     'result': 'UnauthorizedOperation'}],
])
def test_no_scoped_ssm_failure_makes_no_new_claim(gate, tools):
    assert gate.access_scope.regional_claim('SSM is refused.', tools) is None


def test_regional_nudge_is_bounded_and_does_not_execute(gate):
    gate.LOG.add('s1', 'terminal', 'aws --region us-east-1 ssm send-command',
                 'AccessDeniedException in us-east-1')
    args = {'title': 'Agent helper', 'body': 'SSM is refused.'}
    for _ in range(2):
        assert gate.pre_tool_call(tool_name='kanban_create', session_id='s1', args=args)['action'] == 'block'
    # Releasing a quality nudge means only continuing existing card checks, not authorization.
    assert gate._access_scope_hold('s1', 'kanban_create', args) is None
