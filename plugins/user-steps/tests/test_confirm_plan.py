"""Same-message confirmation plans are frozen before any Go can authorize them."""
import json

import pytest

from test_decisions import core, OWNER, decision

PLAN = '## Build plan\n\n1. Update the example renderer to show the new control.\n2. Add regression tests and run the renderer suite.\n3. Deliver a reviewed patch. No deployment or new spend.'
ASK = 'Say **go** and I will start the build.'


def publish(tmp_path, reply, *, kind='approve', source=None):
    ledger = core.Ledger(tmp_path / 'ledger', db=tmp_path / 'missing.db')
    store = core.ActionStore(tmp_path / 'actions.sqlite3')
    if source:
        core.add_step(OWNER['storedSessionId'], ASK, ['Go', 'Change plan'], ledger=ledger)
    llm = lambda *_: json.dumps({'steps': [{'text': ASK, 'kind': kind, 'verbatim_span': ASK}], 'resolved': []})
    result = core.process_reply(reply, OWNER['storedSessionId'], ledger=ledger, llm=llm,
                                action_store=store, action_owner=OWNER)
    return store, store.requests(OWNER), result


def test_plan_then_say_go_freezes_scope_and_approval(tmp_path):
    store, requests, result = publish(tmp_path, PLAN + '\n\n' + ASK)
    assert len(requests) == 1
    r = requests[0]
    assert r['kind'] == 'approval' and r['state'] == 'open'
    assert r['scope']['complete'] is True
    assert PLAN in r['scope']['markdown']
    assert r['choices'] == [{'id': 'go', 'label': 'Go', 'effect': 'approve'},
                            {'id': 'change-plan', 'label': 'Change plan', 'effect': 'revise'}]
    assert '::user-step{' in result['text']
    op = store.decide(OWNER, decision(r, choice='go'))['operation']
    assert op['intent']['executionAuthorized'] is True
    assert PLAN in op['intent']['request']['scope']['markdown']
    assert op['decision']['choiceId'] == 'go'
    import importlib.util
    spec = importlib.util.spec_from_file_location('confirm_prompt', core.Path(core.__file__).with_name('prompt_delivery.py'))
    delivery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(delivery)
    params = delivery.PromptAdmission._params(op)
    assert params['text'].startswith('Go\n\nCanonical user decision.')
    assert 'No deployment or new spend' in params['text']


def test_session_outcome_confirm_in_same_final_message(tmp_path):
    _, requests, _ = publish(tmp_path, PLAN + '\n\n' + ASK, source='session_outcome')
    assert len(requests) == 1
    assert requests[0]['kind'] == 'approval'
    assert requests[0]['scope']['complete'] is True
    assert PLAN in requests[0]['scope']['markdown']
    assert requests[0]['choices'][0]['label'] == 'Go'


@pytest.mark.parametrize('prefix', ['', 'I have a plan.', '## Plan\n\nDetails later.', '```\n1. Update files.\n2. Run tests.\n```'])
def test_absent_or_nonplan_context_stays_incomplete(tmp_path, prefix):
    _, requests, _ = publish(tmp_path, prefix + '\n\n' + ASK)
    assert core.preceding_plan(prefix + '\n\n' + ASK, ASK) is None
    assert all(r['state'] == 'needs_context' for r in requests)
    assert not any(c['effect'] == 'approve' for r in requests for c in r['choices'])


def test_plan_after_ask_is_not_borrowed(tmp_path):
    _, requests, _ = publish(tmp_path, ASK + '\n\n' + PLAN)
    assert requests[0]['state'] == 'needs_context'


@pytest.mark.parametrize('reply,span', [
    (PLAN + '\n\n' + ASK + '\n' + ASK, ASK),
    (PLAN + '\n\n```\n' + ASK, ASK),
    (PLAN + '\n\n~~~\n' + ASK, ASK),
    (PLAN + '\n\nIs this another question?\n\n' + ASK, ASK),
    (PLAN + '\n\n::user-step{t="older ask"}\n\n' + ASK, ASK),
    (PLAN + '\n' + 'x' * 65536 + ASK, ASK),
    (PLAN + '\n\nWhat should the button look like?', 'What should the button look like?'),
])
def test_ambiguous_example_or_oversize_context_never_authorizes(reply, span):
    assert core.preceding_plan(reply, span) is None


@pytest.mark.parametrize('state', ['revised', 'decided'])
def test_final_reply_does_not_replace_producer_revision_or_decision(tmp_path, state):
    ledger = core.Ledger(tmp_path / 'ledger', db=tmp_path / 'missing.db')
    store = core.ActionStore(tmp_path / 'actions.sqlite3')
    step = core.add_step(OWNER['storedSessionId'], ASK, ['Go', 'Change plan'], ledger=ledger)
    request = store.migrate_step(OWNER, step)
    if state == 'revised':
        replacement = dict(request, limits=[{'key': 'spend', 'value': 'none'}])
        request = store.revise(OWNER, request['requestId'], 1, replacement)
    else:
        store.decide(OWNER, decision(request, choice=request['choices'][0]['id'], text=request['choices'][0]['label']))
        request = store.get(OWNER, request['requestId'])
    core.attach_confirmation_plans(PLAN + '\n\n' + ASK, OWNER['storedSessionId'], ledger, store, OWNER)
    assert store.get(OWNER, request['requestId']) == request


def test_repeated_final_transform_does_not_duplicate_confirmation(tmp_path):
    ledger = core.Ledger(tmp_path / 'ledger', db=tmp_path / 'missing.db')
    store = core.ActionStore(tmp_path / 'actions.sqlite3')
    core.add_step(OWNER['storedSessionId'], ASK, ['Go', 'Change plan'], ledger=ledger)
    llm = lambda *_: json.dumps({'steps': [{'text': ASK, 'kind': 'approve', 'verbatim_span': ASK}], 'resolved': []})
    for _ in range(2):
        core.process_reply(PLAN + '\n\n' + ASK, OWNER['storedSessionId'], ledger=ledger,
                           llm=llm, action_store=store, action_owner=OWNER)
    assert len(store.requests(OWNER)) == 1


def test_trailing_limits_are_frozen_too(tmp_path):
    limit = 'Limit: do not publish or deploy until a separate review.'
    _, requests, _ = publish(tmp_path, PLAN + '\n\n' + ASK + '\n\n' + limit)
    assert limit in requests[0]['scope']['markdown']


@pytest.mark.parametrize('source', [None, 'session_outcome'])
@pytest.mark.parametrize('before', [False, True])
def test_distinct_confirmation_never_widens_scope(tmp_path, source, before):
    other = 'Say yes to also deploy production.'
    reply = PLAN + '\n\n' + (other + '\n\n' + ASK if before else ASK + '\n\n' + other)
    _, requests, _ = publish(tmp_path, reply, source=source)
    assert not any(r['scope']['complete'] for r in requests)
    assert not any(c['effect'] == 'approve' for r in requests for c in r['choices'])


@pytest.mark.parametrize('source', [None, 'session_outcome'])
@pytest.mark.parametrize('opening,interior,closing', [
    ('````markdown', '```example', '````'),
    ('`````markdown', '````', '`````'),
    ('~~~~markdown', '~~~', '~~~~'),
    ('~~~~~markdown', '~~~~', '~~~~~'),
])
def test_fenced_confirmation_never_becomes_authorizing(tmp_path, source, opening, interior, closing):
    reply = PLAN + '\n\n' + '\n'.join([opening, interior, ASK, interior, closing])
    _, requests, _ = publish(tmp_path, reply, source=source)
    assert not any(r['scope']['complete'] for r in requests)
    assert not any(c['effect'] == 'approve' for r in requests for c in r['choices'])


def test_closed_code_in_plan_preserves_limits(tmp_path):
    code = '\n\n````text\n```example\nNo network writes.\n```\n````'
    _, requests, _ = publish(tmp_path, PLAN + code + '\n\n' + ASK)
    assert requests[0]['scope']['complete'] is True
    assert code in requests[0]['scope']['markdown']


def test_change_plan_is_non_authorizing_and_retains_exact_plan(tmp_path):
    store, requests, _ = publish(tmp_path, PLAN + '\n\n' + ASK)
    op = store.decide(OWNER, decision(requests[0], choice='change-plan', text='Use a smaller patch'))['operation']
    assert op['intent']['executionAuthorized'] is False
    assert PLAN in op['intent']['request']['scope']['markdown']
