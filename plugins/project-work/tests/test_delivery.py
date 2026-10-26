import pytest
from conftest import start


@pytest.mark.parametrize('status', ['running', 'blocked', 'failed', 'partial'])
def test_incomplete_card_keeps_visible(env, status):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    ports.cards[row['cards']['review']['id']]['status'] = status
    result = service.closeout(row['workflow_id'], 'result')
    assert result['error']
    assert not ports.posts
    assert not ports.get_session(row['stored_session_id'])['archived']


def test_evidence_rejected(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    ports.evidence_ok = False
    assert service.closeout(row['workflow_id'], 'result')['error']
    assert not ports.posts


def test_delivery_then_exact_archive(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    ports.sessions['sibling'] = dict(ports.sessions[row['workflow_id']], id='sibling')
    result = service.closeout(row['workflow_id'], 'result')
    assert result['state'] == 'archived'
    assert not ports.sessions['sibling']['archived']
    assert len(ports.posts) == 1
    service.closeout(row['workflow_id'], 'result')
    assert len(ports.posts) == 1
    assert service.reconcile() == []


@pytest.mark.parametrize('field,value', [('source', 'slack'), ('cwd', '/other'),
    ('title', 'not owned'), ('profile', 'other'), ('parent_session_id', 'parent'), ('id', 'other')])
def test_wrong_session_never_archived(env, field, value):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    ports.sessions[row['workflow_id']][field] = value
    result = service.closeout(row['workflow_id'], 'result')
    assert result['error']
    assert not any(isinstance(x, tuple) and x[0] == 'archive' for x in ports.calls)


def test_ambiguous_post_no_repost_or_archive(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    ports.fail = 'post_result'
    result = service.closeout(row['workflow_id'], 'result')
    assert result['state'] == 'post_intent'
    ports.fail = None
    assert 'Ambiguous delivery' in service.reconcile()[0]['error']
    assert len(ports.posts) == 1
    assert not ports.get_session(row['stored_session_id'])['archived']


@pytest.mark.parametrize('mode', ['late_followup', 'read_failure'])
def test_latest_thread_refresh_failclosed(env, mode):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    if mode == 'late_followup':
        ports.watermark_ok = False
    else:
        ports.fail = 'read_result'
    result = service.closeout(row['workflow_id'], 'result')
    assert result['state'] == 'posted'
    assert result['error']
    assert not ports.get_session(row['stored_session_id'])['archived']
    ports.fail = None
    ports.watermark_ok = True
    assert service.reconcile()[0]['state'] == 'archived'
    assert len(ports.posts) == 1


def test_archive_requires_readback(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    ports.archive_effect = False
    result = service.closeout(row['workflow_id'], 'result')
    assert result['state'] == 'archive_intent'
    assert 'Archive readback failed' in result['error']
    ports.archive_effect = True
    assert service.reconcile()[0]['state'] == 'archived'
    assert len(ports.posts) == 1


def test_generation_changed_during_delivery_blocks_archive(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    def read_result(current, receipt, text):
        newer = service.store.get(row['workflow_id'])
        newer['generation'] += 1
        service.store.save(newer)
        return True
    ports.read_result = read_result
    result = service.closeout(row['workflow_id'], 'result')
    assert 'New generation' in result['error']
    assert result['generation'] == row['generation'] + 1
    assert not ports.get_session(row['stored_session_id'])['archived']


def test_repeat_approval_does_not_reopen_delivery(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    ports.watermark_ok = False
    service.closeout(row['workflow_id'], 'result')
    assert service.approve(ctx, row['workflow_id'], row['plan_hash'])['state'] == 'posted'
    assert len(ports.posts) == 1
