"""Actual owning-runtime contract, no expiring /v1/runs replay cache."""
import copy
import pytest
from test_decisions import load_module, ROOT, OWNER, store, request, decision

module = load_module('prompt_delivery_tests', ROOT / 'prompt_delivery.py')
cards = load_module('prompt_cards_tests', ROOT / 'cards.py')

class RPC:
    def __init__(self):
        self.rows = {}; self.calls = []; self.lost = False
    def __call__(self, method, params):
        self.calls.append((method, copy.deepcopy(params)))
        key = params['operation_id']
        if method == 'prompt.admission.get':
            return self.rows.get(key, {'status': 'absent'})
        self.rows.setdefault(key, {k: v for k, v in params.items() if k != 'text'})
        self.rows[key].update(status='accepted', user_row_id=42, message_uid='message', execution_status='unknown')
        if self.lost:
            self.lost = False
            raise OSError('reply lost')
        return self.rows[key]

def operation(tmp_path, effect='approve'):
    s = store(tmp_path); r = s.create(OWNER, request())
    return s.decide(OWNER, decision(r, choice=effect, text='Revise scope'))['operation']

def test_default_receiver_uses_actual_prompt_admission(tmp_path):
    assert type(cards.DecisionDelivery(tmp_path / 'inbox').runs).__name__ == 'PromptAdmission'

def test_lost_response_restart_reads_receipt_without_resubmit(tmp_path):
    rpc = RPC(); rpc.lost = True; op = operation(tmp_path)
    with pytest.raises(OSError): module.PromptAdmission(rpc=rpc).deliver(op)
    receiver = module.PromptAdmission(rpc=rpc)
    receipt = receiver.deliver(op)
    assert receiver.readback(op, receipt)
    assert [m for m, p in rpc.calls].count('prompt.admit') == 1
    assert receipt['executionStarted'] is False

def test_mismatched_readback_fails_and_decline_never_calls_runtime(tmp_path):
    rpc = RPC(); receiver = module.PromptAdmission(rpc=rpc); op = operation(tmp_path)
    receipt = receiver.deliver(op)
    bad = copy.deepcopy(receipt); bad['admission']['profile'] = 'another'
    assert not receiver.readback(op, bad)
    rpc.rows[op['operationId']]['stored_session_id'] = 'other'
    with pytest.raises(RuntimeError, match='mismatch'): receiver.deliver(op)
    op['intent']['effect'] = 'decline'; rpc.calls.clear()
    with pytest.raises(RuntimeError, match='Decline'): receiver.deliver(op)
    assert not rpc.calls

def test_failed_dispatch_is_not_delivery_and_never_replayed(tmp_path):
    rpc = RPC(); receiver = module.PromptAdmission(rpc=rpc); op = operation(tmp_path)
    receipt = receiver.deliver(op)
    rpc.rows[op['operationId']]['dispatch'] = 'failed'
    assert not receiver.readback(op, receipt)
    with pytest.raises(RuntimeError, match='not started'):
        receiver.deliver(op)
    assert [m for m, p in rpc.calls].count('prompt.admit') == 1
