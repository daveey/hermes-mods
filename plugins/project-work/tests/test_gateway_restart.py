"""Exercise real NativeBridge creation through the service after a gateway restart."""
from dataclasses import asdict, replace
import importlib
import pytest
from conftest import PACKAGE
from test_native import bridge, native_card, native_project


def test_approval_after_gateway_restart_creates_once_through_current_owner(env):
    service, ctx, ports = env
    row = service.request(ctx, 'work')
    row = service.propose_plan(row['workflow_id'], 'Implement and test', row['generation'])
    stored = ports.get_session(row['stored_session_id'])
    calls, cards = [], {}
    def owner_tool(name, args, live):
        assert live == 'new-live'
        calls.append((name, live))
        current = service.store.get(row['workflow_id'])
        stage = args['title'].split(':')[0]
        params = current['cards'][stage]['params']
        cid = 'native-' + stage
        cards[cid] = native_card(service, current, params, cid, args['body'])
        return {'task_id': cid}
    native = bridge(env, sessions=lambda q: [stored],
        rpc=lambda *a: {'session_id': 'new-live', 'stored_session_id': stored['id']},
        projects=lambda q: native_project(service),
        card_keys=lambda q: [cid for cid, card in cards.items()
                             if card['idempotency_key'] == q['idempotency_key']],
        cards=lambda cid: cards[cid], owner_tool=owner_tool)
    native.live[row['session_id']] = stored['id']
    # Retain the bridge across the gateway restart; use its real write and recovery paths.
    ports.recover_session = native.recover_session
    ports.create_card = native.create_card
    ports.recover_card = native.recover_card
    ports.get_card = native.get_card
    approved = service.approve(ctx, row['workflow_id'], row['plan_hash'])
    assert approved['state'] == 'running' and approved['error'] is None
    assert approved['cards']['implement']['id'] == 'native-implement'
    assert native.live == {'new-live': stored['id']}
    assert calls == [('kanban_create', 'new-live')] * 3
    service.approve(ctx, row['workflow_id'], row['plan_hash'])
    assert native.recover_card(row['workflow_id'], 'implement', approved['cards']['implement']['params']) == 'native-implement'
    assert calls == [('kanban_create', 'new-live')] * 3


@pytest.mark.parametrize('operation', ['request', 'observe_followup', 'approve'])
@pytest.mark.parametrize('exception', [PermissionError, ValueError, KeyError])
def test_environmental_errors_remain_retryable(env, tmp_path, monkeypatch, operation, exception):
    service, ctx, _ = env
    runtime = importlib.import_module(PACKAGE + '.runtime')
    client = importlib.import_module(PACKAGE + '.client')
    row = service.request(ctx, 'work')
    row = service.propose_plan(row['workflow_id'], 'Implement and test', row['generation'])
    controller = runtime.Controller(service)
    class Direct:
        def call(self, op, **args):
            return controller.call(op, args)
    queue = client.DurableIngressQueue(tmp_path / 'journal' / 'q.db', Direct(), retry_interval=0)
    args = dict(context=asdict(replace(ctx, message_id='2.0', thread_id=ctx.message_id)))
    if operation == 'approve':
        args.update(workflow_id=row['workflow_id'], plan_hash=row['plan_hash'])
    else:
        args['text'] = 'more work'
    real = service._authorize
    def fail(ctx):
        raise exception('environment unavailable')
    monkeypatch.setattr(service, '_authorize', fail)
    queue.append(operation, **args)
    with pytest.raises(exception):
        queue.drain_once()
    with queue.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM rejected').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM pending').fetchone()[0] == 1
    monkeypatch.setattr(service, '_authorize', real)
    assert queue.drain_once()
    assert not queue.drain_once()
