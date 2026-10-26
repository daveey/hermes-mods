from dataclasses import replace
import pytest
from conftest import start


def test_owner_request_requires_current_concrete_plan(env):
    service, ctx, ports = env
    row = service.request(ctx, 'change the project')
    assert row['state'] == 'awaiting_approval'
    assert row['stored_session_id'] and not ports.cards
    with pytest.raises(PermissionError):
        service.approve(ctx, row['workflow_id'], '')
    plan = service.propose_plan(row['workflow_id'], 'Change only documentation; run link tests.')
    with pytest.raises(PermissionError):
        service.approve(ctx, row['workflow_id'], 'forged')
    result = service.approve(ctx, row['workflow_id'], plan['plan_hash'])
    assert result['state'] == 'running'
    kinds = [x[0] if isinstance(x, tuple) else x for x in ports.calls]
    assert kinds.index('prompt') < kinds.index('create_card')


def test_followup_invalidates_approval_and_old_work_remains_gate(env):
    service, ctx, ports = env
    row = start(service, ctx, 'first')
    old_hash = row['plan_hash']
    follow = replace(ctx, message_id='next', thread_id=ctx.message_id)
    newer = service.followup(follow, 'additional changes')
    assert not newer['approved'] and newer['plan_hash'] is None
    with pytest.raises(PermissionError):
        service.approve(ctx, row['workflow_id'], old_hash)
    plan = service.propose_plan(row['workflow_id'], 'New plan')
    newer = service.approve(ctx, row['workflow_id'], plan['plan_hash'])
    for card in newer['cards'].values():
        ports.cards[card['id']]['status'] = 'done'
    result = service.closeout(row['workflow_id'], 'complete')
    assert 'Earlier generation' in result['error']
    assert not ports.posts


@pytest.mark.parametrize('field,value', [('busy', True), ('busy', None), ('pending_approval', True), ('pending_approval', None)])
def test_unknown_or_live_writer_never_archive(env, field, value):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    ports.sessions[row['workflow_id']][field] = value
    result = service.closeout(row['workflow_id'], 'complete')
    assert result['error']
    assert not ports.get_session(row['stored_session_id'])['archived']


def test_late_remote_followup_rolls_back_archive(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    calls = []
    def read(*args):
        calls.append(1)
        return len(calls) == 1
    ports.read_result = read
    result = service.closeout(row['workflow_id'], 'complete')
    assert result['state'] == 'posted'
    assert not ports.get_session(row['stored_session_id'])['archived']
    assert 'Late followup' in result['error']


def test_proven_ambiguous_send_recovers_without_second_post(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    ports.fail = 'post_result'
    service.closeout(row['workflow_id'], 'complete')
    ports.fail = None
    ports.recover_result = lambda row, text, key: {'id': key}
    assert service.reconcile()[0]['state'] == 'archived'
    assert len(ports.posts) == 1


def test_proven_ambiguous_prompt_recovers_without_replay(env):
    service, ctx, ports = env
    ports.fail = 'prompt'
    service.request(ctx, 'work')
    ports.fail = None
    ports.recover_prompt = lambda *args: True
    assert service.reconcile()[0]['state'] == 'awaiting_approval'
    assert len([c for c in ports.calls if isinstance(c, tuple) and c[0] == 'prompt']) == 1


def test_policy_rejects_string_owners(env):
    service, _, _ = env
    with pytest.raises(ValueError):
        replace(service.policy, owner_ids='owner')


def test_missing_completion_hook_is_reconciled(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    result = service.reconcile()[0]
    assert result['state'] == 'archived'
    assert row['workflow_id'] in ports.posts[0][1]
    assert len(ports.posts) == 1


def test_symlink_cwd_is_rejected(env, tmp_path):
    service, _, _ = env
    link = tmp_path / 'alias'
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        replace(service.policy, cwd=str(link))
