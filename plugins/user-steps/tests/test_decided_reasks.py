"""Final-turn producer retirement, not a periodic cleanup or inferred approval."""
import json
import time

import pytest

from test_user_steps import add_session, core, fake_llm, home, ledger  # noqa: F401
from test_decisions import DurableReceiver


ASK = 'Nothing is built yet. I want your go on this plan first.'
TITLE = 'Approve building the session filter with category pills and tooltips'


def setup_reask(home):
    add_session(home, 'S1')
    lg, st, owner = ledger(home), core.ActionStore(), core.server_owner('S1')
    stamp = time.time() - 10
    question = core.add_step('S1', 'Create the session filter with category pills and tooltips?',
                             [{'label': 'Go as planned', 'value': 'go', 'authorizes': True},
                              {'label': 'Adjust', 'value': 'adjust'}], ledger=lg, now=stamp)
    canonical = st.migrate_step(owner, question)
    # The same extraction resolves the ledger question and mints the prose approval.
    out = core.process_reply(ASK, 'S1', ledger=lg, action_store=st, action_owner=owner,
                             llm=fake_llm({'steps': [{'text': TITLE, 'kind': 'approve',
                                                    'verbatim_span': ASK, 'options': []}],
                                           'resolved': [question['id']]}), now=stamp + 1)
    assert len(out['steps']) == 1
    step = out['steps'][0]
    assert lg.read('S1')['steps'][0]['status'] == 'resolved'
    assert st.get(owner, step['requestId'])['state'] == 'needs_context'
    return lg, st, owner, canonical, step


def deliver(st, owner, canonical, home, *, pending=False):
    choice = next(c for c in canonical['choices'] if c.get('authorizes'))
    op = st.decide(owner, dict(requestId=canonical['requestId'], expectedRevision=1,
                               choiceId=choice['id'], revisionText=choice['label'], idempotencyKey='go'))['operation']
    if not pending:
        receiver = DurableReceiver(home / 'receiver.json'); receiver.fail = False
        assert st.dispatch(owner, op['operationId'], receiver)['operation']['dispatchState'] == 'delivered'
    return op


def finish(lg, st, owner, *, covered=True, reply='Built and pushed the session filter.', renewed=False,
           on_coverage=None):
    def judge(system, user):
        if 'covered_reask' in system:
            data = json.loads(user)
            if on_coverage:
                on_coverage(data)
            return json.dumps({'covered_reask': covered,
                               'scope_quote': data['decision']['intent']['request']['scope']['markdown']})
        return json.dumps({'steps': ([{'text': TITLE, 'kind': 'approve', 'verbatim_span': ASK}]
                                    if renewed else []), 'resolved': []})
    return core.process_reply(reply, 'S1', ledger=lg, action_store=st, action_owner=owner, llm=judge)


def test_delivered_canonical_decision_retires_prose_reask_on_quiet_final(home):
    lg, st, owner, canonical, step = setup_reask(home)
    op = deliver(st, owner, canonical, home)
    before = st.operation(owner, op['operationId'])['operation']
    finish(lg, st, owner)
    assert st.operation(owner, op['operationId'])['operation'] == before
    assert lg.open_steps('S1') == []
    assert not [r for r in st.requests(owner) if r['state'] in ('open', 'needs_context')]
    retired = st.view(owner, step['requestId'])
    assert retired['state'] == 'withdrawn' and not retired.get('resolution')
    assert op['operationId'] in retired['withdrawal']['evidence']


@pytest.mark.parametrize('mode', ['absent', 'pending', 'different_scope', 'renewed', 'revised'])
def test_new_or_uncovered_prose_approval_stays_open(home, mode):
    lg, st, owner, canonical, step = setup_reask(home)
    if mode != 'absent':
        deliver(st, owner, canonical, home, pending=mode == 'pending')
    if mode == 'revised':
        r = st.get(owner, step['requestId'])
        st.revise(owner, r['requestId'], 1, r)
    finish(lg, st, owner, covered=mode != 'different_scope',
           reply=ASK if mode == 'renewed' else 'Built and pushed the session filter.', renewed=mode == 'renewed')
    assert st.get(owner, step['requestId'])['state'] == 'needs_context'
    assert any(s['id'] == step['id'] for s in lg.open_steps('S1'))


@pytest.mark.parametrize('race', ['source_revision', 'target_revision', 'target_decision'])
def test_reconciliation_races_keep_reask(home, race):
    lg, st, owner, canonical, step = setup_reask(home)
    deliver(st, owner, canonical, home)
    def change(_data):
        if race == 'target_decision':
            r = st.get(owner, step['requestId'])
            st.decide(owner, dict(requestId=r['requestId'], expectedRevision=1, choiceId='decline',
                                  idempotencyKey='decline'))
        else:
            rid = canonical['requestId'] if race == 'source_revision' else step['requestId']
            r = st.get(owner, rid)
            st.revise(owner, rid, 1, r)
    finish(lg, st, owner, on_coverage=change)
    assert st.get(owner, step['requestId'])['state'] == 'needs_context'
    assert lg.open_steps('S1')


@pytest.mark.parametrize('mode', ['old_decision', 'producer_authored', 'judge_error', 'non_authorizing'])
def test_only_later_authorization_and_untouched_extraction_qualify(home, mode):
    lg, st, owner, canonical, step = setup_reask(home)
    if mode == 'non_authorizing':
        choice = next(c for c in canonical['choices'] if not c.get('authorizes'))
        op = st.decide(owner, dict(requestId=canonical['requestId'], expectedRevision=1,
                                   choiceId=choice['id'], revisionText=choice['label'], idempotencyKey='adjust'))['operation']
        receiver = DurableReceiver(home / 'receiver.json'); receiver.fail = False
        st.dispatch(owner, op['operationId'], receiver)
    else:
        deliver(st, owner, canonical, home)
    if mode in ('old_decision', 'producer_authored'):
        with lg.edit(lg.root('S1')) as d:
            s = next(s for s in d['steps'] if s['id'] == step['id'])
            if mode == 'old_decision':
                s['created'] = time.time() + 10
            else:
                s['source'] = 'session_outcome'
    def judge_error(_data):
        raise RuntimeError('judge unavailable')
    finish(lg, st, owner, on_coverage=judge_error if mode == 'judge_error' else None)
    assert st.get(owner, step['requestId'])['state'] == 'needs_context'


def test_actual_new_scope_is_presented_to_judge_and_kept(home):
    lg, st, owner, canonical, step = setup_reask(home)
    deliver(st, owner, canonical, home)
    new = 'Approve deleting archived sessions after building the session filter'
    out = core.process_reply('Please approve deleting archived sessions.', 'S1', ledger=lg,
                             action_store=st, action_owner=owner,
                             llm=fake_llm({'steps': [{'text': new, 'kind': 'approve',
                                                    'verbatim_span': 'Please approve deleting archived sessions.'}],
                                           'resolved': []}))
    fresh = out['steps'][0]
    seen = []
    def check(data):
        seen.append(data['ask']['title'])
        assert 'deleting' not in data['decision']['intent']['request']['scope']['markdown']
    finish(lg, st, owner, covered=False, on_coverage=check)
    # The new ask postdates the decision, so it never even reaches semantic matching.
    assert seen == [TITLE]
    assert st.get(owner, fresh['requestId'])['state'] == 'needs_context'

