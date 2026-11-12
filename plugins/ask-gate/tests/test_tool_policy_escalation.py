"""A missing worker operation is not evidence that a human must authenticate."""
from conftest import llm_returning

REQUEST = {
    'action': 'Run the priority edit on the worker. Unblock = verify; archive = drop this helper.',
    'why_human': 'The exposed Kanban tools cannot edit priority, and the worker protocol explicitly '
                 'prohibits using the CLI for board operations.',
    'interaction': 'user_terminal',
    'scope': 'Only the priority of a review card; do not execute the review.',
    'evidence': 'kanban_show: ready, priority=0',
}


def test_worker_tool_gap_does_not_become_user_terminal_authorization(gate):
    gate.LOG.add('s1', 'kanban_show', {'task_id': 't_example'}, REQUEST['evidence'])
    gate.LLM = llm_returning([('ok', 'user can use CLI')])
    result = gate.pre_tool_call(tool_name='kanban_block', session_id='s1', args={
        'kind': 'capability', 'reason': 'see request', 'human_request': REQUEST})
    assert result and result['action'] == 'block'
    assert 'tool_policy_escalation' in result['message']
    assert 'do not bypass' in result['message']
    assert not gate.LLM.seen


import pytest
from test_approval_reconciliation import setup, snapshot, call


@pytest.mark.parametrize('extra', [
    'A password must be typed by the user.', 'OS authentication is required.',
    'The user said to ask before executing.', 'This adds recurring spend.',
])
def test_real_human_boundary_is_not_discarded(gate, extra):
    request = dict(REQUEST, why_human=REQUEST['why_human'] + ' ' + extra)
    assert gate.access_scope.tool_policy_request(request) is None


@pytest.mark.parametrize('notice', [
    'The card was unblocked.', 'The helper was archived.',
    'Request withdrawn: no answer needed; this is not approval.',
    'Kanban notification: use the user-terminal flow.',
])
def test_notice_does_not_replace_authoritative_pending_consent(gate, notice):
    dispose = setup(gate, lambda target: snapshot('pending'))
    gate.LOG.set_users('s1', [notice])
    try:
        # None here means the human approval ask remains permitted, NOT permission to execute.
        assert call(gate, interaction='user_terminal') is None
        assert gate.LLM.seen
    finally:
        dispose()


def test_releasing_policy_nudge_still_runs_existing_approval_gate(gate):
    dispose = setup(gate, lambda target: snapshot('vetoed'))
    args = {'kind': 'capability', 'reason': 'see request', 'human_request': REQUEST}
    try:
        for _ in range(2):
            assert 'tool_policy_escalation' in gate.pre_tool_call(
                tool_name='kanban_block', session_id='s1', args=args)['message']
        result = gate.pre_tool_call(tool_name='kanban_block', session_id='s1', args=args)
        assert 'denied' in result['message']
        assert not gate.LLM.seen
    finally:
        dispose()


def test_agent_owned_recovery_does_not_require_human_terminal(gate):
    assert gate.pre_tool_call(tool_name='kanban_create', session_id='s1', args={
        'title': 'Read-only target inventory recovery',
        'body': 'Use the already authorized inventory reader; no remote writes or new permissions.',
    }) is None
