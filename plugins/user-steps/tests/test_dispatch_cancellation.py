"""Terminal non-delivery is recoverable, without replay or authority leakage."""
import copy
import sqlite3

from test_decisions import core, load_module, ROOT, OWNER, store, request, decision
from test_card_delivery import setup
from test_delivery import cards
from test_prompt_delivery import RPC

prompt = load_module('cancellation_prompt', ROOT / 'prompt_delivery.py')


def test_cancelled_dispatch_recovery_has_no_card_or_inbox_authority(tmp_path):
    adapter, _, path, op = setup(tmp_path, 'approve')
    rpc = RPC()
    run = prompt.PromptAdmission(rpc=rpc)
    run.deliver(op)
    rpc.rows[op['operationId']]['dispatch'] = 'cancelled'
    receiver = cards.DecisionDelivery(tmp_path / 'inbox.db', runs=run, card_receiver=adapter)
    s = store(tmp_path)
    result = s.dispatch(OWNER, op['operationId'], receiver)
    assert result['operation']['dispatchState'] == 'cancelled'
    assert result['request']['state'] == 'cancelled'
    assert result['operation']['receipt']['terminalCancelled'] is True
    assert receiver.inbox.context(OWNER) == ''
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 0
        assert conn.execute('SELECT state FROM user_steps_card_bindings').fetchone()[0] == 'bound'
    for _ in range(3):
        assert s.dispatch(OWNER, op['operationId'], receiver) == result
    assert not s.pending_owners()
    assert s.reconcile(OWNER, receiver) == []
    replacement = copy.deepcopy(op['intent']['request'])
    replacement['scope']['markdown'] = 'Replacement exact plan requiring fresh permission'
    revised = s.revise(OWNER, op['requestId'], 1, replacement,
                       bind=adapter.bind_request)
    assert revised['revision'] == 2 and 'resolution' not in revised
    fresh = s.decide(OWNER, decision(revised, key='fresh-key'))['operation']
    assert fresh['operationId'] != op['operationId']
    assert [m for m, _ in rpc.calls].count('prompt.admit') == 1
    delivered = s.dispatch(OWNER, fresh['operationId'], receiver)
    assert delivered['operation']['dispatchState'] == 'delivered'
    assert delivered['request']['state'] == 'resolved'
    assert [m for m, _ in rpc.calls].count('prompt.admit') == 2
    context = receiver.inbox.context(OWNER)
    assert op['operationId'] not in context and fresh['operationId'] in context


def test_cancellation_requires_exact_authoritative_readback(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    rpc = RPC(); run = prompt.PromptAdmission(rpc=rpc)
    run.deliver(op)
    rpc.rows[op['operationId']]['dispatch'] = 'cancelled'
    receipt = run.deliver(op)
    assert receipt['terminalCancelled'] is True
    assert run.readback(op, receipt)
    rpc.rows[op['operationId']] = dict(rpc.rows[op['operationId']], dispatch='unknown')
    assert not run.readback(op, receipt)
