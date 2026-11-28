"""Independent exact-head probes; disposable real core fixtures only."""
import json
import pytest
from test_kanban_overdue_core import real, parked, mirror_for, published, choose, open_requests, status, SESSION, OWNER, core


def test_overdue_two_created_chats_both_have_actionable_request(real):
    kb, c, path, store, mod = real
    task = parked(kb, c, 'decision_retained', {'summary': 'stopped'})
    # A second real owning chat, as used by core triage attention fanout.
    from hermes_cli.kanban_db_sync import apply_sync_schema_steps
    apply_sync_schema_steps(c)
    with kb.write_txn(c):
        c.execute("INSERT INTO task_chat_links(task_id,chat_gateway,chat_profile,chat_session,kind,released,created_at) VALUES (?,?,?,?,?,?,0)",
                  (task, OWNER['gatewayId'], 'default', 'session-b', 'created', 0))
    mirror = mod.Mirror(core, store, boards=[('default', path)],
                        session_exists=lambda sid: sid in (SESSION, 'session-b'), tip=lambda sid: sid)
    mirror.reconcile(OWNER)
    second = dict(OWNER, storedSessionId='session-b')
    mirror.reconcile(second)
    first_requests = open_requests(store)
    second_requests = [r for r in store.requests(second) if r['state'] in ('open','needs_context') and not r.get('resolution')]
    print('linked_chat_requests', len(first_requests), len(second_requests))
    assert first_requests
    assert second_requests, 'second linked red chat has no actionable overdue request'
    # Both presentations bind the same board episode. The first committed choice
    # wins; another already-recorded choice must not apply a second lifecycle act.
    request = second_requests[0]
    ch = next(ch for ch in request['choices'] if 'Archive' in ch['label'])
    second_op = store.decide(second, dict(requestId=request['requestId'], expectedRevision=request['revision'],
                                         choiceId=ch['id'], revisionText=ch['label'], idempotencyKey='second'))['operation']
    first_op = choose(store, first_requests[0], 'Keep parked')
    mirror.deliver(second_op)
    assert status(c, task) == 'archived'
    with pytest.raises(core.ActionError):
        mirror.deliver(first_op)
    mod._MEMO.clear()
    mirror.reconcile(OWNER)
    mirror.reconcile(second)
    assert open_requests(store) == []
    assert not [r for r in store.requests(second) if r['state'] == 'open' and not r.get('resolution')]
    assert store.operation(OWNER, first_op['operationId'])['operation']['dispatchState'] == 'cancelled'


@pytest.mark.parametrize('kind,payload,outcome', [
    ('spend_budget_triaged', {'reason': 'run budget exhausted', 'cause': 'scope_stopped'}, 'released'),
    ('blocked', {'status': 'triage', 'reason': 'lane pending'}, 'promoted'),
])
def test_overdue_release_crash_then_reconcile_recovers_receipt(real, monkeypatch, kind, payload, outcome):
    kb, c, path, store, mod = real
    task = parked(kb, c, kind, payload)
    mirror, (request,) = published(mod, store, path)
    op = choose(store, request, 'Release')
    original = kb.add_comment
    def crash_after_release(conn, task_id, author, text, *args, **kwargs):
        if text.startswith('Release chosen in Desktop'):
            raise RuntimeError('simulated process loss after durable release')
        return original(conn, task_id, author, text, *args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(kb, 'add_comment', crash_after_release)
        with pytest.raises(RuntimeError, match='simulated process loss'):
            mirror.deliver(op)
    assert status(c, task) == 'ready'
    mod._MEMO.clear()
    mirror.reconcile(OWNER)
    recovered = store.operation(OWNER, op['operationId'])['operation']
    print('release_committed_operation', recovered['dispatchState'])
    assert recovered['dispatchState'] != 'cancelled', 'committed human release was retired as undelivered'
    receipt = mirror.deliver(op)
    assert receipt['outcome'] == outcome
    assert mirror.readback(op, receipt)
    assert mirror.deliver(op) == receipt


@pytest.mark.parametrize('gateway,profile', [('foreign-gateway', 'default'), (OWNER['gatewayId'], 'foreign-profile')])
def test_overdue_foreign_link_does_not_authorize_same_named_local_chat(real, gateway, profile):
    kb, c, path, store, mod = real
    task = parked(kb, c, 'decision_retained', {'summary': 'stopped'}, session='absent-creator')
    from hermes_cli.kanban_db_sync import apply_sync_schema_steps
    apply_sync_schema_steps(c)
    with kb.write_txn(c):
        c.execute("INSERT INTO task_chat_links(task_id,chat_gateway,chat_profile,chat_session,kind,released,created_at) VALUES (?,?,?,?,?,?,0)",
                  (task, gateway, profile, SESSION, 'created', 0))
    mirror, requests = published(mod, store, path)
    print('foreign_link_local_requests', gateway, profile, len(requests))
    assert not requests, 'foreign qualified chat link was treated as local decision authority'


@pytest.mark.parametrize('column,value', [('chat_gateway', 'elsewhere'), ('chat_profile', 'elsewhere'), ('released', 1)])
def test_overdue_link_revalidated_after_choice(real, column, value):
    kb, c, path, store, mod = real
    task = parked(kb, c, 'decision_retained', {'summary': 'stopped'}, session='absent')
    from hermes_cli.kanban_db_sync import apply_sync_schema_steps
    apply_sync_schema_steps(c)
    with kb.write_txn(c):
        c.execute("INSERT INTO task_chat_links(task_id,chat_gateway,chat_profile,chat_session,kind,released,created_at) VALUES (?,?,?,?,?,?,0)",
                  (task, 'test-gateway', 'default', SESSION, 'created', 0))
    mirror, (request,) = published(mod, store, path)
    op = choose(store, request, 'Archive')
    before = mirror._board_digest(c, [task])
    with kb.write_txn(c):
        c.execute(f'UPDATE task_chat_links SET {column}=? WHERE task_id=?', (value, task))
    assert mirror._board_digest(c, [task]) != before
    with pytest.raises(core.ActionError):
        mirror.deliver(op)
    assert status(c, task) == 'triage'


def test_overdue_link_uses_transport_not_install_identity(real, monkeypatch):
    kb, c, path, store, mod = real
    task = parked(kb, c, 'decision_retained', {'summary': 'stopped'}, session='absent')
    from hermes_cli.kanban_db_sync import apply_sync_schema_steps
    apply_sync_schema_steps(c)
    with kb.write_txn(c):
        c.execute("INSERT INTO task_chat_links(task_id,chat_gateway,chat_profile,chat_session,kind,released,created_at) VALUES (?,?,?,?,?,?,0)",
                  (task, 'transport-home', 'default', SESSION, 'created', 0))
    mirror = mirror_for(mod, store, path)
    monkeypatch.setattr(mod._overdue, '_gateway', lambda m: m.remote.gateway())
    mirror.remote.gateway = lambda: 'transport-home'
    owner = dict(OWNER, gatewayId='install:fixture')
    mirror.reconcile(owner)
    (request,) = [r for r in store.requests(owner) if r['state'] == 'open']
    assert request['owner'] == owner
    binding = mirror._read_snapshot('default', task, owner)
    assert binding['chat_link']['gateway'] == 'transport-home'
    assert binding['chat_link']['profile'] == 'default'
