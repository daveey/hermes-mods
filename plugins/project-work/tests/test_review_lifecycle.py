from dataclasses import replace
import pytest
from conftest import start


@pytest.mark.parametrize('invalid', ['evidence', 'watermark'])
def test_uncertain_archive_compensated_before_gates(env, invalid):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    archive = ports.archive
    def timeout_after_effect(stored, profile, archived):
        archive(stored, profile, archived)
        if archived:
            raise TimeoutError()
    ports.archive = timeout_after_effect
    result = service.closeout(row['workflow_id'], 'done')
    assert result['archive_pending']
    assert ports.get_session(row['stored_session_id'])['archived']
    if invalid == 'evidence':
        ports.evidence_ok = False
    else:
        ports.watermark_ok = False
    # Failed compensation must remain a durable obligation.
    ports.archive_effect = False
    service.reconcile()
    assert service.store.get(row['workflow_id'])['archive_pending']
    ports.archive_effect = True
    service.reconcile()
    assert not ports.get_session(row['stored_session_id'])['archived']
    assert service.store.get(row['workflow_id'])['state'] != 'archived'


def test_old_ambiguous_card_recovered_and_quiesced(env):
    service, ctx, ports = env
    ports.fail = 'implement'
    row = start(service, ctx, 'original requirement')
    assert not row['cards']['implement'].get('id')
    ports.fail = None
    newer = service.followup(replace(ctx, message_id='next', thread_id=ctx.message_id), 'thanks')
    assert newer['history'][0]['retired']
    assert newer['history'][0]['cards']['implement']['id']
    assert len(ports.cards) == 1
    assert next(iter(ports.cards.values()))['status'] == 'cancelled'
    assert 'original requirement' in newer['text'] and 'thanks' in newer['text']


def test_retirement_failure_gates_new_work_and_retries(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    quiesce = ports.quiesce_generation
    ports.quiesce_generation = lambda *args: False
    newer = service.followup(replace(ctx, message_id='next', thread_id=ctx.message_id), 'more')
    assert 'has not stopped' in newer['error']
    assert newer['prompt_state'] is None
    ports.quiesce_generation = quiesce
    service.reconcile()
    newer = service.store.get(row['workflow_id'])
    assert newer['state'] == 'awaiting_approval'
    assert newer['history'][0]['retired']


def test_uncertain_create_never_repeats_on_recovery_miss(env):
    service, ctx, ports = env
    ports.fail = 'implement'
    row = start(service, ctx, 'work')
    ports.cards.clear()
    ports.fail = None
    service.reconcile()
    assert not ports.cards
    assert 'Ambiguous card' in service.store.get(row['workflow_id'])['error']
    assert len([c for c in ports.calls if isinstance(c, tuple) and c[0] == 'create_card']) == 1


def test_plan_generation_and_no_idle_resume(env):
    service, ctx, ports = env
    row = service.request(ctx, 'work')
    calls = len(ports.calls)
    service.reconcile()
    assert len(ports.calls) == calls
    with pytest.raises(RuntimeError, match='Stale planning'):
        service.propose_plan(row['workflow_id'], 'stale plan', 0)


def test_backfill_archived_followup_once(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    service.closeout(row['workflow_id'], 'done')
    ports.thread_events = lambda row: [(ctx, 'work'),
        (replace(ctx, message_id='next', thread_id=ctx.message_id), 'missed reply')]
    service.reconcile()
    service.reconcile()
    result = service.store.get(row['workflow_id'])
    assert result['generation'] == 2 and result['state'] == 'awaiting_approval'
    assert not ports.get_session(row['stored_session_id'])['archived']


def test_backfill_rejected_and_duplicate_approvals_are_control_events(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    command = f"!project approve {row['workflow_id']} {row['plan_hash']}"
    ports.thread_events = lambda row: [
        (replace(ctx, message_id='attempt1', thread_id=ctx.message_id, user_id='member'), command),
        (replace(ctx, message_id='attempt2', thread_id=ctx.message_id), command)]
    service.reconcile()
    service.reconcile()
    result = service.store.get(row['workflow_id'])
    assert result['generation'] == 1
    assert len(result['approval_attempts']) == 2
    assert result['approved_message_id'] == 'attempt2'


def test_approval_reread_cannot_be_forged(env):
    service, ctx, ports = env
    row = service.request(ctx, 'work')
    row = service.propose_plan(row['workflow_id'], 'plan', row['generation'])
    ports.verify_approval = lambda *args: False
    with pytest.raises(PermissionError, match='Slack approval'):
        service.approve(ctx, row['workflow_id'], row['plan_hash'])
    assert not ports.cards


def test_native_like_unpersisted_draft_fails_closed(env):
    service, ctx, ports = env
    def lost_create(*args):
        raise TimeoutError('draft was never persisted')
    ports.create_session = lost_create
    row = service.request(ctx, 'work')
    service.reconcile()
    result = service.store.get(row['workflow_id'])
    assert 'operator recovery' in result['error']
    assert not ports.cards and not result['stored_session_id']


def test_status_cross_thread_requires_owner(env):
    service, ctx, ports = env
    row = service.request(ctx, 'work')
    with pytest.raises(PermissionError):
        service.status(replace(ctx, user_id='member', message_id='other'), row['workflow_id'])


def test_profiles_must_be_distinct(env):
    service, _, _ = env
    with pytest.raises(ValueError, match='distinct'):
        replace(service.policy, reviewer_profile=service.policy.builder_profile)
