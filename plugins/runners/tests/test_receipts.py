"""Decision-receipt verification for repair chats: fictional fixtures, a temp user-steps store."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from plugins.runners import receipts

OP = 'e6c056f2-911b-465f-b427-c2ce41eb8d7b'
RUNNER = 'runner_' + '3' * 32
SERVICE = 'service_' + 'a' * 32
NAME = 'example-canary-1'
NS = json.dumps(['install:test', 'default'])
SCOPE = ('Register runner `example-canary-1`. Card F then runs the demo: deliberately stop the canary, '
         'recover it from a repair chat.')


def make_store(tmp_path, *, effect='approve', authorized=True, scope=SCOPE, ns=NS, op_id=OP, state='resolved',
               live_revision=4, age_days=0, approve_text='Run the demo.'):
    path = tmp_path / 'actions.sqlite3'
    c = sqlite3.connect(path)
    c.execute('CREATE TABLE operations (id TEXT PRIMARY KEY, namespace TEXT, session TEXT, request TEXT, '
              'revision INTEGER, idem TEXT, fingerprint TEXT, body TEXT)')
    c.execute('CREATE TABLE requests (namespace TEXT, id TEXT, session TEXT, revision INTEGER, state TEXT, resolution TEXT)')
    c.execute('INSERT INTO requests VALUES (?,?,?,?,?,?)', (ns, 'req-1', 's', live_revision, state, None))
    recorded = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    body = dict(operationId=op_id, requestId='req-1', revision=4, dispatchState='delivered', recordedAt=recorded,
                intent=dict(effect=effect, executionAuthorized=authorized,
                            request=dict(scope=dict(markdown=scope, complete=True), outcomes=dict(approve=approve_text))))
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


REAL_SHAPE = (
    '**Why:** card F.\n\n**Exact plan if approved (option a):**\n1. Register one runner `example-canary-1`.\n'
    '5. Card F then runs the demo: deliberately stop the canary, recover it from a repair chat.\n\n'
    '**Limits:** exactly one registration.\n\n**Exclusions:** no changes to the existing runners ec2-gwhub-1/ec2-gwhub-2; '
    'no production labels (`hermes-hub`).\n\n**Consequences:** one extra idle runner.')


def test_runner_named_only_in_exclusions_is_refused_but_the_granted_subject_passes(tmp_path):
    path = make_store(tmp_path, scope=REAL_SHAPE)
    assert verify(path)['authorized_operations'] == ['diagnose', 'service_start']
    for excluded in ('ec2-gwhub-1', 'ec2-gwhub-2', 'hermes-hub'):
        out = verify(path, names=(excluded,), runner='runner_' + '9' * 32)
        assert out['verified'] is False and out['reason'] == 'subject_excluded_by_scope'


def test_start_wording_only_in_restricting_sections_does_not_grant_service_start(tmp_path):
    scope = 'Inspect `example-canary-1` read-only.\n\n**Exclusions:** do not recover or restart anything.'
    assert verify(make_store(tmp_path, scope=scope))['authorized_operations'] == ['diagnose']


@pytest.mark.parametrize('state', ['withdrawn', 'cancelled', 'superseded'])
def test_withdrawn_or_superseded_request_is_refused(tmp_path, state):
    assert verify(make_store(tmp_path, state=state))['reason'] == 'request_withdrawn_or_superseded'


def test_newer_revision_and_old_receipts_are_refused(tmp_path):
    assert verify(make_store(tmp_path, live_revision=5))['reason'] == 'request_withdrawn_or_superseded'


def test_old_receipt_is_refused(tmp_path):
    assert verify(make_store(tmp_path, age_days=15))['reason'] == 'receipt_expired'


def test_subject_names_come_from_repair_binding_and_inventory():
    config = dict(bindings=[dict(runner_id=RUNNER, service_ref=SERVICE), dict(runner_id='runner_' + '4' * 32, service_ref='service_' + 'b' * 32)])
    runners = [dict(id=RUNNER, display_name=NAME), dict(id='runner_' + '4' * 32, display_name='other')]
    assert receipts.subject_names(dict(entity_id=RUNNER), config, runners) == (RUNNER, SERVICE, NAME)
    assert receipts.subject_names(dict(entity_id='runner_' + '8' * 32), config, runners) == ('runner_' + '8' * 32,)
