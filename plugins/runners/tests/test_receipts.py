"""Decision-receipt verification for repair chats: fictional fixtures, a temp user-steps store."""
import json
import sqlite3

import pytest

from plugins.runners import receipts

OP = 'e6c056f2-911b-465f-b427-c2ce41eb8d7b'
RUNNER = 'runner_' + '3' * 32
SERVICE = 'service_' + 'a' * 32
NAME = 'example-canary-1'
NS = json.dumps(['install:test', 'default'])
SCOPE = ('Register runner `example-canary-1`. Card F then runs the demo: deliberately stop the canary, '
         'recover it from a repair chat.')


def make_store(tmp_path, *, effect='approve', authorized=True, scope=SCOPE, ns=NS, op_id=OP):
    path = tmp_path / 'actions.sqlite3'
    c = sqlite3.connect(path)
    c.execute('CREATE TABLE operations (id TEXT PRIMARY KEY, namespace TEXT, session TEXT, request TEXT, '
              'revision INTEGER, idem TEXT, fingerprint TEXT, body TEXT)')
    body = dict(operationId=op_id, requestId='req-1', revision=4, dispatchState='delivered',
                intent=dict(effect=effect, executionAuthorized=authorized,
                            request=dict(scope=dict(markdown=scope, complete=True))))
    c.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?)', (op_id, ns, 's', 'req-1', 4, 'k', 'f', json.dumps(body)))
    c.commit(); c.close()
    return path


def verify(path, op=OP, runner=RUNNER, names=(NAME,), profile='default'):
    return receipts.verify_receipt(op, store_path=path, owner_profile=profile,
                                   subject_names=(runner, SERVICE) + tuple(names))


def test_receipt_for_this_subject_authorizes_service_start(tmp_path):
    out = verify(make_store(tmp_path))
    assert out['verified'] is True and 'service_start' in out['authorized_operations']
    assert set(out['authorized_operations']) <= {'diagnose', 'service_start'}


def test_receipt_for_another_subject_is_refused(tmp_path):
    out = verify(make_store(tmp_path), names=('other-runner',), runner='runner_' + '9' * 32)
    assert out['verified'] is False and out['reason'] == 'scope_does_not_name_subject'


@pytest.mark.parametrize('effect,authorized', [('decline', False), ('revise', False), ('approve', False), ('answer', True)])
def test_non_approval_is_refused(tmp_path, effect, authorized):
    out = verify(make_store(tmp_path, effect=effect, authorized=authorized))
    assert out['verified'] is False and out['reason'] == 'not_an_authorizing_approval'


def test_unknown_and_forged_operation_ids_are_refused(tmp_path):
    path = make_store(tmp_path)
    assert verify(path, op='11111111-1111-4111-8111-111111111111')['reason'] == 'unknown_operation'
    assert verify(path, op="x' OR '1'='1")['reason'] == 'invalid_operation_id'
    assert verify(path, op='')['reason'] == 'invalid_operation_id'


def test_missing_store_and_missing_receipt_are_refused(tmp_path):
    assert verify(tmp_path / 'nope.sqlite3')['verified'] is False
    assert receipts.verify_receipt(None, store_path=make_store(tmp_path), owner_profile='default',
                                   subject_names=(RUNNER,))['reason'] == 'invalid_operation_id'


def test_other_profile_namespace_is_refused(tmp_path):
    assert verify(make_store(tmp_path), profile='someone-else')['reason'] == 'unknown_operation'


def test_scope_without_start_wording_authorizes_diagnose_only(tmp_path):
    out = verify(make_store(tmp_path, scope='Inspect `example-canary-1` read-only.'))
    assert out['verified'] is True and out['authorized_operations'] == ['diagnose']


def test_subject_match_is_exact_token_not_substring(tmp_path):
    out = verify(make_store(tmp_path, scope='Recover example-canary-10 now.'), runner='runner_' + '9' * 32)
    assert out['verified'] is False


def test_tool_handler_requires_active_repair_and_refuses_relay_without_receipt(tmp_path):
    store = make_store(tmp_path)
    out = receipts.receipt_tool(dict(repair_id='repair_' + '1' * 32), repair=dict(id='repair_' + '1' * 32, state='active'),
                                subject_names=(RUNNER,), owner_profile='default', store_path=store)
    assert out['verified'] is False and out['reason'] == 'invalid_operation_id'
    out = receipts.receipt_tool(dict(repair_id='repair_' + '1' * 32, operation_id=OP),
                                repair=dict(id='repair_' + '1' * 32, state='closed'),
                                subject_names=(RUNNER, NAME), owner_profile='default', store_path=store)
    assert out['verified'] is False and out['reason'] == 'repair_not_active'
    out = receipts.receipt_tool(dict(repair_id='repair_' + '1' * 32, operation_id=OP, junk=1),
                                repair=dict(id='repair_' + '1' * 32, state='active'),
                                subject_names=(RUNNER, NAME), owner_profile='default', store_path=store)
    assert out['reason'] == 'unknown_field'
