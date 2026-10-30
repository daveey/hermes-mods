"""Receipt rejection and safe diagnostic classification, without a live runtime."""
import copy
import pytest
from test_decisions import load_module, ROOT, OWNER, store, request, decision

gateway = load_module('gateway_receipt_tests', ROOT / 'gateway_delivery.py')
prompt = load_module('gateway_prompt_tests', ROOT / 'prompt_delivery.py')


@pytest.mark.parametrize('status,outcome,expected', [
    ('queued', None, 'queued'), ('started', None, 'queued'),
    ('terminal', 'completed', 'queued'), ('terminal', 'cancelled', 'cancelled'),
    # A receipt confirms durable admission, not successful model completion.
    ('terminal', 'interrupted', 'queued'), ('terminal', 'steered', 'queued'),
    ('terminal', 'steering', 'queued'),
    ('terminal', 'failed', 'failed'), ('terminal', 'error', 'failed'),
    ('terminal', 'rejected', 'failed'), ('unknown', None, 'failed'),
])
def test_gateway_receipt_outcomes(status, outcome, expected):
    op = {'operationId': 'receipt-test', 'owner': OWNER, 'intent': {'effect': 'answer'}}
    params = prompt.PromptAdmission._params(op)
    row = {'admission_id': 'a', 'request_id': gateway._identity(params), 'principal_id': 'automation:route',
        'payload': {'text': params['text'], 'local_automation_v1': {
            'owner': OWNER['storedSessionId'], 'identity': gateway._identity(params), 'route': 'route'}},
        'status': status, 'outcome': outcome}
    receipt = gateway._receipt(None, params, row)
    assert receipt['dispatch'] == expected
    assert prompt.PromptAdmission._matches(params, receipt) is (expected != 'failed')
    forged = copy.deepcopy(row); forged['payload']['text'] += ' changed'
    with pytest.raises(gateway.GatewayDeliveryError, match='gateway_admission_mismatch'):
        gateway._receipt(None, params, forged)


def test_delivery_failure_logs_classification_not_transport_secrets(tmp_path, caplog):
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    class Receiver:
        def deliver(self, op):
            raise gateway.GatewayDeliveryError('gateway_owner_unavailable')
    result = s.dispatch(OWNER, op['operationId'], Receiver())
    assert result['operation']['dispatchState'] == 'failed'
    assert result['request']['state'] == 'open'
    assert 'classification=gateway_owner_unavailable' in caplog.text
    assert op['operationId'] in caplog.text
