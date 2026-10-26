from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import uuid
import pytest
from conftest import Service, Store, start


def test_lifecycle_and_dedupe(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    assert str(uuid.UUID(row['workflow_id'])) == row['workflow_id']
    assert row['thread_id'] == ctx.message_id
    assert row['state'] == 'running'
    calls = list(ports.calls)
    assert service.request(ctx, 'duplicate') == row
    assert ports.calls == calls
    cards = row['cards']
    assert cards['review']['params']['depends_on'] == [cards['implement']['id']]
    assert cards['release']['params']['depends_on'] == [cards['review']['id']]
    assert cards['review']['params']['profile'] == 'reviewer'


def test_followup_reuses_session_and_invalidates(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    assert service.closeout(row['workflow_id'], 'complete')['state'] == 'archived'
    followup = replace(ctx, message_id='next', thread_id=ctx.message_id)
    updated = service.followup(followup, 'more work')
    assert updated['generation'] == 2
    assert updated['session_id'] == row['session_id']
    assert updated['state'] == 'awaiting_approval'
    assert updated['approved'] is False
    assert updated['evidence'] == {}
    assert updated['result_receipt'] is None
    assert updated['history'][0]['result_text'] == 'complete'
    assert updated['history'][0]['state'] == 'archived'
    assert not ports.get_session(updated['stored_session_id'])['archived']
    assert ports.calls.count('create_session') == 1
    assert service.followup(followup, 'duplicate')['generation'] == 2


def test_unknown_followup(env):
    service, ctx, ports = env
    with pytest.raises(KeyError):
        service.followup(ctx, 'work')
    assert not ports.calls


def test_restart_recovers_session_without_recreate(env):
    service, ctx, ports = env
    ports.fail = 'create_session'
    row = start(service, ctx, 'work')
    assert row['session_state'] == 'create_intent'
    ports.fail = None
    restarted = Service(service.policy, Store(service.store.path), ports)
    assert restarted.reconcile()[0]['state'] == 'awaiting_approval'
    assert ports.calls.count('create_session') == 1
    assert ports.calls.count('recover_session') == 1


def test_missing_recovery_stops(env):
    service, ctx, ports = env
    ports.fail = 'create_session'
    service.request(ctx, 'work')
    ports.sessions.clear()
    ports.fail = None
    row = service.reconcile()[0]
    assert 'Ambiguous session' in row['error']
    assert ports.calls.count('create_session') == 1


def test_prompt_ambiguity_never_replayed(env):
    service, ctx, ports = env
    ports.fail = 'prompt'
    service.request(ctx, 'work')
    ports.fail = None
    row = service.reconcile()[0]
    assert 'Ambiguous prompt' in row['error']
    assert len([x for x in ports.calls if isinstance(x, tuple) and x[0] == 'prompt']) == 1


def test_card_recovery_uses_durable_key(env):
    service, ctx, ports = env
    ports.fail = 'review'
    row = start(service, ctx, 'work')
    key = row['cards']['review']['params']['idempotency_key']
    ports.fail = None
    row = service.reconcile()[0]
    assert row['state'] == 'running'
    assert row['cards']['review']['id'] == key
    assert len(ports.cards) == 3


def test_concurrent_duplicate_requests(env):
    service, ctx, ports = env
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(lambda _: service.request(ctx, 'work'), range(12)))
    assert len({r['workflow_id'] for r in rows}) == 1
    assert ports.calls.count('create_session') == 1
    assert len(service.store.rows()) == 1


def _process_request(service, ctx, queue):
    try:
        queue.put(service.request(ctx, 'work')['workflow_id'])
    except Exception as exc:
        queue.put(type(exc).__name__ + ':' + str(exc))


def test_cross_process_duplicate_requests(env):
    import multiprocessing
    service, ctx, _ = env
    context = multiprocessing.get_context('fork')
    queue = context.Queue()
    children = [context.Process(target=_process_request, args=(service, ctx, queue)) for _ in range(4)]
    for child in children:
        child.start()
    ids = [queue.get(timeout=30) for _ in children]
    for child in children:
        child.join(timeout=30)
        assert child.exitcode == 0
    assert len(set(ids)) == 1
    assert service.store.get(ids[0])['state'] == 'awaiting_approval'
    assert len(service.store.rows()) == 1


def test_database_durability_configuration(env):
    service, _, _ = env
    with service.store.connect() as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert db.execute('PRAGMA synchronous').fetchone()[0] == 2
