"""v1 durable decision delivery: bound answers and owner receipts (approved durable-decision-delivery plan).

Every behaviour here is new: on the parent commit the question-binding, numbered-list and receipt APIs do not exist,
and the clarify provider ends a blocked clarify with ANY undelivered decision of the same session (the unrelated
same-session negative control below). All persistence is under pytest's tmp_path; no live store is touched.
"""
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_decisions import core, load_module, ROOT, OWNER, store, request, decision, BusyReceiver, DurableReceiver

cards = load_module('bound_cards', ROOT / 'cards.py')
early_mod = load_module('bound_clarify_early', ROOT / 'clarify_early.py')
prompt_delivery = load_module('bound_prompt_delivery', ROOT / 'prompt_delivery.py')


def choice_request(title='Which runner?', labels=('canary', 'aws'), owner=None):
    r = request()
    r.update(kind='choice', title=title, owner=dict(owner or OWNER),
             outcomes=dict(revise='Keep blocked', answer='Use the picked runner'),
             choices=[dict(id=f'c{i}', label=l, effect='answer') for i, l in enumerate(labels)])
    return r


def answer(r, i=0, key=None):
    return decision(r, key=key or str(uuid.uuid4()), choice=f'c{i}', text=r['choices'][i]['label'])


class Receiver:
    """Durable idempotent admission keyed by operationId (the PromptAdmission contract), with crash hooks."""
    def __init__(self): self.admitted = {}; self.crash_after_admit = False
    def deliver(self, op):
        self.admitted.setdefault(op['operationId'], op['intent'])
        if self.crash_after_admit:
            raise KeyboardInterrupt('process killed after admission, before readback')
        return {'destination': 'test_runtime', 'operationId': op['operationId'], 'executionStarted': False}
    def readback(self, op, receipt): return self.admitted.get(op['operationId']) == op['intent']


def delivered(tmp_path, s=None, r=None, owner=None, choice='approve'):
    s = s or store(tmp_path); owner = owner or dict(OWNER)
    r = r or s.create(owner, request())
    op = s.decide(owner, decision(r, key=str(uuid.uuid4()), choice=choice,
                                  text='Need more' if choice == 'revise' else None))['operation']
    s.dispatch(owner, op['operationId'], Receiver())
    return s, owner, r, s.operation(owner, oid=op['operationId'])['operation']


def ack(op, **kw):
    return dict(operationId=op['operationId'], phase='acknowledged', revision=op['revision'],
                scopeDigest=core.scope_digest(op['intent']['request']), **kw)


def reserve(op, **kw):
    return dict(ack(op), phase='reserved', **kw)


def applied(op, evidence=None):
    return dict(ack(op), phase='applied', evidence=evidence or EVIDENCE)


EVIDENCE = [{'type': 'commit', 'id': '0123456789abcdef0123456789abcdef01234567'}]


# ---- 1. exact revision/digest binding ------------------------------------------------------------------------

def test_every_new_revision_gets_an_immutable_question_binding(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    b = s.binding(OWNER, r['requestId'])
    assert b['revision'] == 1 and b['owner'] == OWNER and b['source'] == {'type': 'action_request'}
    assert b['questions'] == [{'question': r['title'], 'choices': [{'id': c['id'], 'label': c['label']}
                                                                    for c in r['choices']]}]
    assert b['scopeDigest'] == core.scope_digest(r) and b['questionKey'] == core.question_key(
        r['title'], [c['label'] for c in r['choices']])
    r2 = s.revise(OWNER, r['requestId'], 1, dict(r, title='Synthetic change v2'))
    assert s.binding(OWNER, r['requestId'])['revision'] == 2
    assert s.binding(OWNER, r['requestId'], 1) == b  # the old revision's binding is preserved untouched
    assert s.binding(OWNER, r['requestId'])['scopeDigest'] != b['scopeDigest']
    with s._tx() as c:  # rebinding the same revision with different content fails closed
        with pytest.raises(core.ActionError) as err:
            s._bind_question(c, dict(r2, title='Tampered'))
    assert err.value.status == 409


def test_decision_operation_carries_the_binding_and_old_rows_stay_unbound(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    assert op['binding']['scopeDigest'] == core.scope_digest(r)
    with s._tx() as c:  # a row written by a pre-v1 process: no binding row at all
        c.execute('DELETE FROM question_bindings')
    r_old = s.create(OWNER, request())
    with s._tx() as c:
        c.execute('DELETE FROM question_bindings WHERE request=?', (r_old['requestId'],))
    old = s.decide(OWNER, decision(r_old, key='old'))['operation']
    assert 'binding' not in old and s.binding(OWNER, r_old['requestId']) is None


def early_provider(s, tmp_path, runs=None):
    delivery = cards.DecisionDelivery(tmp_path / 'inbox.db', runs=runs or Receiver())
    return early_mod.make_provider(store=lambda: s, delivery=lambda: delivery, db=tmp_path / 'none.db',
                                   owner_for=lambda sid: dict(OWNER, storedSessionId=sid))


def descriptor(questions, tool_call_id='call_1'):
    import hashlib
    qs = [{'question': q, 'choices': list(c)} for q, c in questions]
    return {'schemaVersion': 1, 'toolCallId': tool_call_id, 'questions': qs,
            'digest': hashlib.sha256(json.dumps(qs, sort_keys=True, separators=(',', ':'),
                                                ensure_ascii=False).encode()).hexdigest()}


def busy_decided(s, r, i=0):
    op = s.decide(r['owner'], answer(r, i))['operation']
    s.dispatch(r['owner'], op['operationId'], BusyReceiver())
    return op


def test_clarify_ends_only_for_its_bound_question_not_an_unrelated_same_session_decision(tmp_path):
    s = store(tmp_path)
    unrelated = s.create(OWNER, choice_request('Deploy where?', ('hub', 'worker')))
    busy_decided(s, unrelated)
    provider = early_provider(s, tmp_path)
    assert getattr(provider, 'accepts_question', False) is True
    q = descriptor([('Which runner?', ('canary', 'aws'))])
    # Negative control: same session, recorded and undelivered, but a different question.
    assert provider('session-a', 'call_1', question=q) is None
    bound = s.create(OWNER, choice_request('Which runner?', ('canary', 'aws')))
    op = busy_decided(s, bound, 1)
    # call_1 was bound when no such request existed: render-time binding never adopts a later request.
    assert provider('session-a', 'call_1', question=q) is None
    q = descriptor([('Which runner?', ('canary', 'aws'))], 'call_2')
    found = provider('session-a', 'call_2', question=q)
    assert [d['requestId'] for d in found.result['decisions']] == [bound['requestId']]
    d = found.result['decisions'][0]
    assert d['operationId'] == op['operationId'] and d['revision'] == 1 and d['choice'] == 'aws'
    assert d['scopeDigest'] == core.scope_digest(bound) and d['permissionGrants'] == []
    assert found.result['binding'] == {'toolCallId': 'call_2', 'questionDigest': q['digest']}
    found.commit()
    done = s.operation(OWNER, oid=op['operationId'])['operation']
    assert done['dispatchState'] == 'delivered' and done['clarifyBinding']['questionDigest'] == q['digest']
    still = s.operation(OWNER, oid=s.undelivered('test-gateway', 'default')[0]['operationId'])['operation']
    assert still['requestId'] == unrelated['requestId'] and still['dispatchState'] == 'failed'


def test_clarify_changed_choice_order_or_revised_scope_is_not_the_same_question(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, choice_request())
    busy_decided(s, r)
    provider = early_provider(s, tmp_path)
    assert provider('session-a', 'call_1', question=descriptor([('Which runner?', ('aws', 'canary'))])) is None
    assert provider('session-a', 'call_1', question=descriptor([('Which runner?', ('canary', 'aws'))],
                                                               'other_call')) is None  # descriptor/tool mismatch
    assert provider('session-a', 'call_1', question=None) is None  # older core: no descriptor -> fail closed
    assert provider('session-a', 'call_1') is None

    # A fresh displayed clarify freezes revision 1 before there is a decision.
    # Changing only scope must not reuse the identical question/options as authority.
    s2 = store(tmp_path / 'scope-only')
    original = s2.create(OWNER, choice_request())
    p2 = early_provider(s2, tmp_path / 'scope-only')
    q = descriptor([('Which runner?', ('canary', 'aws'))], 'scope_call_1')
    assert p2('session-a', 'scope_call_1', question=q) is None
    revised = s2.revise(OWNER, original['requestId'], 1,
                        dict(original, scope=dict(original['scope'], markdown='Only the new isolated target')))
    assert revised['title'] == original['title'] and revised['choices'] == original['choices']
    assert core.scope_digest(revised) != core.scope_digest(original)
    op = busy_decided(s2, revised, 1)
    assert p2('session-a', 'scope_call_1', question=q) is None
    fresh = descriptor([('Which runner?', ('canary', 'aws'))], 'scope_call_2')
    found = p2('session-a', 'scope_call_2', question=fresh)
    assert found is not None
    assert found.result['decisions'][0]['operationId'] == op['operationId']
    assert found.result['decisions'][0]['revision'] == 2
    assert found.result['decisions'][0]['scopeDigest'] == core.scope_digest(revised)


def test_unbound_pre_v1_decision_never_ends_a_clarify(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, choice_request())
    with s._tx() as c: c.execute('DELETE FROM question_bindings')
    busy_decided(s, r)
    assert early_provider(s, tmp_path)('session-a', 'call_1',
                                       question=descriptor([('Which runner?', ('canary', 'aws'))])) is None


# ---- 2. numbered answers -------------------------------------------------------------------------------------

def test_numbered_answer_maps_to_the_original_list_after_newer_reordered_lists(tmp_path):
    s = store(tmp_path)
    a = s.create(OWNER, choice_request('Runner?', ('canary', 'aws')))
    b = s.create(OWNER, choice_request('Region?', ('us-east-1', 'eu-west-1')))
    first = s.record_list(OWNER, 'msg-1', [dict(requestId=a['requestId'], choiceId='c0'),
                                           dict(requestId=b['requestId'], choiceId='c1')])
    # Intervening messages show another list with the opposite order.
    s.record_list(OWNER, 'msg-2', [dict(requestId=b['requestId'], choiceId='c1'),
                                   dict(requestId=a['requestId'], choiceId='c0')])
    assert first['entries'][0]['n'] == 1 and first['entries'][1]['label'] == 'eu-west-1'
    late = s.resolve_numbered(OWNER, 'msg-1', 2)  # delayed reply "2" to the FIRST list
    assert late == {'requestId': b['requestId'], 'expectedRevision': 1, 'choiceId': 'c1', 'revisionText': 'eu-west-1'}
    assert s.resolve_numbered(OWNER, 'msg-2', 2)['requestId'] == a['requestId']
    op = s.decide(OWNER, dict(late, idempotencyKey='n2'))['operation']
    assert op['intent']['revisionText'] == 'eu-west-1' and op['intent']['permissionGrants'] == []


def test_ambiguous_unbound_and_stale_numbered_answers_cannot_authorize(tmp_path):
    s = store(tmp_path); a = s.create(OWNER, request())
    s.record_list(OWNER, 'msg-1', [dict(requestId=a['requestId'], choiceId='approve')])
    for source in (None, '', '  '):
        with pytest.raises(core.ActionError) as err: s.resolve_numbered(OWNER, source, 1)
        assert err.value.status == 422  # "1" with no originating list is never mapped to the newest list
    with pytest.raises(core.ActionError) as err: s.resolve_numbered(OWNER, 'msg-unknown', 1)
    assert err.value.status == 404
    with pytest.raises(core.ActionError): s.resolve_numbered(OWNER, 'msg-1', 2)
    with pytest.raises(core.ActionError): s.resolve_numbered(OWNER, 'msg-1', True)
    s.revise(OWNER, a['requestId'], 1, dict(a, title='Synthetic change, bigger scope'))
    with pytest.raises(core.ActionError) as err: s.resolve_numbered(OWNER, 'msg-1', 1)
    assert err.value.status == 409  # the list showed revision 1; approval never transfers to revision 2
    with pytest.raises(core.ActionError) as err:  # same source, different list: never silently replaced
        s.record_list(OWNER, 'msg-1', [dict(requestId=a['requestId'], choiceId='decline')])
    assert err.value.status == 409
    with pytest.raises(core.ActionError):  # free text / change requests are not numbered options
        s.record_list(OWNER, 'msg-3', [dict(requestId=a['requestId'], choiceId='revise')])
    other = dict(OWNER, gatewayId='other-gateway')
    with pytest.raises(core.ActionError): s.resolve_numbered(other, 'msg-1', 1)  # cross-gateway same ids


# ---- 3./8. owner receipts are separate phases ------------------------------------------------------------------

def test_receipts_are_ordered_phases_and_delivery_alone_is_unknown(tmp_path):
    s, owner, r, op = delivered(tmp_path)
    assert s.receipts(owner, op['operationId'])['phase'] == 'unknown'  # admission is not acknowledgment
    with pytest.raises(core.ActionError) as err:
        s.record_receipt(owner, dict(ack(op), phase='applied', evidence=EVIDENCE))
    assert err.value.status == 409  # application needs a prior acknowledgment
    got = s.record_receipt(owner, ack(op))
    assert got['consumer']['phase'] == 'acknowledged' and got['consumer']['grants'] == []
    s.record_receipt(owner, reserve(op))
    with pytest.raises(core.ActionError): s.record_receipt(owner, dict(ack(op), phase='applied'))  # no evidence
    done = s.record_receipt(owner, dict(ack(op), phase='applied', evidence=EVIDENCE))
    assert done['consumer']['phase'] == 'applied'
    assert [x['phase'] for x in s.receipts(owner, op['operationId'])['receipts']] == [
        'acknowledged', 'reserved', 'applied']
    assert s.operation(owner, oid=op['operationId'])['operation']['intent'] == op['intent']  # nothing widened
    view = s.receipts(owner, op['operationId'])
    assert view['phase'] == 'applied' and view['delivery'] == 'delivered'


def test_receipts_require_confirmed_delivery_and_the_exact_revision_and_scope(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    with pytest.raises(core.ActionError) as err: s.record_receipt(OWNER, ack(op))
    assert err.value.status == 409  # recorded but not delivered
    s.dispatch(OWNER, op['operationId'], BusyReceiver())
    with pytest.raises(core.ActionError): s.record_receipt(OWNER, ack(op))  # failed delivery is not delivery
    s.dispatch(OWNER, op['operationId'], Receiver())
    with pytest.raises(core.ActionError) as err: s.record_receipt(OWNER, dict(ack(op), revision=2))
    assert err.value.status == 409
    with pytest.raises(core.ActionError) as err: s.record_receipt(OWNER, dict(ack(op), scopeDigest='0' * 64))
    assert err.value.status == 409
    with pytest.raises(core.ActionError): s.record_receipt(OWNER, dict(ack(op), phase='done'))
    with pytest.raises(core.ActionError): s.record_receipt(OWNER, dict(ack(op), permissionGrants=['gpu']))
    with pytest.raises(core.ActionError): s.record_receipt(OWNER, dict(ack(op), evidence=EVIDENCE))
    assert s.record_receipt(OWNER, ack(op))['consumer']['phase'] == 'acknowledged'


def test_not_applied_needs_a_reason_and_excludes_applied(tmp_path):
    s, owner, r, op = delivered(tmp_path)
    s.record_receipt(owner, ack(op))  # before any reservation nothing can have happened: not_applied is honest
    with pytest.raises(core.ActionError): s.record_receipt(owner, dict(ack(op), phase='not_applied'))
    s.record_receipt(owner, dict(ack(op), phase='not_applied', reason='Superseded by a newer plan'))
    with pytest.raises(core.ActionError) as err:
        s.record_receipt(owner, dict(ack(op), phase='applied', evidence=EVIDENCE))
    assert err.value.status == 409
    assert s.receipts(owner, op['operationId'])['phase'] == 'not_applied'
    with pytest.raises(core.ActionError) as err: s.record_receipt(owner, reserve(op))  # no reservation after a denial
    assert err.value.status == 409


def test_not_applied_after_reservation_is_refused_and_a_later_applied_stays_possible(tmp_path):
    s, owner, r, op = delivered(tmp_path)
    s.record_receipt(owner, ack(op)); s.record_receipt(owner, reserve(op))
    with pytest.raises(core.ActionError) as err:
        s.record_receipt(owner, dict(ack(op), phase='not_applied', reason='readback absent; uncertain'))
    assert err.value.status == 409 and 'uncertain' in str(err.value).lower()
    view = s.receipts(owner, op['operationId'])
    assert view['phase'] == 'uncertain' and [x['phase'] for x in view['receipts']] == ['acknowledged', 'reserved']
    s.record_receipt(owner, applied(op))  # the real effect may still be reported
    assert s.receipts(owner, op['operationId'])['phase'] == 'applied'


def test_non_holder_not_applied_cannot_block_the_holders_applied_after_the_real_effect(tmp_path):
    s, owner, r, op = delivered(tmp_path)
    s.record_receipt(owner, ack(op)); assert s.record_receipt(owner, reserve(op))['reservation'] == 'new'
    assert store(tmp_path).record_receipt(owner, reserve(op))['mustVerify'] is True  # non-holder
    with pytest.raises(core.ActionError) as err:
        store(tmp_path).record_receipt(owner, dict(ack(op), phase='not_applied', reason='readback absent; uncertain'))
    assert err.value.status == 409
    s.record_receipt(owner, applied(op))
    assert s.receipts(owner, op['operationId'])['phase'] == 'applied'


def test_reserved_row_with_a_legacy_not_applied_still_reads_uncertain(tmp_path):
    # a8a9daec could store reserved + not_applied; reading such a row must never claim nothing happened
    op = {'dispatchState': 'delivered'}
    rows = [{'phase': 'acknowledged'}, {'phase': 'reserved'}, {'phase': 'not_applied', 'reason': 'x'}]
    assert core.ActionStore.consumer_state(op, rows)['phase'] == 'uncertain'


def test_prompt_tells_the_agent_to_leave_an_uncertain_reservation_alone(tmp_path):
    s, owner, r, op = delivered(tmp_path)
    text = prompt_delivery.PromptAdmission._params(op)['text']
    low = text.lower()
    assert 'leave it uncertain' in low and 'do not record not_applied' in low
    assert 'before reservation' in low or 'before reserving' in low


def test_decline_cannot_be_applied(tmp_path):
    s, owner, r, op = delivered(tmp_path, choice='decline')
    s.record_receipt(owner, ack(op))
    with pytest.raises(core.ActionError) as err: s.record_receipt(owner, reserve(op))
    assert err.value.status == 422  # only approve/answer can be reserved
    with pytest.raises(core.ActionError) as err:
        s.record_receipt(owner, dict(ack(op), phase='applied', evidence=EVIDENCE))
    assert err.value.status == 422


# ---- 4. duplicates, concurrency, lost replies --------------------------------------------------------------------

def test_duplicate_and_concurrent_receipts_keep_one_row_and_conflicts_fail_closed(tmp_path):
    s, owner, r, op = delivered(tmp_path)
    first = s.record_receipt(owner, ack(op))
    assert store(tmp_path).record_receipt(owner, ack(op))['receipt'] == first['receipt']  # lost reply, retried
    s.record_receipt(owner, reserve(op))
    applied = dict(ack(op), phase='applied', evidence=EVIDENCE)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: store(tmp_path).record_receipt(owner, applied)['receipt'], range(8)))
    assert len({json.dumps(x, sort_keys=True) for x in results}) == 1
    with pytest.raises(core.ActionError) as err:  # same phase, different evidence: conflicting reuse
        s.record_receipt(owner, dict(applied, evidence=[{'type': 'pr', 'id': 'other'}]))
    assert err.value.status == 409
    with s._read() as c:
        assert c.execute('SELECT COUNT(*) FROM owner_receipts').fetchone()[0] == 3


def test_concurrent_applied_versus_not_applied_has_exactly_one_winner(tmp_path):
    s, owner, r, op = delivered(tmp_path)
    s.record_receipt(owner, ack(op)); s.record_receipt(owner, reserve(op))
    bodies = [dict(ack(op), phase='applied', evidence=EVIDENCE),
              dict(ack(op), phase='not_applied', reason='Chat moved on')] * 4

    def attempt(body):
        try: return store(tmp_path).record_receipt(owner, body)['receipt']['phase']
        except core.ActionError as exc: return exc.status
    with ThreadPoolExecutor(8) as pool:
        out = list(pool.map(attempt, bodies))
    winners = {x for x in out if isinstance(x, str)}
    assert winners == {'applied'}  # reserved: not_applied can never win
    assert len(winners) == 1 and all(x in winners or x == 409 for x in out)


def test_duplicate_concurrent_delivery_preserves_one_decision(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    keys = ['dup'] * 6
    with ThreadPoolExecutor(6) as pool:
        ops = list(pool.map(lambda k: store(tmp_path).decide(OWNER, decision(r, key=k))['operation']['operationId'], keys))
    assert len(set(ops)) == 1
    receiver = Receiver()
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda _: store(tmp_path).dispatch(OWNER, ops[0], receiver), range(6)))
    assert list(receiver.admitted) == ops[:1]
    assert s.operation(OWNER, oid=ops[0])['operation']['dispatchState'] == 'delivered'


# ---- restart boundaries ------------------------------------------------------------------------------------------

def test_restart_at_each_boundary_recovers_only_the_missing_phase(tmp_path):
    owner = dict(OWNER)
    s = store(tmp_path); r = s.create(owner, request())
    op = s.decide(owner, decision(r, key='restart'))['operation']
    # (a) recorded / pre-send: the process dies before dispatch. A fresh store finds it pending and sends it once.
    receiver = Receiver()
    assert [o['operationId'] for o in store(tmp_path).undelivered('test-gateway', 'default')] == [op['operationId']]
    # (b) post-admission / pre-readback: admitted, then killed before the delivery transaction.
    receiver.crash_after_admit = True
    with pytest.raises(KeyboardInterrupt):
        store(tmp_path).dispatch(owner, op['operationId'], receiver)
    mid = store(tmp_path).operation(owner, oid=op['operationId'])['operation']
    assert mid['dispatchState'] == 'pending' and mid['attempts'] == 1
    receiver.crash_after_admit = False
    out = store(tmp_path).reconcile(owner, receiver)
    assert [x['operation']['dispatchState'] for x in out] == ['delivered'] and len(receiver.admitted) == 1
    # (c) post-owner-ack: the ack survives a restart; re-acknowledging is a no-op.
    store(tmp_path).record_receipt(owner, ack(op))
    assert store(tmp_path).record_receipt(owner, ack(op))['consumer']['phase'] == 'acknowledged'
    # (d) post-apply / pre-receipt: the work happened, the receipt write is retried after restart exactly once.
    assert store(tmp_path).record_receipt(owner, reserve(op))['reservation'] == 'new'
    applied = dict(ack(op), phase='applied', evidence=EVIDENCE)
    store(tmp_path).record_receipt(owner, applied)
    store(tmp_path).record_receipt(owner, applied)
    assert store(tmp_path).reconcile(owner, receiver) == [] and len(receiver.admitted) == 1
    assert [x['phase'] for x in store(tmp_path).receipts(owner, op['operationId'])['receipts']] == [
        'acknowledged', 'reserved', 'applied']


# ---- 5. superseded / revised / archived ---------------------------------------------------------------------------

def test_revised_request_rejects_stale_application_and_inherits_nothing(tmp_path):
    s, owner, r, op = delivered(tmp_path)
    s.record_receipt(owner, ack(op)); s.record_receipt(owner, reserve(op))  # reserved while still current
    r2 = s.revise(owner, r['requestId'], 1, dict(r, title='Synthetic change with more scope'))
    assert r2['state'] == 'open' and 'resolution' not in r2  # the replacement needs a fresh decision
    with pytest.raises(core.ActionError) as err:
        s.record_receipt(owner, dict(ack(op), phase='applied', evidence=EVIDENCE))
    assert err.value.status == 409
    with pytest.raises(core.ActionError) as err:  # reserved before the revision: outcome stays uncertain, never not_applied
        s.record_receipt(owner, dict(ack(op), phase='not_applied', reason='Request was revised'))
    assert err.value.status == 409
    assert s.receipts(owner, op['operationId'])['phase'] == 'uncertain'
    assert s.history(owner, r['requestId'])[0]['title'] == 'Synthetic change'  # historical answer preserved
    assert s.operation(owner, oid=op['operationId'])['operation']['decision']['choiceId'] == 'approve'
    early = early_provider(s, tmp_path)
    assert early('session-a', 'c', question=descriptor([(r2['title'], ('Approve', 'Revise', 'Decline'))], 'c')) is None


def test_retired_operation_for_an_archived_task_cannot_be_acknowledged_or_applied(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    s.retire_operation(OWNER, op['operationId'], by='system', reason='card archived', evidence='kanban:t_x')
    for receipt in (ack(op), reserve(op), applied(op)):
        with pytest.raises(core.ActionError) as err:
            s.record_receipt(OWNER, receipt)
        assert err.value.status == 409
    assert s.receipts(OWNER, op['operationId'])['phase'] == 'unknown'


# ---- 6. no broadened authority -------------------------------------------------------------------------------------

def test_ok_free_text_and_relay_cannot_broaden_frozen_authority(tmp_path):
    s = store(tmp_path)
    r = s.create(OWNER, dict(choice_request(), choices=[dict(id='answer-text', label='Other', effect='answer'),
                                                        dict(id='now', label='now', effect='answer',
                                                             authorizes=True)]))
    free = s.decide(OWNER, decision(r, key='free', choice='answer-text', text='OK, and also use the GPU'))['operation']
    assert free['intent']['executionAuthorized'] is False and free['intent']['permissionGrants'] == []
    s.dispatch(OWNER, free['operationId'], Receiver())
    out = s.record_receipt(OWNER, ack(free))
    assert out['consumer']['grants'] == [] and 'permissionGrants' not in out['receipt']
    # A relay from another session (not the owner, not its compression continuation) cannot write receipts.
    relay = dict(OWNER, storedSessionId='relay-session')
    with pytest.raises(core.ActionError) as err: s.record_receipt(relay, ack(free))
    assert err.value.status == 403
    with pytest.raises(core.ActionError) as err:  # a runtime notice cannot smuggle extra authority fields
        s.record_receipt(OWNER, dict(ack(free), phase='applied', evidence=EVIDENCE, executionAuthorized=True))
    assert err.value.status == 422


# ---- 7. compaction, cross-gateway, TUI and gateway FIFO -------------------------------------------------------------

def test_compression_continuation_may_receipt_but_lineage_alone_never_binds_a_question(tmp_path):
    s, owner, r, op = delivered(tmp_path)
    child = dict(OWNER, storedSessionId='session-a-compressed')
    with pytest.raises(core.ActionError): s.record_receipt(child, ack(op))  # without server-side lineage
    assert s.record_receipt(child, ack(op), lineage=['session-a-compressed', 'session-a'])['receipt'][
        'consumer'] == child
    assert s.record_receipt(owner, ack(op))['receipt']['consumer'] == child  # idempotent across the rotation


def test_cross_gateway_same_ids_are_isolated(tmp_path):
    s, owner, r, op = delivered(tmp_path)
    other = dict(OWNER, gatewayId='other-gateway')
    with pytest.raises(core.ActionError) as err: s.record_receipt(other, ack(op))
    assert err.value.status == 404
    with pytest.raises(core.ActionError): s.receipts(other, op['operationId'])
    o2 = s.create(other, dict(choice_request(owner=other)))
    busy_decided(s, o2)
    q = descriptor([('Which runner?', ('canary', 'aws'))])
    assert early_provider(s, tmp_path)('session-a', 'call_1', question=q) is None  # provider owner is test-gateway


@pytest.mark.parametrize('session,receipt', [
    ('session-a', {'status': 'accepted', 'user_row_id': 7, 'message_uid': 'm-1'}),            # current TUI runtime
    ('local-abc', {'status': 'accepted', 'transport': 'gateway_fifo', 'admission_id': 'a-1'}),  # local gateway FIFO
])
def test_tui_and_gateway_fifo_admission_are_delivery_not_acknowledgment(tmp_path, session, receipt):
    owner = dict(OWNER, storedSessionId=session)
    s = store(tmp_path); r = s.create(owner, dict(request(), owner=owner))
    op = s.decide(owner, decision(r))['operation']
    calls = []

    def rpc(method, params):
        calls.append(method)
        if method == 'prompt.admission.get' and len(calls) == 1:
            return {'status': 'absent'}
        assert 'Owner receipt' in params.get('text', 'Owner receipt') and op['binding']['scopeDigest'] in params.get(
            'text', op['binding']['scopeDigest'])
        return dict(receipt, **{k: params[k] for k in ('profile', 'stored_session_id', 'operation_id',
                                                       'payload_digest')})
    runs = prompt_delivery.PromptAdmission(rpc=rpc)
    out = s.dispatch(owner, op['operationId'], cards.DecisionDelivery(tmp_path / 'inbox.db', runs=runs,
                                                                     card_receiver=cards.QualifiedCardDelivery()))
    assert out['operation']['dispatchState'] == 'delivered' and out['operation']['receipt']['executionStarted'] is False
    assert s.receipts(owner, op['operationId'])['phase'] == 'unknown'


def test_pre_v1_operation_keeps_its_exact_admission_text(tmp_path):
    """Changing the admitted text of an in-flight old operation would change its admission digest."""
    s = store(tmp_path); r = s.create(OWNER, request())
    op = s.decide(OWNER, decision(r))['operation']
    legacy = {k: v for k, v in op.items() if k != 'binding'}
    assert 'Owner receipt' not in prompt_delivery.PromptAdmission._params(legacy)['text']
    assert 'Owner receipt' in prompt_delivery.PromptAdmission._params(op)['text']


# ---- the agent tool entry path ------------------------------------------------------------------------------------

def test_action_request_receipt_tool_uses_the_server_owner_only(tmp_path, monkeypatch):
    plugin = load_module('bound_init', ROOT / '__init__.py')
    s, owner, r, op = delivered(tmp_path)
    monkeypatch.setattr(plugin.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(plugin.core, 'lineage', lambda sid, db=None: [sid])
    monkeypatch.setattr(plugin.core, 'ActionStore', lambda *a, **k: s)
    out = json.loads(plugin.publish_action_request({'receipt': ack(op)}, session_id='session-a'))
    assert out['consumer']['phase'] == 'acknowledged' and out['consumer']['grants'] == []
    forged = json.loads(plugin.publish_action_request({'receipt': dict(ack(op), owner=OWNER)}, session_id='session-a'))
    assert forged['status'] == 422  # caller-supplied owner authority is rejected
    other = json.loads(plugin.publish_action_request({'receipt': ack(op)}, session_id='session-b'))
    assert other['status'] == 403
    both = json.loads(plugin.publish_action_request({'receipt': ack(op), 'withdraw': {}}, session_id='session-a'))
    assert both['status'] == 422
    assert 'receipt' in plugin.PRODUCER_SCHEMA['parameters']['properties']


def test_receipts_read_route_is_read_only(tmp_path):
    pytest.importorskip('fastapi'); from fastapi.testclient import TestClient; from fastapi import FastAPI
    api = load_module('bound_plugin_api', ROOT / 'dashboard' / 'plugin_api.py')
    s, owner, r, op = delivered(tmp_path)
    app = FastAPI()
    app.include_router(api.create_router(ledger_dir=tmp_path, state_db=tmp_path / 'state.db', action_store=s,
                                         owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid)))
    client = TestClient(app)
    got = client.get(f"/operations/{op['operationId']}/receipts", params={'session': 'session-a'}).json()
    assert got['phase'] == 'unknown' and got['receipts'] == []
    assert client.post(f"/operations/{op['operationId']}/receipts", params={'session': 'session-a'},
                       json=ack(op)).status_code in (404, 405)
    assert 'receipts' in client.get('/ping').json()['features']


# ---- review findings: render-time clarify binding --------------------------------------------------------------

def test_look_alike_request_cannot_answer_a_clarify_bound_to_another(tmp_path):
    """Two requests with the same title/labels: the clarify binds at first display, never to the newer look-alike."""
    s = store(tmp_path)
    a = s.create(OWNER, choice_request('Which runner?', ('canary', 'aws')))
    provider = early_provider(s, tmp_path)
    q = descriptor([('Which runner?', ('canary', 'aws'))], 'call_x')
    assert provider('session-a', 'call_x', question=q) is None  # displayed: bound to A (undecided)
    b = s.create(OWNER, choice_request('WHICH  runner?', ('Canary', 'AWS')))  # same key, created later
    busy_decided(s, b)  # decision on B only
    assert provider('session-a', 'call_x', question=q) is None
    busy_decided(s, a, 1)
    found = provider('session-a', 'call_x', question=q)
    assert [d['requestId'] for d in found.result['decisions']] == [a['requestId']]


def test_two_look_alikes_at_first_display_are_ambiguous_and_never_answered(tmp_path):
    s = store(tmp_path)
    a = s.create(OWNER, choice_request()); b = s.create(OWNER, choice_request())
    busy_decided(s, a)
    q = descriptor([('Which runner?', ('canary', 'aws'))], 'call_y')
    assert early_provider(s, tmp_path)('session-a', 'call_y', question=q) is None
    with s._read() as c:
        body = json.loads(c.execute("SELECT body FROM question_bindings WHERE request='clarify:call_y'").fetchone()[0])
    assert body['source'] == {'type': 'clarify', 'toolCallId': 'call_y'} and body['matches'][0]['ambiguous'] is True
    busy_decided(s, b)
    assert early_provider(s, tmp_path)('session-a', 'call_y', question=q) is None  # frozen, still ambiguous


def test_multi_question_clarify_needs_every_question_answered(tmp_path):
    s = store(tmp_path)
    a = s.create(OWNER, choice_request('Which runner?', ('canary', 'aws')))
    b = s.create(OWNER, choice_request('Which region?', ('east', 'west')))
    q = descriptor([('Which runner?', ('canary', 'aws')), ('Which region?', ('east', 'west'))], 'call_m')
    provider = early_provider(s, tmp_path)
    busy_decided(s, a)
    assert provider('session-a', 'call_m', question=q) is None  # a partial answer would drop "Which region?"
    busy_decided(s, b, 1)
    found = provider('session-a', 'call_m', question=q)
    assert {d['requestId'] for d in found.result['decisions']} == {a['requestId'], b['requestId']}
    q2 = descriptor([('Which runner?', ('canary', 'aws')), ('Unrelated?', ('y', 'n'))], 'call_n')
    assert provider('session-a', 'call_n', question=q2) is None


def test_clarify_binding_is_immutable_per_tool_call(tmp_path):
    s = store(tmp_path); s.create(OWNER, choice_request())
    q = descriptor([('Which runner?', ('canary', 'aws'))], 'call_i')
    first = s.bind_clarify(OWNER, q, lambda sid: True)
    assert s.bind_clarify(OWNER, q, lambda sid: True) == first
    with pytest.raises(core.ActionError) as err:
        s.bind_clarify(OWNER, descriptor([('Other?', ('x',))], 'call_i'), lambda sid: True)
    assert err.value.status == 409


def test_numbered_list_is_found_from_a_compression_continuation(tmp_path):
    s = store(tmp_path); a = s.create(OWNER, request())
    s.record_list(OWNER, 'msg-1', [dict(requestId=a['requestId'], choiceId='approve')])
    child = dict(OWNER, storedSessionId='session-a2')
    with pytest.raises(core.ActionError): s.resolve_numbered(child, 'msg-1', 1)
    got = s.resolve_numbered(child, 'msg-1', 1, lineage=['session-a2', 'session-a'])
    assert got == {'requestId': a['requestId'], 'expectedRevision': 1, 'choiceId': 'approve'}


def test_numbered_tool_entry_maps_but_never_decides(tmp_path, monkeypatch):
    plugin = load_module('bound_init_numbered', ROOT / '__init__.py')
    s = store(tmp_path); a = s.create(OWNER, request())
    monkeypatch.setattr(plugin.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(plugin.core, 'lineage', lambda sid, db=None: [sid])
    monkeypatch.setattr(plugin.core, 'ActionStore', lambda *a, **k: s)
    rec = json.loads(plugin.publish_action_request({'list': {'sourceId': 'm1', 'entries': [
        {'requestId': a['requestId'], 'choiceId': 'approve'}]}}, session_id='session-a'))
    assert rec['entries'][0]['n'] == 1
    out = json.loads(plugin.publish_action_request({'numbered': {'sourceId': 'm1', 'n': 1}}, session_id='session-a'))
    assert out['authorizes'] is False and out['maps_to']['choiceId'] == 'approve'
    assert s.get(OWNER, a['requestId'])['state'] == 'open' and s.undelivered('test-gateway', 'default') == []
    bad = json.loads(plugin.publish_action_request({'numbered': {'sourceId': '', 'n': 1}}, session_id='session-a'))
    assert bad['status'] == 422


# ---- durable application reservation (corrective review of 0286fccd) --------------------------------------------
# A fake downstream system with its own durable store, keyed by applicationKey: the only real effect counter. Without
# a reservation nothing separates "agent acts" from "receipt stored", so these tests drive the full consumer protocol.

class DurableSink:
    def __init__(self, path):
        import sqlite3
        self.path = path
        with sqlite3.connect(path) as c:
            c.execute('CREATE TABLE IF NOT EXISTS effects (key TEXT PRIMARY KEY, n INTEGER)')

    def has(self, key):
        import sqlite3
        with sqlite3.connect(self.path) as c:
            return c.execute('SELECT 1 FROM effects WHERE key=?', (key,)).fetchone() is not None

    def apply(self, key):
        """Idempotent downstream write: a second call with the same key is absorbed."""
        import sqlite3
        with sqlite3.connect(self.path, timeout=30) as c:
            c.execute('INSERT OR IGNORE INTO effects VALUES (?,1)', (key,))

    def total(self):
        import sqlite3
        with sqlite3.connect(self.path) as c:
            return c.execute('SELECT COUNT(*) FROM effects').fetchone()[0]


class Crash(BaseException):
    pass


def consume(tmp_path, owner, op, sink, crash=None):
    """The consumer protocol the prompt prescribes: ack -> reserve -> (effect only if new) -> applied receipt.
    A repeat caller (mustVerify) reads the downstream back and never repeats an effect it cannot prove absent."""
    s = store(tmp_path)  # a fresh store per call = a restarted process
    s.record_receipt(owner, ack(op))
    res = s.record_receipt(owner, reserve(op))
    key = res['applicationKey']
    if crash == 'after-reserve': raise Crash()
    if res['reservation'] == 'new':
        sink.apply(key)
        if crash == 'after-effect': raise Crash()
    else:
        assert res['mustVerify'] is True
        if not sink.has(key):
            return 'uncertain'  # non-idempotent work whose result is unknown is never repeated
    s.record_receipt(owner, applied(op))
    return 'applied'


def test_reservation_is_new_once_and_every_repeat_must_verify(tmp_path):
    s, owner, r, op = delivered(tmp_path)
    with pytest.raises(core.ActionError) as err: s.record_receipt(owner, reserve(op))
    assert err.value.status == 409  # reserve only after the exact revision is acknowledged
    s.record_receipt(owner, ack(op))
    first = s.record_receipt(owner, reserve(op))
    assert first['reservation'] == 'new' and first['mustVerify'] is False
    key = first['applicationKey']
    assert key == core._sha([s._ns(owner), op['operationId'], op['revision'], core.scope_digest(op['intent']['request'])])
    again = store(tmp_path).record_receipt(owner, reserve(op))
    assert again['reservation'] == 'existing' and again['mustVerify'] is True and again['applicationKey'] == key
    assert again['receipt'] == first['receipt']
    view = s.receipts(owner, op['operationId'])
    assert view['phase'] == 'uncertain' and view['applicationKey'] == key  # reserved only: outcome unknown
    assert [x['phase'] for x in view['receipts']] == ['acknowledged', 'reserved']


def test_concurrent_reservations_have_exactly_one_new(tmp_path):
    s, owner, r, op = delivered(tmp_path)
    s.record_receipt(owner, ack(op))
    with ThreadPoolExecutor(8) as pool:
        out = list(pool.map(lambda _: store(tmp_path).record_receipt(owner, reserve(op)), range(8)))
    assert sorted(x['reservation'] for x in out) == ['existing'] * 7 + ['new']
    assert all(x['mustVerify'] is (x['reservation'] == 'existing') for x in out)
    assert len({x['applicationKey'] for x in out}) == 1
    with s._read() as c:
        assert c.execute("SELECT COUNT(*) FROM owner_receipts WHERE phase='reserved'").fetchone()[0] == 1


def test_applied_without_a_reservation_is_denied_for_bound_operations(tmp_path):
    s, owner, r, op = delivered(tmp_path)
    s.record_receipt(owner, ack(op))
    with pytest.raises(core.ActionError) as err: s.record_receipt(owner, applied(op))
    assert err.value.status == 409 and 'reserv' in err.value.args[0].lower()
    assert s.receipts(owner, op['operationId'])['phase'] == 'acknowledged'


def test_legacy_unbound_operations_stay_unknown_and_are_never_backfilled(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    with s._tx() as c: c.execute('DELETE FROM question_bindings WHERE 1=1')
    op = s.decide(OWNER, decision(r))['operation']
    s.dispatch(OWNER, op['operationId'], Receiver())
    assert 'binding' not in op and s.receipts(OWNER, op['operationId'])['phase'] == 'unknown'
    with s._read() as c:
        assert c.execute('SELECT COUNT(*) FROM owner_receipts').fetchone()[0] == 0
    s.record_receipt(OWNER, ack(op))
    assert s.receipts(OWNER, op['operationId'])['phase'] == 'acknowledged'  # no reservation invented
    with pytest.raises(core.ActionError): s.record_receipt(OWNER, reserve(op))  # legacy rows cannot reserve


def test_stale_revised_requests_cannot_reserve(tmp_path):
    s, owner, r, op = delivered(tmp_path)
    s.record_receipt(owner, ack(op))
    s.revise(owner, r['requestId'], 1, dict(r, title='Synthetic change with more scope'))
    with pytest.raises(core.ActionError) as err: s.record_receipt(owner, reserve(op))
    assert err.value.status == 409
    assert s.receipts(owner, op['operationId'])['phase'] == 'acknowledged'
    with s._read() as c:
        assert c.execute("SELECT COUNT(*) FROM owner_receipts WHERE phase='reserved'").fetchone()[0] == 0


def test_reservation_survives_a_revision_so_the_lost_application_is_still_verifiable(tmp_path):
    s, owner, r, op = delivered(tmp_path)
    s.record_receipt(owner, ack(op)); first = s.record_receipt(owner, reserve(op))
    s.revise(owner, r['requestId'], 1, dict(r, title='Synthetic change with more scope'))
    again = store(tmp_path).record_receipt(owner, reserve(op))  # read-only repeat, no new reservation
    assert again['reservation'] == 'existing' and again['mustVerify'] is True
    assert again['applicationKey'] == first['applicationKey']
    with pytest.raises(core.ActionError) as err: s.record_receipt(owner, applied(op))  # existing stale rejection holds
    assert err.value.status == 409


@pytest.mark.parametrize('extra', [{'applicationKey': 'forged'}, {'evidenceVerified': True}, {'evidenceSource': 'verified'},
                                   {'reservation': 'new'}, {'mustVerify': False}, {'verified': True}])
def test_receipt_fields_owned_by_the_server_cannot_be_spoofed(tmp_path, extra):
    s, owner, r, op = delivered(tmp_path)
    s.record_receipt(owner, ack(op)); s.record_receipt(owner, reserve(op))
    for body in (dict(reserve(op), **extra), dict(applied(op), **extra)):
        with pytest.raises(core.ActionError) as err: s.record_receipt(owner, body)
        assert err.value.status == 422
    with pytest.raises(core.ActionError) as err:
        s.record_receipt(owner, dict(applied(op), evidence=[dict(EVIDENCE[0], verified=True)]))
    assert err.value.status == 422


def test_applied_evidence_is_server_labelled_agent_reported_unverified_and_key_bound(tmp_path):
    s, owner, r, op = delivered(tmp_path)
    s.record_receipt(owner, ack(op)); res = s.record_receipt(owner, reserve(op))
    done = s.record_receipt(owner, applied(op))
    rec = done['receipt']
    assert rec['evidenceSource'] == 'agent_reported' and rec['evidenceVerified'] is False
    assert rec['applicationKey'] == res['applicationKey'] and done['consumer']['phase'] == 'applied'
    assert done['consumer']['evidenceVerified'] is False and done['consumer']['grants'] == []


def test_crash_after_reserve_before_effect_never_repeats_unknown_work(tmp_path):
    sink = DurableSink(tmp_path / 'sink.db')
    s, owner, r, op = delivered(tmp_path)
    with pytest.raises(Crash): consume(tmp_path, owner, op, sink, crash='after-reserve')
    assert sink.total() == 0
    assert consume(tmp_path, owner, op, sink) == 'uncertain'  # restarted: reservation exists, effect unprovable
    assert sink.total() == 0  # no safe repeat was claimed
    view = store(tmp_path).receipts(owner, op['operationId'])
    assert view['phase'] == 'uncertain' and [x['phase'] for x in view['receipts']] == ['acknowledged', 'reserved']


def test_crash_after_effect_before_receipt_recovers_only_the_missing_phase(tmp_path):
    sink = DurableSink(tmp_path / 'sink.db')
    s, owner, r, op = delivered(tmp_path)
    with pytest.raises(Crash): consume(tmp_path, owner, op, sink, crash='after-effect')
    assert sink.total() == 1
    assert store(tmp_path).receipts(owner, op['operationId'])['phase'] == 'uncertain'  # effect happened, unrecorded
    assert consume(tmp_path, owner, op, sink) == 'applied'  # restart reads the sink back by applicationKey
    assert sink.total() == 1  # exactly one real effect
    view = store(tmp_path).receipts(owner, op['operationId'])
    assert view['phase'] == 'applied' and view['receipts'][-1]['applicationKey'] == view['applicationKey']
    assert sink.has(view['applicationKey'])


def test_two_concurrent_consumers_produce_exactly_one_effect(tmp_path):
    sink = DurableSink(tmp_path / 'sink.db')
    s, owner, r, op = delivered(tmp_path)
    with ThreadPoolExecutor(6) as pool:
        out = list(pool.map(lambda _: consume(tmp_path, owner, op, sink), range(6)))
    assert sink.total() == 1 and 'applied' in out
    assert set(out) <= {'applied', 'uncertain'}  # a loser that cannot yet see the effect reports uncertain


def test_reserved_phase_tool_schema_and_prompt(tmp_path):
    plugin = load_module('bound_init2', ROOT / '__init__.py')
    enum = plugin.PRODUCER_SCHEMA['parameters']['properties']['receipt']['properties']['phase']['enum']
    assert enum == ['acknowledged', 'reserved', 'applied', 'not_applied']
    s, owner, r, op = delivered(tmp_path)
    text = prompt_delivery.PromptAdmission._params(op)['text']
    for needle in ('reserved', 'applicationKey', 'idempotency', 'uncertain', 'never repeat'):
        assert needle.lower() in text.lower(), needle
    low = text.lower()
    assert low.index('acknowledged') < low.index('reserved') < low.index('"applied"')


# ---- authenticated snapshot-bound numbered answers (route + store + renderer contract) --------------------------

def _lists_client(tmp_path, s):
    pytest.importorskip('fastapi'); from fastapi.testclient import TestClient; from fastapi import FastAPI
    api = load_module('bound_plugin_api_lists', ROOT / 'dashboard' / 'plugin_api.py')
    app = FastAPI()
    app.include_router(api.create_router(ledger_dir=tmp_path, state_db=tmp_path / 'state.db', action_store=s,
                                         receiver=Receiver(),
                                         owner_resolver=lambda sid: dict(OWNER, storedSessionId=sid)))
    return TestClient(app)


def _two_lists(s):
    a = s.create(OWNER, choice_request('Runner?', ('canary', 'aws')))
    b = s.create(OWNER, choice_request('Region?', ('us-east-1', 'eu-west-1')))
    first = s.record_list(OWNER, 'msg-1', [dict(requestId=a['requestId'], choiceId='c0'),
                                           dict(requestId=b['requestId'], choiceId='c1')])
    second = s.record_list(OWNER, 'msg-2', [dict(requestId=b['requestId'], choiceId='c1'),
                                            dict(requestId=a['requestId'], choiceId='c0')])
    return a, b, first, second


def _post(client, source, **kw):
    body = dict(session='session-a', snapshotDigest=None, n=1, idempotencyKey=str(uuid.uuid4()))
    body.update(kw)
    return client.post(f'/lists/{source}/answers', json=body)


def test_list_snapshot_entries_freeze_the_question_title(tmp_path):
    s = store(tmp_path); a = s.create(OWNER, choice_request('Runner?', ('canary', 'aws')))
    snap = s.record_list(OWNER, 'msg-1', [dict(requestId=a['requestId'], choiceId='c1')])
    assert snap['entries'][0]['title'] == 'Runner?' and snap['entries'][0]['label'] == 'aws'


def test_list_route_returns_the_exact_session_snapshot_only(tmp_path):
    s = store(tmp_path); a, b, first, _ = _two_lists(s); client = _lists_client(tmp_path, s)
    got = client.get('/lists/msg-1', params={'session': 'session-a'})
    assert got.status_code == 200 and got.json()['list']['digest'] == first['digest']
    assert [e['title'] for e in got.json()['list']['entries']] == ['Runner?', 'Region?']
    assert client.get('/lists/msg-1', params={'session': 'session-b'}).status_code == 404  # other session
    assert client.get('/lists/nope', params={'session': 'session-a'}).status_code == 404


def test_list_route_does_not_authorize_through_lineage(tmp_path):
    s = store(tmp_path); _two_lists(s); client = _lists_client(tmp_path, s)
    # a compression continuation is a different stored session: the list is not found there
    assert client.get('/lists/msg-1', params={'session': 'session-a2'}).status_code == 404
    assert _post(client, 'msg-1', session='session-a2', snapshotDigest='x', n=1).status_code == 404


def test_delayed_numbered_answer_maps_to_the_original_list_and_decides_once(tmp_path):
    s = store(tmp_path); a, b, first, second = _two_lists(s); client = _lists_client(tmp_path, s)
    key = str(uuid.uuid4())
    got = _post(client, 'msg-1', snapshotDigest=first['digest'], n=2, idempotencyKey=key)  # "2" of the FIRST list
    assert got.status_code == 200, got.text
    op = got.json()['operation']
    assert op['requestId'] == b['requestId'] and op['intent']['revisionText'] == 'eu-west-1'
    assert op['decision']['choiceId'] == 'c1' and op['intent']['permissionGrants'] == []
    assert op['provenance'] == {'type': 'numbered_list', 'sourceId': 'msg-1', 'snapshotDigest': first['digest'], 'n': 2}
    again = _post(client, 'msg-1', snapshotDigest=first['digest'], n=2, idempotencyKey=key)
    assert again.status_code == 200 and again.json()['operation']['operationId'] == op['operationId']
    # the replay also works after the request is decided (list is now stale) and after a new store handle (crash)
    s2 = core.ActionStore(s.path)
    assert s2.answer_numbered(OWNER, 'msg-1', first['digest'], 2, key)['operation']['operationId'] == op['operationId']
    # a conflicting reuse of the key fails; so does a direct decide with the same key
    assert _post(client, 'msg-1', snapshotDigest=first['digest'], n=1, idempotencyKey=key).status_code == 409
    assert _post(client, 'msg-2', snapshotDigest=second['digest'], n=1, idempotencyKey=key).status_code == 409


def test_numbered_answer_rejections_record_no_decision(tmp_path):
    s = store(tmp_path); a, b, first, second = _two_lists(s); client = _lists_client(tmp_path, s)
    bad = [
        _post(client, 'msg-1', snapshotDigest='0' * 64, n=1),             # wrong digest
        _post(client, 'msg-1', snapshotDigest=second['digest'], n=1),     # another list's digest
        _post(client, 'msg-1', snapshotDigest=first['digest'], n=3),      # no such option
        _post(client, 'msg-1', snapshotDigest=first['digest'], n=0),
        _post(client, 'msg-1', snapshotDigest=first['digest'], n=True),   # strict int
        _post(client, 'msg-1', snapshotDigest=first['digest'], n='1'),
        _post(client, 'unknown', snapshotDigest=first['digest'], n=1),    # unknown list
        _post(client, 'msg-1', snapshotDigest=first['digest'], n=1, session='session-b'),  # wrong owner
        _post(client, 'msg-1', snapshotDigest=first['digest'], n=1, owner=OWNER),          # caller owner field
        _post(client, 'msg-1', snapshotDigest=first['digest'], n=1, choiceId='c0'),        # caller choice
        _post(client, 'msg-1', snapshotDigest=first['digest'], n=1, expectedRevision=1),
    ]
    assert [r.status_code for r in bad] == [409, 409, 422, 422, 422, 422, 404, 404, 422, 422, 422]
    s.revise(OWNER, a['requestId'], 1, dict(a, title='Synthetic change v2'))
    assert _post(client, 'msg-1', snapshotDigest=first['digest'], n=1).status_code == 409  # revision moved on
    with s._read() as c: assert c.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == 0


def test_concurrent_duplicate_numbered_answers_share_one_operation(tmp_path):
    s = store(tmp_path); a, b, first, _ = _two_lists(s); key = str(uuid.uuid4())
    def go(_): return core.ActionStore(s.path).answer_numbered(OWNER, 'msg-1', first['digest'], 1, key)['operation']['operationId']
    with ThreadPoolExecutor(8) as pool: ids = list(pool.map(go, range(8)))
    assert len(set(ids)) == 1
    with s._read() as c: assert c.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == 1
    # two different keys for the same revision: the second loses, one decision stands
    with pytest.raises(core.ActionError): s.answer_numbered(OWNER, 'msg-1', first['digest'], 1, 'other-key')


def test_lineage_snapshots_sharing_a_source_id_are_ambiguous(tmp_path):
    s = store(tmp_path); a = s.create(OWNER, choice_request('Runner?', ('canary', 'aws')))
    child = dict(OWNER, storedSessionId='session-a2')
    s.record_list(OWNER, 'msg-1', [dict(requestId=a['requestId'], choiceId='c0')])
    ac = s.create(child, choice_request('Runner?', ('canary', 'aws'), owner=child))
    s.record_list(child, 'msg-1', [dict(requestId=ac['requestId'], choiceId='c1')])
    with pytest.raises(core.ActionError) as err: s.resolve_numbered(child, 'msg-1', 1, lineage=['session-a2', 'session-a'])
    assert err.value.status == 409 and 'ambiguous' in str(err.value).lower()


def test_numbered_tool_stays_non_authorizing_and_list_response_gives_marker_after_persistence(tmp_path, monkeypatch):
    plugin = load_module('bound_init_marker', ROOT / '__init__.py')
    s = store(tmp_path); a = s.create(OWNER, choice_request('Runner?', ('canary', 'aws')))
    monkeypatch.setattr(plugin.core, 'ActionStore', lambda *a_, **k: s)
    monkeypatch.setattr(plugin.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(plugin.core, 'lineage', lambda sid, db=None: [sid])
    out = json.loads(plugin.publish_action_request(
        {'list': {'sourceId': 'm1', 'entries': [dict(requestId=a['requestId'], choiceId='c0')]}}, session_id='session-a'))
    assert out["marker"] == '::decision-list{id="m1" d="%s"}' % out['digest']
    assert 'typed' in out['instructions'] and 'not' in out['instructions']
    bad = json.loads(plugin.publish_action_request(
        {'list': {'sourceId': 'm2', 'entries': [dict(requestId='nope', choiceId='c0')]}}, session_id='session-a'))
    assert 'marker' not in bad  # no marker without persistence
    num = json.loads(plugin.publish_action_request({'numbered': {'sourceId': 'm1', 'n': 1}}, session_id='session-a'))
    assert num['authorizes'] is False


# ---- 9. frozen request display in numbered lists --------------------------------------------------------------------

def _legacy_list(tmp_path, s, entries, source='legacy-1'):
    """A list recorded before entries carried display data (digest over the entries exactly as they were stored)."""
    body = {'schemaVersion': core.BINDING_SCHEMA, 'owner': OWNER, 'source': {'type': 'message', 'id': source},
            'entries': entries}
    body['digest'] = core._sha(body)
    with s._tx() as c:
        c.execute('INSERT INTO list_snapshots VALUES (?,?,?,?,?)',
                  (s._ns(OWNER), OWNER['storedSessionId'], source, body['digest'], core._json(body)))
    return body


def _legacy_entry(s, r, choice_id, n=1):
    c = next(x for x in r['choices'] if x['id'] == choice_id)
    b = s.binding(OWNER, r['requestId'])
    return {'n': n, 'requestId': r['requestId'], 'revision': r['revision'], 'choiceId': c['id'], 'label': c['label'],
            'effect': c['effect'], 'scopeDigest': b['scopeDigest'], 'title': r['title']}


def test_numbered_entries_freeze_the_complete_request_display(tmp_path):
    s = store(tmp_path); req = request(); req['limits'] = [dict(key='spend', value='none', permissionRequired=True)]
    r = s.create(OWNER, req)
    snap = s.record_list(OWNER, 'msg-d', [dict(requestId=r['requestId'], choiceId='approve')])
    d = snap['entries'][0]['display']
    assert d['scopeMarkdown'] == 'Change example.py; run correctness tests; deliver patch.' and d['scopeComplete'] is True
    assert d['limits'] == [dict(key='spend', value='none', permissionRequired=True)] and d['exclusions'] == ['No training']
    assert d['outcomes']['approve'] == 'Authorize this scope only'
    assert d['outcomes']['decline'] == 'Cancel request'


def test_display_is_part_of_the_snapshot_digest(tmp_path):
    a = request(); b = dict(a, limits=[dict(key='spend', value='$100')])
    (tmp_path / 'x').mkdir(); (tmp_path / 'y').mkdir()
    sa, sb = store(tmp_path / 'x'), store(tmp_path / 'y')
    ra, rb = sa.create(OWNER, a), sb.create(OWNER, dict(b, requestId=a['requestId']))
    da = sa.record_list(OWNER, 'm', [dict(requestId=ra['requestId'], choiceId='approve')])
    db = sb.record_list(OWNER, 'm', [dict(requestId=rb['requestId'], choiceId='approve')])
    assert da['digest'] != db['digest']


def test_frozen_display_survives_a_revision_and_the_stale_answer_authorizes_nothing(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    snap = s.record_list(OWNER, 'msg-r', [dict(requestId=r['requestId'], choiceId='approve')])
    s.revise(OWNER, r['requestId'], 1, dict(r, scope=dict(r['scope'], markdown='A much larger replacement scope'),
                                            limits=[dict(key='spend', value='$10000')]))
    shown = s.list_snapshot(OWNER, 'msg-r')['entries'][0]
    assert shown['display']['scopeMarkdown'].startswith('Change example.py') and shown['display']['limits'] == [
        dict(key='spend', value='none')] and shown['open'] is False  # the ORIGINAL data, never the revision
    with pytest.raises(core.ActionError) as err: s.answer_numbered(OWNER, 'msg-r', snap['digest'], 1, str(uuid.uuid4()))
    assert err.value.status == 409


def test_legacy_list_without_display_fails_closed_for_authorizing_choices(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    legacy = _legacy_list(tmp_path, s, [_legacy_entry(s, r, 'approve')])
    with pytest.raises(core.ActionError) as err: s.answer_numbered(OWNER, 'legacy-1', legacy['digest'], 1, str(uuid.uuid4()))
    assert err.value.status == 409 and 'display' in str(err.value).lower()
    with pytest.raises(core.ActionError) as err: s.resolve_numbered(OWNER, 'legacy-1', 1)
    assert err.value.status == 409
    with s._read() as c:
        assert c.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == 0
    assert s.get(OWNER, r['requestId'])['state'] == 'open'


def test_legacy_authorizing_answer_choice_also_fails_closed(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, choice_request('Runner?', ('canary', 'aws')))
    legacy = _legacy_list(tmp_path, s, [_legacy_entry(s, r, 'c0')])
    with pytest.raises(core.ActionError) as err: s.answer_numbered(OWNER, 'legacy-1', legacy['digest'], 1, str(uuid.uuid4()))
    assert err.value.status == 409


def test_legacy_nonauthorizing_decline_stays_compatible(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    legacy = _legacy_list(tmp_path, s, [_legacy_entry(s, r, 'decline')])
    op = s.answer_numbered(OWNER, 'legacy-1', legacy['digest'], 1, str(uuid.uuid4()))['operation']
    assert op['intent']['effect'] == 'decline' and op['intent']['executionAuthorized'] is False
    assert s.list_snapshot(OWNER, 'legacy-1')['entries'][0].get('display') is None


def test_list_snapshot_marks_legacy_authorizing_entries_unanswerable(tmp_path):
    s = store(tmp_path); r = s.create(OWNER, request())
    _legacy_list(tmp_path, s, [_legacy_entry(s, r, 'approve', 1), _legacy_entry(s, r, 'decline', 2)])
    e = s.list_snapshot(OWNER, 'legacy-1')['entries']
    assert e[0]['open'] is False and e[1]['open'] is True


def test_prompt_and_hints_say_an_uncertain_reservation_is_left_alone():
    assert 'uncertain' in early_mod.RECEIPT_HINT.lower() and 'not_applied' in early_mod.RECEIPT_HINT
