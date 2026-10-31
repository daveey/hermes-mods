"""A status digest of asks that live in other chats is not a new For-you tile.

The incident was one paragraph listing the user's pending decisions elsewhere.
The extractor minted one tile per named decision in the chat that only reported
them. These tests use generic fixtures (example.com-style names, no live ids).
"""
import importlib.util
import json
import uuid
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = load_module('elsewhere_digest_core', ROOT / 'core.py')
OWNER = {'gatewayId': 'test-gateway', 'profile': 'default', 'storedSessionId': 'session-a'}
OTHER = dict(OWNER, storedSessionId='session-b')

# The incident paragraph, with generic host names. Each stub step is scoped to
# the whole paragraph, which is how the four tiles were minted.
DIGEST = (
    'Your "For you" queue now holds only real decisions:\n'
    '- **Host A:** whether to register a spare test runner; whether to approve a '
    'build machine for the app; when to run the gateway setup.\n'
    '- **Host B:** the model ID question.'
)
DIGEST_STEPS = [
    {'text': 'Decide whether to register a spare test runner on host A', 'kind': 'input',
     'verbatim_span': DIGEST, 'options': []},
    {'text': 'Decide whether to approve a build machine for the app', 'kind': 'input',
     'verbatim_span': DIGEST, 'options': []},
    {'text': 'Choose when to run the gateway setup', 'kind': 'choose',
     'verbatim_span': DIGEST, 'options': []},
    {'text': 'Answer the model ID question for host B', 'kind': 'input',
     'verbatim_span': DIGEST, 'options': []},
]
REAL_ASK = 'Can you approve deploying build 412?'


def env(tmp_path):
    return core.ActionStore(tmp_path / 'actions.sqlite3'), core.Ledger(tmp_path / 'ledger', tmp_path / 'missing.db')


def run(store, ledger, text, steps, *, elsewhere=None, repeats=None, seen=None, session='session-a', user_msg=''):
    payload = {'steps': steps, 'resolved': []}
    if elsewhere is not None:
        payload['elsewhere'] = elsewhere
    if repeats is not None:
        payload['repeats'] = repeats

    def llm(system, user):
        if seen is not None:
            seen.append(user)
        return json.dumps(payload)

    return core.process_reply(text, session, ledger=ledger, action_store=store, user_msg=user_msg,
                              action_owner=dict(OWNER, storedSessionId=session), llm=llm)


# A runtime notice started the turn: the reply relays an ask that lives in another chat (relay provenance).
NOTICE = '\u23f8 [default] @default Kanban t_aaaa1111 blocked: approve deploying build 412 to the test environment'


def titles(store, owner):
    return [r['title'] for r in store.requests(owner) if r['state'] in ('open', 'needs_context')]


def test_digest_paragraph_mints_nothing(tmp_path):
    store, ledger = env(tmp_path)
    out = run(store, ledger, DIGEST, DIGEST_STEPS)
    assert out['steps'] == []
    assert titles(store, OWNER) == []
    assert '::user-step' not in (out['text'] or '')


def test_digest_plus_a_separate_ask_mints_only_the_ask(tmp_path):
    store, ledger = env(tmp_path)
    reply = DIGEST + '\n\n' + REAL_ASK
    real = {'text': 'Approve deploying build 412', 'kind': 'approve',
            'verbatim_span': REAL_ASK, 'options': []}
    out = run(store, ledger, reply, DIGEST_STEPS + [real])
    assert [s['text'] for s in out['steps']] == ['Approve deploying build 412']
    assert titles(store, OWNER) == ['Approve deploying build 412']


def test_direct_request_inside_a_digest_paragraph_is_kept(tmp_path):
    # The span itself asks in this chat and does not contain a digest marker.
    store, ledger = env(tmp_path)
    reply = 'Your "For you" queue now holds one item. ' + REAL_ASK
    step = {'text': 'Approve deploying build 412', 'kind': 'approve',
            'verbatim_span': REAL_ASK, 'options': []}
    out = run(store, ledger, reply, [step])
    assert [s['text'] for s in out['steps']] == ['Approve deploying build 412']


def _approval(owner, title):
    return dict(schemaVersion=2, owner=owner.copy(), requestId=str(uuid.uuid4()), revision=1,
                kind='approval', title=title,
                scope=dict(markdown='Change example.py; run its tests.', complete=True, sourceRefs=[]),
                limits=[], exclusions=[],
                outcomes=dict(approve='Do it', revise='Block', decline='Cancel'),
                choices=[dict(id=e, label=l, effect=e) for e, l in
                         zip(('approve', 'revise', 'decline'), ('Approve', 'Revise', 'Decline'))],
                aliases=[], state='open')


def test_elsewhere_titles_reach_the_prompt_and_a_mapped_repeat_is_not_minted(tmp_path):
    store, ledger = env(tmp_path)
    oldest = 'Open ask 00 for the other chat session'
    rest = [f'Open ask {i:02d} for the other chat session' for i in range(1, 31)]
    store.create(OTHER, _approval(OTHER, oldest))
    for title in rest:
        store.create(OTHER, _approval(OTHER, title))
    repeated = rest[-1]
    sentence = repeated + '.'
    reply = 'Noted in passing. ' + sentence
    step = {'text': repeated, 'kind': 'input', 'verbatim_span': sentence, 'options': []}
    seen = []
    out = run(store, ledger, reply, [step],
              elsewhere=[{'title': repeated, 'verbatim_span': sentence}], seen=seen, user_msg=NOTICE)
    assert seen, 'the extractor was not called'
    prompt = seen[0]
    assert 'ELSEWHERE' in prompt
    for title in rest:
        assert title in prompt
    assert oldest not in prompt
    assert out['steps'] == []
    assert titles(store, OWNER) == []
    assert '::user-step' not in (out['text'] or '')


def test_elsewhere_mapping_is_not_framed_onto_a_local_tile(tmp_path):
    store, ledger = env(tmp_path)
    local = run(store, ledger, REAL_ASK, [{'text': 'Approve deploying build 412', 'kind': 'approve',
                                           'verbatim_span': REAL_ASK, 'options': []}])
    title = 'Decide whether to register a spare test runner'
    store.create(OTHER, _approval(OTHER, title))
    sentence = title + '.'
    reply = 'Status only. ' + sentence
    out = run(store, ledger, reply,
              [{'text': title, 'kind': 'input', 'verbatim_span': sentence, 'options': []}],
              elsewhere=[{'title': title, 'verbatim_span': sentence}],
              repeats=[{'id': local['steps'][0]['id'], 'verbatim_span': sentence}], user_msg=NOTICE)
    assert out['steps'] == []
    assert '::user-step' not in (out['text'] or '')


def test_unrelated_elsewhere_title_does_not_suppress_a_real_ask(tmp_path):
    store, ledger = env(tmp_path)
    title = 'Decide whether to register a spare test runner'
    store.create(OTHER, _approval(OTHER, title))
    step = {'text': 'Approve deploying build 412', 'kind': 'approve',
            'verbatim_span': REAL_ASK, 'options': []}
    out = run(store, ledger, REAL_ASK, [step],
              elsewhere=[{'title': title, 'verbatim_span': REAL_ASK}])
    assert [s['text'] for s in out['steps']] == ['Approve deploying build 412']


def test_publication_buffer_receives_elsewhere_titles_and_does_not_hold_a_restatement(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    plugin = load_module('elsewhere_digest_plugin', ROOT / '__init__.py')
    monkeypatch.setattr(plugin.core, 'home', lambda: tmp_path)
    monkeypatch.setattr(plugin.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    title = 'Decide whether to register a spare test runner'
    plugin.core.ActionStore().create(OTHER, _approval(OTHER, title))
    sentence = title + '.'
    reply = 'Status only. ' + sentence
    seen = []

    def llm(system, user):
        seen.append(user)
        return json.dumps({'steps': [{'text': title, 'kind': 'approve', 'verbatim_span': sentence, 'options': []}],
                           'elsewhere': [{'title': title, 'verbatim_span': sentence}], 'resolved': []})

    monkeypatch.setattr(plugin, 'aux_llm', llm)
    plugin._USER_MSG['session-a'] = NOTICE  # relay provenance: a runtime notice started this turn
    assert plugin.publication_buffer(session_id='session-a', platform='desktop', final_response=reply) is None
    # The same reply in a turn the user started: the other chat's title is no source association, so this
    # chat's approval sentence reaches the plan gate and the reply is held for a complete plan.
    plugin._USER_MSG['session-a'] = 'Should we register a spare runner?'
    held = plugin.publication_buffer(session_id='session-a', platform='desktop', final_response=reply)
    assert held and held.get('hold'), held
    plugin._USER_MSG.pop('session-a', None)
    assert seen and 'ELSEWHERE' in seen[0] and title in seen[0]


def test_exact_elsewhere_title_suppressed_without_model_mapping_beyond_prompt_cap(tmp_path):
    store, ledger = env(tmp_path)
    title = 'Approve deploying build 412 to the test environment'
    original = store.create(OTHER, _approval(OTHER, title))
    for i in range(35):
        store.create(OTHER, _approval(OTHER, f'Approve unrelated operation number {i}'))
    assert title not in core.elsewhere_titles(store, OWNER)
    before = store.get(OTHER, original['requestId'])
    sentence = 'Can you approve the deployment described in the other chat?'
    seen = []
    out = run(store, ledger, sentence,
              [{'text': title, 'kind': 'approve', 'verbatim_span': sentence, 'options': []}], seen=seen,
              user_msg=NOTICE)
    assert seen and title not in seen[0]
    assert out['steps'] == []
    assert titles(store, OWNER) == []
    assert ledger.read('session-a')['steps'] == []
    assert '::user-step' not in (out['text'] or '')
    assert store.get(OTHER, original['requestId']) == before
    with store._read() as c:
        assert c.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == 0


def test_elsewhere_title_created_during_extraction_is_rechecked(tmp_path):
    store, ledger = env(tmp_path)
    title = 'Approve deploying build 412 to the test environment'
    sentence = title + '?'
    def llm(system, user):
        store.create(OTHER, _approval(OTHER, title))
        return json.dumps({'steps': [{'text': title, 'kind': 'approve',
                                     'verbatim_span': sentence, 'options': []}], 'resolved': []})
    out = core.process_reply(sentence, 'session-a', ledger=ledger, action_store=store,
                             action_owner=OWNER, llm=llm, user_msg=NOTICE)
    assert out['steps'] == []
    assert titles(store, OWNER) == []
    assert ledger.read('session-a')['steps'] == []


def test_exact_elsewhere_evidence_suppressed_without_adopting_owner(tmp_path):
    store, ledger = env(tmp_path)
    title = 'Can you approve deploying build 412 to the test environment?'
    original = store.create(OTHER, _approval(OTHER, title))
    before = store.get(OTHER, original['requestId'])
    out = run(store, ledger, title, [{'text': 'Deployment permission for the test environment',
                                     'kind': 'approve', 'verbatim_span': title, 'options': []}], user_msg=NOTICE)
    assert out['steps'] == []
    assert ledger.read('session-a')['steps'] == []
    assert titles(store, OWNER) == []
    assert store.get(OTHER, original['requestId']) == before


@pytest.mark.parametrize('typed', ['', 'Can you also get the deploy approved here?'])
def test_equal_elsewhere_title_in_a_user_turn_is_its_own_request(tmp_path, typed):
    # No relay provenance: the user started this turn (or the hook missed its message). An equal title in
    # another chat is not a source association, so this chat's ask is published as its own request, and the
    # other chat's request is untouched (no adoption, no decision, no operation).
    store, ledger = env(tmp_path)
    title = 'Approve deploying build 412 to the test environment'
    original = store.create(OTHER, _approval(OTHER, title))
    before = store.get(OTHER, original['requestId'])
    sentence = title + '?'
    seen = []
    # Even when the model pairs the sentence with the other chat's title (ELSEWHERE mapping), that pairing is
    # title overlap, not provenance: in a user-started turn it suppresses nothing, and the prompt lists no
    # other chats' asks at all.
    out = run(store, ledger, sentence,
              [{'text': title, 'kind': 'approve', 'verbatim_span': sentence, 'options': []}], user_msg=typed,
              elsewhere=[{'title': title, 'verbatim_span': sentence}], seen=seen)
    assert seen and 'other chats; a sentence restating one is never a new step):\n(none)' in seen[0]
    assert len(out['steps']) == 1 and out['steps'][0]['requestId'] != original['requestId']
    assert titles(store, OWNER) == [title]
    assert store.get(OTHER, original['requestId']) == before
    assert all(not r.get('resolution') for r in store.requests(OWNER))


def test_identical_title_in_other_namespace_does_not_suppress(tmp_path):
    store, ledger = env(tmp_path)
    title = 'Approve deploying build 412 to the test environment'
    for owner in (dict(OTHER, gatewayId='different-gateway'), dict(OTHER, profile='different-profile')):
        store.create(owner, _approval(owner, title))
    sentence = title + '?'
    out = run(store, ledger, sentence,
              [{'text': title, 'kind': 'approve', 'verbatim_span': sentence, 'options': []}], user_msg=NOTICE)
    assert len(out['steps']) == 1
    assert titles(store, OWNER) == [title]


def test_retired_elsewhere_title_does_not_suppress(tmp_path):
    store, ledger = env(tmp_path)
    title = 'Approve deploying build 412 to the test environment'
    original = store.create(OTHER, _approval(OTHER, title))
    store.withdraw(OTHER, original['requestId'], by='agent', reason='No longer needed')
    sentence = title + '?'
    out = run(store, ledger, sentence,
              [{'text': title, 'kind': 'approve', 'verbatim_span': sentence, 'options': []}], user_msg=NOTICE)
    assert len(out['steps']) == 1
    assert titles(store, OWNER) == [title]
