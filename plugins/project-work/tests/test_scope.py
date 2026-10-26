from dataclasses import replace
import pytest
from conftest import Policy, Service


@pytest.mark.parametrize('field,value', [('platform', 'desktop'), ('team_id', 'other'),
    ('channel_id', 'other'), ('profile', 'builder'), ('user_id', ''), ('message_id', '')])
def test_scope_rejected(env, field, value):
    service, ctx, ports = env
    with pytest.raises(PermissionError):
        service.request(replace(ctx, **{field: value}), 'work')
    assert not ports.calls
    assert not service.store.rows()


def test_untrusted_context(env):
    service, ctx, ports = env
    with pytest.raises(PermissionError):
        service.request(vars(ctx), 'work')


def test_owner_approval(env):
    service, ctx, ports = env
    outsider = replace(ctx, user_id='member')
    row = service.request(outsider, 'work')
    assert row['state'] == 'awaiting_approval'
    assert ports.get_session(row['stored_session_id'])['archived'] is False
    assert not ports.cards
    row = service.propose_plan(row['workflow_id'], 'Concrete plan and acceptance tests')
    with pytest.raises(PermissionError):
        service.approve(outsider, row['workflow_id'], row['plan_hash'])
    assert service.approve(ctx, row['workflow_id'], row['plan_hash'])['state'] == 'running'


@pytest.mark.parametrize('cwd', ['relative', '/not/a/real/project', '/tmp/../tmp'])
def test_invalid_paths(env, cwd):
    service, _, _ = env
    with pytest.raises(ValueError):
        replace(service.policy, cwd=cwd)


@pytest.mark.parametrize('repo', ['https://example.test/owner/repo', 'owner', 'owner/repo/extra'])
def test_invalid_repository(env, repo):
    service, _, _ = env
    with pytest.raises(ValueError):
        replace(service.policy, repo=repo)


def test_changed_policy_cannot_resume(env):
    service, ctx, ports = env
    row = service.request(ctx, 'work')
    changed = Service(replace(service.policy, repo='owner/other'), service.store, ports)
    with pytest.raises(PermissionError):
        changed.status(ctx, row['workflow_id'])
    with pytest.raises(PermissionError):
        changed.request(replace(ctx, message_id='next', thread_id=ctx.message_id), 'work')
    assert changed.reconcile() == []


def test_status_scope(env):
    service, ctx, _ = env
    row = service.request(ctx, 'work')
    with pytest.raises(PermissionError):
        service.status(replace(ctx, channel_id='elsewhere'), row['workflow_id'])
