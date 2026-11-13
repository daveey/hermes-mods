"""One decision maps to one open request (For-you tile).

Each test replays a duplicate seen in a live store (sanitized): the extractor re-minted
an open ask whenever the aux model reworded it, the publication gate demanded a new
request for an ask an open one already covered, and the action_request tool published
re-asks and "No decision needed" revisions as separate tiles. Re-asks now reuse or
revise the open request; a no-op ask is withdrawn. When two asks are not provably the
same decision, both stay. All persistence is under pytest's tmp_path.
"""
import importlib.util
import json
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = load_module('one_ask_core', ROOT / 'core.py')
OWNER = {'gatewayId': 'test-gateway', 'profile': 'default', 'storedSessionId': 'session-a'}


def env(tmp_path):
    return core.ActionStore(tmp_path / 'actions.sqlite3'), core.Ledger(tmp_path / 'ledger', tmp_path / 'missing.db')


def reply(store, ledger, text, steps, repeats=None, session='session-a', seen=None, turn=(), user_msg=''):
    payload = {'steps': steps, 'resolved': []}
    if repeats is not None:
        payload['repeats'] = repeats

    def llm(system, user):
        if seen is not None:
            seen.append((system, user))
        return json.dumps(payload)
    return core.process_reply(text, session, ledger=ledger, action_store=store, user_msg=user_msg,
                              action_owner=dict(OWNER, storedSessionId=session), llm=llm, turn_requests=turn)


def live(store, session='session-a'):
    return [r for r in store.requests(dict(OWNER, storedSessionId=session)) if r['state'] in ('open', 'needs_context')]


def approval(title, markdown='Change example.py; run its tests.', aliases=None, labels=('Approve', 'Revise', 'Decline')):
    return dict(schemaVersion=2, owner=OWNER.copy(), requestId=str(uuid.uuid4()), revision=1, kind='approval',
                title=title, scope=dict(markdown=markdown, complete=True, sourceRefs=[]), limits=[], exclusions=[],
                outcomes=dict(approve='Do exactly this', revise='Keep it blocked', decline='Cancel'),
                choices=[dict(id=e, label=l, effect=e) for e, l in zip(('approve', 'revise', 'decline'), labels)],
                aliases=aliases or [], state='open')


# ---- extractor path (transform_llm_output -> process_reply) ----------------------------------------------

CARDS_1 = ("**Two decision cards need you, no rush:**\n"
           "- **t_aaaa1111:** register one spare test runner for the repair feature.\n"
           "- **t_bbbb2222:** approve a dedicated build machine.")
CARDS_2 = ("The only things that need you are still the two decision cards:\n"
           "- **t_aaaa1111:** register one spare test runner for the repair feature, still pending.\n"
           "- **t_bbbb2222:** approve a dedicated build machine, still pending.")
CARDS_3 = ("**Still waiting on you.**\n"
           "- **t_aaaa1111:** register one spare test runner. Not urgent.\n"
           "- **t_bbbb2222:** approve a dedicated build machine. Not urgent.")


def card_steps(lines, titles):
    return [{'text': t, 'kind': 'approve', 'verbatim_span': s, 'options': []} for t, s in zip(titles, lines)]


def test_extractor_same_title_on_a_later_reply_is_its_own_ask_unless_the_model_names_the_tile(tmp_path):
    # Hub chat ..37ae77: round 2 re-extracted both card asks with the SAME text and a new span. An equal title
    # is not identity (two decisions can share one), so without provenance each is its own request. The
    # extractor's explicit channel ("repeats" naming the open tile's id for that sentence) maps it.
    store, ledger = env(tmp_path)
    titles = ['Decide on t_aaaa1111: register one spare test runner',
              'Decide on t_bbbb2222: approve a dedicated build machine']
    first = reply(store, ledger, CARDS_1, card_steps(CARDS_1.split('\n')[1:], titles))
    lines = CARDS_2.split('\n')[1:]
    named = reply(store, ledger, CARDS_2, card_steps(lines, titles),
                  repeats=[{'id': s['id'], 'verbatim_span': line} for s, line in zip(first['steps'], lines)])
    assert len(live(store)) == 2
    assert [s['id'] for s in named['steps']] == [s['id'] for s in first['steps']]  # framed onto the same tiles
    unnamed = reply(store, ledger, CARDS_3, card_steps(CARDS_3.split('\n')[1:], titles))
    assert len(live(store)) == 4 and not {s['id'] for s in unnamed['steps']} & {s['id'] for s in first['steps']}
    assert all(not r.get('resolution') for r in store.requests(OWNER))


def test_extractor_reworded_ask_naming_the_same_card_is_a_separate_ask(tmp_path):
    # Round 3 of the same chat reworded the title ("Decide whether to register ... (t_aaaa1111)"). A card both
    # name is context, not identity: the old word-overlap rule folded it. Without provenance both stay.
    store, ledger = env(tmp_path)
    reply(store, ledger, CARDS_1, card_steps(CARDS_1.split('\n')[1:], [
        'Decide on t_aaaa1111: register one spare test runner',
        'Decide on t_bbbb2222: approve a dedicated build machine']))
    before = {r['requestId']: r for r in live(store)}
    reply(store, ledger, CARDS_3, card_steps(CARDS_3.split('\n')[1:], [
        'Decide whether to register one spare test runner (t_aaaa1111)',
        'Approve a dedicated build machine (t_bbbb2222)']))
    assert len(live(store)) == 4
    assert all(store.get(OWNER, rid) == r for rid, r in before.items())  # earlier requests untouched


def test_extractor_same_evidence_sentence_reworded_reuses_the_open_tile(tmp_path):
    # MacBook chat ..6212f7: the same "**Ask:** ..." sentence came back 75 min later as kind choose, new text.
    store, ledger = env(tmp_path)
    ask = '**Ask:** which model ID should the study use, or which model should it use instead?'
    reply(store, ledger, ask, [{'text': 'Pick the model ID for the study, or name another model', 'kind': 'input',
                                'verbatim_span': ask, 'options': []}])
    reply(store, ledger, 'Still blocked on one thing.\n\n' + ask, [{
        'text': 'Pick the model ID for the study (or name a different model)', 'kind': 'choose',
        'verbatim_span': ask, 'options': ['Provide the model ID', 'Name a different model']}])
    assert len(live(store)) == 1


def test_extractor_restating_this_turns_published_title_does_not_mint_a_second_tile(tmp_path):
    # Hub chat ..67cb2d: the reply bolded the producer request's own title; the model paraphrased it. The
    # producer published it in this turn (server-recorded), so the reply relays it.
    store, ledger = env(tmp_path)
    title = 'Approve the notify fix (accept not-found exit 1 + regression test)?'
    r = store.create(OWNER, approval(title))
    step = [{'text': 'Approve the notify fix (exit-1 not-found handling, regression tests)', 'kind': 'approve',
             'verbatim_span': f'**{title}**', 'options': []}]
    out = reply(store, ledger, f'**{title}**\n\nThe plan is in the request.', step, turn={r['requestId']})
    assert len(live(store)) == 1 and out['steps'] == []


def test_extractor_same_title_as_an_earlier_turns_request_is_its_own_ask(tmp_path):
    # Same text, but the request was published in an earlier turn and the model named no repeat: a title is not
    # provenance, so this is a distinct request and the original is untouched.
    store, ledger = env(tmp_path)
    title = 'Approve the notify fix (accept not-found exit 1 + regression test)?'
    r = store.create(OWNER, approval(title))
    out = reply(store, ledger, f'**{title}**\n\nThe plan is in the request.', [{
        'text': 'Approve the notify fix (exit-1 not-found handling, regression tests)', 'kind': 'approve',
        'verbatim_span': f'**{title}**', 'options': []}])
    assert len(live(store)) == 2 and len(out['steps']) == 1 and out['steps'][0]['requestId'] != r['requestId']
    assert store.get(OWNER, r['requestId']) == r


def test_extractor_choice_menu_of_this_turns_approval_is_not_a_second_ask(tmp_path):
    # Same chat: "**Your choices:** Approve fix / Change plan / Decline" became its own choice tile.
    store, ledger = env(tmp_path)
    r = store.create(OWNER, approval('Approve the notify fix?', labels=('Approve fix', 'Change plan', 'Decline')))
    text = ('**Your choices:**\n- **Approve fix:** an agent does steps 1-6.\n- **Change plan:** tell me what to '
            'change.\n- **Decline:** nothing changes.')
    menu = [{'text': 'Choose: approve fix, change plan, or decline the notify repair',
             'kind': 'choose', 'verbatim_span': '**Your choices:**',
             'options': ['Approve fix', 'Change plan', 'Decline']}]
    reply(store, ledger, text, menu, turn={r['requestId']})
    assert len(live(store)) == 1
    # Same labels, but the approval came from an earlier turn: the menu is not proven to be its menu.
    reply(store, ledger, text, menu)
    assert len(live(store)) == 2


def test_extractor_two_readings_of_one_reminder_sentence_mint_nothing(tmp_path):
    # Same chat, 21:39: one sentence produced an approve AND a choose step next to the open approval.
    store, ledger = env(tmp_path)
    r = store.create(OWNER, approval('Approve the notify fix (accept not-found exit 1 + regression tests)?'))
    span = 'The fix to `notify.py` is still waiting for your approval.'
    steps = [
        {'text': 'Approve the notify fix (accept not-found exit 1 + regression tests)', 'kind': 'approve',
         'verbatim_span': span, 'options': []},
        {'text': 'Choose whether to approve the notify repair, change the plan, or decline it', 'kind': 'choose',
         'verbatim_span': span, 'options': ['Approve', 'Change the plan', 'Decline']}]
    reply(store, ledger, "I've updated the plan. " + span, steps, turn={r['requestId']})
    assert len(live(store)) == 1


def test_two_readings_of_one_new_sentence_mint_one_tile(tmp_path):
    # No open request to relay: the approve reading mints one tile and the menu reading of the SAME sentence in
    # the same reply lands on it (exact sentence provenance), not on a second tile.
    store, ledger = env(tmp_path)
    span = 'The fix to `notify.py` needs your approval before I run it.'
    out = reply(store, ledger, "I've drafted the plan. " + span, [
        {'text': 'Approve the notify fix', 'kind': 'approve', 'verbatim_span': span, 'options': []},
        {'text': 'Choose whether to approve the notify repair, change the plan, or decline it', 'kind': 'choose',
         'verbatim_span': span, 'options': ['Approve', 'Change the plan', 'Decline']}])
    assert len(live(store)) == 1 and len({s['id'] for s in out['steps']}) == 1


def test_extractor_model_marked_repeat_is_not_minted_and_prompt_lists_open_requests(tmp_path):
    # MacBook chat ..f0271d: "they'll stay in your list until you answer them" re-asked two open prompts.
    store, ledger = env(tmp_path)
    r = store.create(OWNER, approval('Add a backup model to shared settings?'))
    span = "They'll stay in your list until you answer them."
    seen = []
    reply(store, ledger, span, [{'text': 'Answer whether to add a backup model to shared settings', 'kind': 'choose',
                                 'verbatim_span': span, 'options': ['No action needed', 'Add a backup model']}],
          repeats=[{'id': r['requestId'], 'verbatim_span': span}], seen=seen)
    assert len(live(store)) == 1
    system, user = seen[0]
    assert r['requestId'] in user and 'Add a backup model to shared settings?' in user  # the model sees it
    assert '"repeats"' in system


def test_distinct_asks_still_get_their_own_tiles(tmp_path):
    # Constraint: when two asks are not provably the same decision, keep both.
    store, ledger = env(tmp_path)
    reply(store, ledger, CARDS_1, card_steps(CARDS_1.split('\n')[1:2], ['Decide on t_aaaa1111: register a runner']))
    text = 'Separately: which runner label should t_aaaa1111 use, `spare` or `canary`?'
    reply(store, ledger, text, [{'text': 'Pick the runner label for t_aaaa1111', 'kind': 'input',
                                 'verbatim_span': text, 'options': []}])  # same card, different kind of ask
    reply(store, ledger, 'Should I also rotate the deploy key?', [{
        'text': 'Approve rotating the deploy key', 'kind': 'approve',
        'verbatim_span': 'Should I also rotate the deploy key?', 'options': []}])
    assert len(live(store)) == 3


def test_choice_menu_with_two_open_approvals_is_kept(tmp_path):
    store, ledger = env(tmp_path)
    store.create(OWNER, approval('Approve fix A?'))
    store.create(OWNER, approval('Approve fix B?'))
    text = '**Your choices:** approve, change the plan, or decline.'
    reply(store, ledger, text, [{'text': 'Choose: approve, change the plan, or decline', 'kind': 'choose',
                                 'verbatim_span': text, 'options': ['Approve', 'Change the plan', 'Decline']}])
    assert len(live(store)) == 3  # unsure which approval it belongs to: not merged


def test_a_decided_or_withdrawn_ask_is_never_the_tile_a_reask_lands_on(tmp_path):
    store, ledger = env(tmp_path)
    decided = store.create(OWNER, approval('Approve fix A?'))
    store.decide(OWNER, dict(requestId=decided['requestId'], expectedRevision=1, choiceId='approve',
                             idempotencyKey='k', revisionText=None))
    gone = store.create(OWNER, approval('Approve fix B?'))
    core.withdraw_request(store, ledger, OWNER, gone['requestId'], by='system', reason='card done')
    for x in 'BA':
        ask = f'Should I go ahead with fix {x}?'
        reply(store, ledger, ask, [{'text': f'Approve fix {x}', 'kind': 'approve', 'verbatim_span': ask, 'options': []}])
    titles = [r['title'] for r in live(store) if not r.get('resolution')]
    assert sorted(titles) == ['Approve fix A', 'Approve fix B']  # fresh asks, nothing merged into closed ones


# ---- publication gate (pre_final_gate) --------------------------------------------------------------------

@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    monkeypatch.delenv('HERMES_USER_STEPS_OFF', raising=False)
    mod = load_module('one_ask_plugin', ROOT / '__init__.py')
    monkeypatch.setattr(mod.core, 'home', lambda: tmp_path)
    monkeypatch.setattr(mod.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    return mod


def publish(plugin, args, session='session-a'):
    return json.loads(plugin.publish_action_request(args, session_id=session))


def payload_of(r):
    return {k: v for k, v in r.items() if k not in ('owner', 'requestId', 'revision', 'state', 'resolution')} | {'state': 'open'}


def test_gate_does_not_hold_a_reply_whose_approval_ask_is_already_published(plugin, monkeypatch):
    title = 'Approve the notify fix (accept not-found exit 1 + regression tests)?'
    publish(plugin, {'request': payload_of(approval(title))})
    final = f'**{title}**\n\nPick in For you.'
    monkeypatch.setattr(plugin, 'aux_llm', lambda *a: json.dumps({'steps': [{
        'text': 'Approve the notify fix (exit-1 handling, regression tests)', 'kind': 'approve',
        'verbatim_span': f'**{title}**', 'options': []}], 'resolved': []}))
    assert plugin.publication_buffer(session_id='session-a', platform='cli', final_response=final) is None


def test_gate_still_holds_an_approval_ask_with_no_published_plan(plugin, monkeypatch):
    monkeypatch.setattr(plugin, 'aux_llm', lambda *a: json.dumps({'steps': [{
        'text': 'Approve deploying build 9', 'kind': 'approve', 'verbatim_span': 'Want me to deploy build 9?',
        'options': []}], 'resolved': []}))
    out = plugin.publication_buffer(session_id='session-a', platform='cli', final_response='Want me to deploy build 9?')
    assert out and out.get('hold') is True and 'requestId' in out['nudge']


# ---- tool path (action_request) ---------------------------------------------------------------------------

def test_tool_exact_retry_returns_the_open_request_unchanged(plugin, tmp_path):
    # A lost response / retried tool call re-sends the identical request: nothing new, no new revision.
    first = publish(plugin, {'request': payload_of(approval('Approve the notify fix?'))})['request']
    again = publish(plugin, {'request': payload_of(approval('Approve the notify fix?'))})
    store = plugin.core.ActionStore()
    assert [r['requestId'] for r in live(store)] == [first['requestId']]
    assert again['request']['requestId'] == first['requestId'] and again['request']['revision'] == 1
    assert 'Nothing new' in again['note'] and first['requestId'] in again['note']
    assert not store.get(OWNER, first['requestId']).get('resolution')  # a retry is never a decision


def test_tool_identical_body_with_new_provenance_is_a_separate_request(plugin):
    # Same body but a source the open request does not carry: not a retry of it, so it is not absorbed and the
    # new provenance is not dropped.
    first = publish(plugin, {'request': payload_of(approval('Approve the notify fix?'))})['request']
    body = payload_of(approval('Approve the notify fix?'))
    ref = {'gatewayId': OWNER['gatewayId'], 'profile': OWNER['profile'], 'type': 'message', 'id': 'msg-77'}
    body['aliases'] = [ref]
    again = publish(plugin, {'request': body})
    assert again['request']['requestId'] != first['requestId'], again


def test_tool_same_title_with_a_different_complete_plan_keeps_both_and_says_so(plugin):
    # Reviewer L5: "Approve the plan." for plan B must not overwrite plan A in place.
    first = publish(plugin, {'request': payload_of(approval('Approve the migration plan.', markdown='Migrate DB A.'))})
    second = publish(plugin, {'request': payload_of(approval('Approve the migration plan.', markdown='Delete bucket B.'))})
    assert second['request']['requestId'] != first['request']['requestId']
    assert first['request']['requestId'] in second['note'] and 'withdraw' in second['note']
    assert len(live(plugin.core.ActionStore())) == 2


def test_tool_short_generic_titles_are_never_folded(plugin):
    publish(plugin, {'request': payload_of(approval('OK?'))})
    publish(plugin, {'request': payload_of(approval('OK.'))})
    assert len(live(plugin.core.ActionStore())) == 2


def extracted(plugin, store, title, session='session-a'):
    step = {'text': title, 'kind': 'approve', 'verbatim_span': title, 'options': [], 'session': session,
            'id': plugin.core.new_step_id(), 'status': 'open', 'created': 1, 'paragraph': title}
    owner = dict(OWNER, storedSessionId=session)
    return plugin.core.ActionStore.create(store, owner, plugin.core.legacy_request(owner, step, dict(
        gatewayId=OWNER['gatewayId'], profile='default', type='user_step', id=step['id'])))


def test_tool_same_title_as_extracted_planless_requests_is_a_separate_request(plugin, tmp_path):
    # Was: the publish folded into an extracted/plan-less request by normalized title and withdrew the others
    # (__init__ fold). An equal title proves nothing: both extracted requests stay open and untouched.
    store = plugin.core.ActionStore()
    title = 'Approve the notify fix?'
    older, newer = extracted(plugin, store, title), extracted(plugin, store, title)
    out = publish(plugin, {'request': payload_of(approval(title, markdown='The full exact plan.'))})
    assert out['request']['requestId'] not in (older['requestId'], newer['requestId'])
    assert {older['requestId'], newer['requestId']} <= set(out['note'].split(': ', 1)[1].replace('.', ' ').replace(',', ' ').split())
    assert store.get(OWNER, older['requestId']) == older and store.get(OWNER, newer['requestId']) == newer
    assert len(live(store)) == 3 and all(not r.get('resolution') for r in store.requests(OWNER))


def test_tool_citing_the_extracted_step_revises_that_request_only(plugin, tmp_path):
    # Provenance: scope.sourceRefs names the exact qualified user_step of ONE extracted request. That request
    # gets the plan (aliases kept); the other same-title request is left alone, never withdrawn.
    store = plugin.core.ActionStore()
    title = 'Approve the notify fix?'
    older, newer = extracted(plugin, store, title), extracted(plugin, store, title)
    ref = next(a for a in older['aliases'] if a['type'] == 'user_step')
    body = payload_of(approval(title, markdown='The full exact plan.'))
    body['scope'] = dict(body['scope'], sourceRefs=[ref])
    out = publish(plugin, {'request': body})
    assert out['request']['requestId'] == older['requestId'] and out['request']['scope']['complete']
    assert ref in out['request']['aliases'] and newer['requestId'] in out['note']
    assert store.get(OWNER, newer['requestId']) == newer
    assert not store.get(OWNER, older['requestId']).get('resolution')


@pytest.mark.parametrize('field,value', [('gatewayId', 'other-gateway'), ('profile', 'other-profile'),
                                         ('id', 'us_000000000000')])
def test_tool_a_ref_from_another_gateway_profile_or_step_folds_nothing(plugin, field, value):
    store = plugin.core.ActionStore()
    title = 'Approve the notify fix?'
    target = extracted(plugin, store, title)
    ref = dict(next(a for a in target['aliases'] if a['type'] == 'user_step'), **{field: value})
    body = payload_of(approval(title, markdown='The full exact plan.'))
    body['scope'] = dict(body['scope'], sourceRefs=[ref])
    out = publish(plugin, {'request': body})
    assert out['request']['requestId'] != target['requestId'], out
    assert store.get(OWNER, target['requestId']) == target


def _cited_body(target, title='Approve the notify fix?'):
    ref = next(a for a in target['aliases'] if a['type'] == 'user_step')
    body = payload_of(approval(title, markdown='The full exact plan.'))
    body['scope'] = dict(body['scope'], sourceRefs=[ref])
    return body, ref


def test_tool_citing_a_target_revised_meanwhile_is_an_error_not_a_second_tile(plugin, monkeypatch):
    store = plugin.core.ActionStore()
    target = extracted(plugin, store, 'Approve the notify fix?')
    body, _ = _cited_body(target)

    def stale(*a, **k):
        raise plugin.core.ActionError('Stale revision', 409)
    monkeypatch.setattr(plugin.core.ActionStore, 'revise', stale)
    out = publish(plugin, {'request': body})
    assert out.get('status') == 409 and target['requestId'] in out['error'] and 'expectedRevision' in out['error']
    assert [r['requestId'] for r in live(store)] == [target['requestId']]


def test_tool_citing_a_target_decided_meanwhile_publishes_separately_without_its_refs(plugin, monkeypatch):
    store = plugin.core.ActionStore()
    target = extracted(plugin, store, 'Approve the notify fix?')
    body, ref = _cited_body(target)

    def decided(*a, **k):
        raise plugin.core.ActionError('A decision is already recorded for this revision', 409)
    monkeypatch.setattr(plugin.core.ActionStore, 'revise', decided)
    out = publish(plugin, {'request': body})['request']
    assert out['requestId'] != target['requestId']
    assert ref not in out['aliases'] and ref not in out['scope']['sourceRefs']
    assert not out.get('resolution')


def test_tool_fold_conflict_on_another_requests_alias_is_reported_as_is(plugin, monkeypatch):
    store = plugin.core.ActionStore()
    target = extracted(plugin, store, 'Approve the notify fix?')
    body, _ = _cited_body(target)

    def taken(*a, **k):
        raise plugin.core.ActionError('Alias already belongs to another request', 409)
    monkeypatch.setattr(plugin.core.ActionStore, 'revise', taken)
    out = publish(plugin, {'request': body})
    assert out == {'error': 'Alias already belongs to another request', 'status': 409}
    assert [r['requestId'] for r in live(store)] == [target['requestId']]


def test_tool_citing_steps_of_two_open_requests_is_refused(plugin):
    store = plugin.core.ActionStore()
    a, b = extracted(plugin, store, 'Approve the notify fix?'), extracted(plugin, store, 'Approve the notify fix?')
    body, ref_a = _cited_body(a)
    body['scope']['sourceRefs'].append(next(x for x in b['aliases'] if x['type'] == 'user_step'))
    out = publish(plugin, {'request': body})
    assert 'more than one open request' in out['error']
    assert store.get(OWNER, a['requestId']) == a and store.get(OWNER, b['requestId']) == b


def test_tool_extracted_tile_in_another_session_is_never_a_fold_target(plugin):
    store = plugin.core.ActionStore()
    title = 'Approve the notify fix?'
    other = extracted(plugin, store, title, session='session-b')
    ref = next(a for a in other['aliases'] if a['type'] == 'user_step')
    body = payload_of(approval(title, markdown='The full exact plan.'))
    body['scope'] = dict(body['scope'], sourceRefs=[ref])
    out = publish(plugin, {'request': body})
    assert out.get('request', {}).get('requestId') != other['requestId'], out
    assert store.get(dict(OWNER, storedSessionId='session-b'), other['requestId']) == other


def test_published_then_relayed_through_the_real_hooks_is_one_tile(plugin, monkeypatch):
    # Real producer + transform path: the tool publishes in this turn, the final reply restates it, and the
    # turn's recorded provenance keeps it to one request. The next turn's same-title ask is a separate request.
    title = 'Approve deploying build 412 to the test environment'
    monkeypatch.setattr(plugin, '_eligible', lambda *a, **k: True)
    monkeypatch.setattr(plugin, '_framed', lambda *a: True)
    monkeypatch.setattr(plugin.core, '_worth_asking', lambda *a: True)
    plugin.pre_llm_call(session_id='session-a', user_message='deploy it', platform='cli')
    first = publish(plugin, {'request': payload_of(approval(title))})['request']
    step = {'text': title, 'kind': 'approve', 'verbatim_span': f'**{title}?**', 'options': []}
    monkeypatch.setattr(plugin, 'aux_llm', lambda *a: json.dumps({'steps': [step], 'resolved': []}))
    plugin.transform_llm_output(response_text=f'**{title}?**\n\nPick in For you.', session_id='session-a',
                                platform='cli')
    store = plugin.core.ActionStore()
    assert [r['requestId'] for r in live(store)] == [first['requestId']]
    plugin.pre_llm_call(session_id='session-a', user_message='and the other env?', platform='cli')
    plugin.transform_llm_output(response_text=f'**{title}?**\n\nThis one is for staging.', session_id='session-a',
                                platform='cli')
    assert len(live(store)) == 2 and all(not r.get('resolution') for r in store.requests(OWNER))


def test_tool_reask_never_lands_on_a_decided_or_card_bound_request(plugin):
    store = plugin.core.ActionStore()
    first = publish(plugin, {'request': payload_of(approval('Approve the notify fix?'))})['request']
    store.decide(OWNER, dict(requestId=first['requestId'], expectedRevision=1, choiceId='approve',
                             idempotencyKey='k', revisionText=None))
    again = publish(plugin, {'request': payload_of(approval('Approve the notify fix?'))})['request']
    assert again['requestId'] != first['requestId']
    assert store.get(OWNER, first['requestId'])['resolution']['choiceId'] == 'approve'  # the decision wins


def test_tool_revision_that_says_no_decision_is_needed_withdraws_instead(plugin):
    # MacBook chat ..f0271d: four asks revised to "No decision needed: ..." stayed in For you.
    r = publish(plugin, {'request': payload_of(approval('Add a backup model?'))})['request']
    noop = payload_of(approval('No decision needed: shared settings already have a backup model',
                               markdown='Settled, nothing to decide.'))
    out = publish(plugin, {'request': noop, 'requestId': r['requestId'], 'expectedRevision': 1})
    assert out['request']['state'] == 'withdrawn' and out['request']['revision'] == 1
    assert 'withdrawn' in out['note']
    assert live(plugin.core.ActionStore()) == []


def test_tool_new_request_that_asks_nothing_is_refused(plugin):
    out = publish(plugin, {'request': payload_of(approval('Nothing to decide: the backup model is set'))})
    assert 'error' in out and 'withdraw' in out['error']
    assert plugin.core.ActionStore().requests(OWNER) == []


# ---- legacy re-render (pre_llm_call migrate_step / backfill) ---------------------------------------------

def test_rerender_of_an_extracted_step_keeps_its_request_id(tmp_path):
    # Guard, not a regression: the re-render path revised in place in every live row (clarification_upgrades).
    store, ledger = env(tmp_path)
    ask = '**Ask:** which model should the study use, or another one?'
    out = reply(store, ledger, ask, [{'text': 'Pick the study model', 'kind': 'input', 'verbatim_span': ask,
                                      'options': []}])
    step = ledger.read('session-a')['steps'][0]
    again = store.migrate_step(OWNER, step)
    assert again['requestId'] == out['steps'][0]['requestId'] and len(store.requests(OWNER)) == 1


# ---- adversarial review cases: distinct asks that must stay separate ----------------------------------------

def test_two_different_asks_about_one_card_stay_separate(tmp_path):
    store, ledger = env(tmp_path)  # reviewer L1
    a = 'Should I register a spare runner for t_aaaa1111 now?'
    b = 'Should I delete the stale branch of t_aaaa1111 as well?'
    reply(store, ledger, a, [{'text': 'Approve registering a spare runner for t_aaaa1111', 'kind': 'approve',
                              'verbatim_span': a, 'options': []}])
    reply(store, ledger, b, [{'text': 'Approve deleting the stale branch of t_aaaa1111', 'kind': 'approve',
                              'verbatim_span': b, 'options': []}])
    assert len(live(store)) == 2


def test_a_card_named_only_in_the_evidence_does_not_anchor(tmp_path):
    store, ledger = env(tmp_path)  # reviewer L2
    a = 'Should I register a spare runner for t_aaaa1111 now?'
    b = 'Once t_aaaa1111 lands, should I also rotate the deploy key?'
    reply(store, ledger, a, [{'text': 'Approve registering a spare runner for t_aaaa1111', 'kind': 'approve',
                              'verbatim_span': a, 'options': []}])
    reply(store, ledger, b, [{'text': 'Approve rotating the deploy key', 'kind': 'approve',
                              'verbatim_span': b, 'options': []}])
    assert len(live(store)) == 2


def test_a_generic_closing_sentence_does_not_merge_two_questions(tmp_path):
    store, ledger = env(tmp_path)  # reviewer L3
    tail = 'Let me know which you prefer.'
    reply(store, ledger, 'Cache in Redis or SQLite? ' + tail, [{'text': 'Pick the cache store', 'kind': 'choose',
          'verbatim_span': tail, 'options': ['Redis', 'SQLite']}])
    reply(store, ledger, 'Logo in blue or green? ' + tail, [{'text': 'Pick the logo colour', 'kind': 'choose',
          'verbatim_span': tail, 'options': ['Blue', 'Green']}])
    assert len(live(store)) == 2


def test_a_menu_about_another_topic_is_not_the_open_approvals_menu(tmp_path):
    store, ledger = env(tmp_path)  # reviewer L4
    store.create(OWNER, approval('Approve deploying build 9?'))
    text = 'For the vendor contract: approve, request changes, or decline?'
    reply(store, ledger, text, [{'text': 'Approve, request changes, or decline the vendor contract', 'kind': 'choose',
                                 'verbatim_span': text, 'options': ['Approve', 'Request changes', 'Decline']}])
    assert len(live(store)) == 2


def test_scoped_no_action_wording_is_a_real_ask(plugin):
    # reviewer L7/L8: a scoped "no input needed for steps 1-3" or "from ops" still asks something.
    r = publish(plugin, {'request': payload_of(approval('Approve the rollout plan for the API'))})['request']
    rev = payload_of(approval('Approve the rollout plan for the API',
                              markdown='No input needed from you for steps 1-3. Step 4 needs your approval.'))
    out = publish(plugin, {'request': rev, 'requestId': r['requestId'], 'expectedRevision': 1})
    assert out['request']['state'] == 'open' and out['request']['revision'] == 2
    new = publish(plugin, {'request': payload_of(approval('No action required from ops: approve the budget increase?'))})
    assert new['request']['state'] == 'open'


def test_noop_revision_needs_expected_revision(plugin):
    r = publish(plugin, {'request': payload_of(approval('Add a backup model to settings?'))})['request']
    out = publish(plugin, {'request': payload_of(approval('No decision needed: already set')), 'requestId': r['requestId']})
    assert 'error' in out and live(plugin.core.ActionStore())


def test_model_repeat_naming_an_extracted_tile_by_its_step_id_is_honoured(tmp_path):
    store, ledger = env(tmp_path)  # reviewer D1
    first = reply(store, ledger, 'Which region should the cluster use?', [{
        'text': 'Pick the cluster region', 'kind': 'input', 'verbatim_span': 'Which region should the cluster use?',
        'options': []}])
    span = 'Still need the region for the cluster before I continue, please.'
    reply(store, ledger, span, [{'text': 'Choose the region for the cluster', 'kind': 'input', 'verbatim_span': span,
                                 'options': []}], repeats=[{'id': first['steps'][0]['id'], 'verbatim_span': span}])
    assert len(live(store)) == 1


def test_malformed_repeats_keep_the_model_result(tmp_path):
    store, ledger = env(tmp_path)  # reviewer C1
    out = reply(store, ledger, 'Should I deploy build 12 to staging?', [{
        'text': 'Approve deploying build 12', 'kind': 'approve', 'verbatim_span': 'Should I deploy build 12 to staging?',
        'options': []}], repeats=5)
    assert out['source'] == 'model' and len(out['steps']) == 1


def test_gate_holds_a_reask_of_an_open_plan_less_approval(plugin, monkeypatch):
    # reviewer G1: a listed open title must not drop the approval before the gate judges it.
    store, ledger = plugin.core.ActionStore(), plugin.core.Ledger()
    plugin.core.process_reply('Want me to deploy build 9 to prod?', 'session-a', ledger=ledger, action_store=store,
                              action_owner=OWNER, llm=lambda *a: json.dumps({'steps': [{
                                  'text': 'Approve deploying build 9', 'kind': 'approve',
                                  'verbatim_span': 'Want me to deploy build 9 to prod?', 'options': []}], 'resolved': []}))
    monkeypatch.setattr(plugin, 'aux_llm', lambda *a: json.dumps({'steps': [{
        'text': 'Approve deploying build 9', 'kind': 'approve', 'verbatim_span': 'Want me to deploy build 9 to prod?',
        'options': []}], 'resolved': []}))
    out = plugin.publication_buffer(session_id='session-a', platform='cli',
                                    final_response='Want me to deploy build 9 to prod?')
    assert out and out.get('hold') is True


def test_automatic_reask_revision_loses_to_a_recorded_decision(tmp_path):
    store, _ = env(tmp_path)  # reviewer S4
    r = store.create(OWNER, approval('Approve the notify fix?'))
    store.decide(OWNER, dict(requestId=r['requestId'], expectedRevision=1, choiceId='approve', idempotencyKey='k',
                             revisionText=None))
    with pytest.raises(core.ActionError):
        store.revise(OWNER, r['requestId'], 1, dict(r, title='Approve the notify fix?'), undecided=True)
    assert store.get(OWNER, r['requestId'])['resolution']['choiceId'] == 'approve'


def test_a_model_marked_repeat_is_framed_onto_the_existing_tile(tmp_path):
    # Live MacBook chat 20261027_232651_5eab92: the reworded re-ask came back only as a repeat of us_...
    store, ledger = env(tmp_path)
    first = reply(store, ledger, 'One question first: should the demo label use blue or green?', [{
        'text': 'Choose the demo label color: blue or green', 'kind': 'choose',
        'verbatim_span': 'should the demo label use blue or green?', 'options': ['blue', 'green']}])
    tile = first['steps'][0]
    span = 'Which colour do you want for the demo label, blue or green?'
    payload = {'steps': [], 'resolved': [], 'repeats': [{'id': tile['id'], 'verbatim_span': span}]}
    out = core.process_reply('I am still blocked on the scratch demo. ' + span, 'session-a', ledger=ledger,
                             action_store=store, action_owner=OWNER, llm=lambda *a: json.dumps(payload))
    assert [s['id'] for s in out['steps']] == [tile['id']] and tile['id'] in out['text']
    assert len(live(store)) == 1 and len(ledger.read('session-a')['steps']) == 1


# ---- one decision, one tile: identity is provenance only; a restatement is refused pre-send ------------------
# Live MacBook chat ..f07c32 (2026-11-13): request 2948b7f0 (step us_852f91e2d3) held both macOS CI picks. The 11:54
# reply then minted us_64aeb33b63 from a bare reminder sentence (same A/B/C menu, reworded) and us_60b82616bf from the
# restated second question (whose card ask 2948b7f0's scope already quoted): three tiles for two decisions. At 13:17
# a status headline ("The only thing waiting on you is the macOS CI pick.") minted 2288f5b4 the same way.
#
# Four review rounds of PR #93 each found false merges in title/menu/scope identity rules (one decision folded onto
# another, silently losing an ask). So nothing is ever merged by what a sentence says: core.same_open_ask keeps its
# provenance-only rules, every regression below stays two tiles at publication, and the pre-send gate
# (publication_buffer) holds a reply ONCE when an ask that maps to no open request by provenance would mint a tile
# beside an open one. The agent, which knows whether it meant a new decision, drops the restatement or re-sends.

BLACKSMITH_ASK = ('"A = move the hermes_state lane plus the Native memory and real-codesign jobs to Blacksmith 6vcpu '
                  'macOS (~$26/day, ~$770/mo); B = move only the sub-minute Native memory + real-codesign jobs '
                  '(~$4/day, ~$120/mo; lane stays on the mini); C = no spend, keep everything on the mini."')
TESTS_OS_ASK = ('"Pick how macOS platforms-marked tests should get CI: A) PR lane on the mini, B) scheduled '
                'non-required lane, C) keep manual."')
NEEDS_YOU_1108 = (
    "**Needs you: reply here with a letter for each**\n"
    "1. **Moving macOS CI off the overloaded Mac mini** has been waiting on your answer for over 3 hours. The ask is: "
    f"{BLACKSMITH_ASK} It's also in your For-you list.\n"
    "   - **If you pick A or B,** an agent moves those jobs.\n"
    "   - **If you pick C, or don't answer,** nothing changes.\n"
    "   - I'd pick B.\n"
    "2. **macOS CI for the tests marked as posix/macOS-only.** These tests never run on macOS in CI today. The ask is: "
    f"{TESTS_OS_ASK}\n"
    "   - **If you pick A or B,** an agent builds that lane.\n"
    "   - **If you pick C, or don't answer,** they stay manual.\n"
    "   - I'd pick B.")
STEP_852F = {'text': 'Pick macOS CI option A, B, or C (B recommended)', 'kind': 'choose',
             'verbatim_span': "**If you pick C, or don't answer,** nothing changes.",
             'options': ['B: sub-minute jobs only (~$120/mo)', 'A: lane plus jobs (~$770/mo)',
                         'C: no spend, keep on mini']}
STATUS_1154 = ("I've gone through all 95 card updates since my 10:36 message. Every fix they report merged exactly as "
               "reviewed. A fix for the state guard is running now. Two macOS CI choices still need you.")
STEP_64AE = {'text': 'Pick macOS CI option A, B, or C (B recommended)', 'kind': 'choose',
             'verbatim_span': 'Two macOS CI choices still need you.',
             'options': ['B: sub-minute jobs only (~$120/mo)', 'A: lane + jobs to Blacksmith (~$770/mo)',
                         'C: no spend, keep on the mini']}
NEEDS_YOU_1154 = (
    "**Needs you: one letter for each.** Both cards are still waiting on you, and both questions are under one item "
    "in your For-you list.\n"
    f"1. **Moving macOS CI off the overloaded Mac mini** (MacBook board). The card asks: {BLACKSMITH_ASK}\n"
    "   - **I'd pick B:** for $4/day it ends the 16-minute median wait the short jobs have.\n"
    "2. **macOS CI for tests marked posix/macOS-only** (worker-6 board). These tests never run on macOS in CI today. "
    f"The card asks: {TESTS_OS_ASK}\n"
    "   - I'd pick B.\n"
    "   - If you pick A or B, an agent builds that lane. If you pick C, or don't answer, they stay manual.")
STEP_60B8 = {'text': 'Pick how macOS-marked tests should get CI: A, B, or C (B recommended)', 'kind': 'choose',
             'verbatim_span': '**macOS CI for tests marked posix/macOS-only** (worker-6 board). These tests never run '
                              'on macOS in CI today.',
             'options': ['B: scheduled non-required lane', 'A: PR lane on the mini', 'C: keep manual']}
# 13:17 PST: request 2288f5b4 (step us_a9defb1d27) minted from this headline while 2948b7f0 was open (withdrawn 13:21).
HEADLINE_1317 = (
    "**All 38 updates check out.** Every merge in the batch landed exactly as approved, with required CI green, and no "
    "wrong behaviour turned up. Two bugs still have unmerged fixes: lifecycle-guard #2485 and the duplicate For-you "
    "tiles (hermes-mods #93). Tests for 5 untested-code gaps are still being written. The only thing waiting on you is "
    "the macOS CI pick. (Checked 13:15 PST: that card is still blocked and the request is your only open ask from "
    "this chat.)")
STEP_A9DE = {'text': 'Pick macOS CI option A, B, or C (B recommended)', 'kind': 'choose',
             'verbatim_span': 'The only thing waiting on you is the macOS CI pick.',
             'options': ['B (recommended)', 'A', 'C']}


def _open_macos_tile(store, ledger):
    first = reply(store, ledger, NEEDS_YOU_1108, [dict(STEP_852F)])
    assert len(live(store)) == 1
    return first['steps'][0]


# -- the pre-send gate, through the plugin's real publication_buffer -------------------------------------------

def _gate_open(plugin, text=NEEDS_YOU_1108, step=STEP_852F):
    """Open one extracted tile in the plugin's own store (the delivered 11:08 reply); return (store, request)."""
    store, ledger = plugin.core.ActionStore(), plugin._ledger()
    plugin.core.process_reply(text, 'session-a', ledger=ledger, action_store=store, action_owner=OWNER,
                              llm=lambda *a: json.dumps({'steps': [dict(step)], 'resolved': []}))
    [req] = [r for r in store.requests(OWNER) if r['state'] == 'open']
    return store, req


def _gate(plugin, monkeypatch, text, steps, repeats=None, **kw):
    payload = {'steps': [dict(s) for s in steps], 'resolved': []}
    if repeats is not None:
        payload['repeats'] = repeats
    monkeypatch.setattr(plugin, 'aux_llm', lambda *a: json.dumps(payload))
    return plugin.publication_buffer(session_id='session-a', platform='cli', final_response=text, **kw)


def _send(plugin, text, steps, repeats=None):
    """The delivered reply's transform (process_reply), as transform_llm_output runs it."""
    payload = {'steps': [dict(s) for s in steps], 'resolved': []}
    if repeats is not None:
        payload['repeats'] = repeats
    return plugin.core.process_reply(text, 'session-a', ledger=plugin._ledger(), action_store=plugin.core.ActionStore(),
                                     action_owner=OWNER, llm=lambda *a: json.dumps(payload))


def _open_titles(store):
    return [r['title'] for r in store.requests(OWNER) if r['state'] in ('open', 'needs_context')]


def _after_nudge(held):
    return {'attempt': 1, 'turn_messages': [{'role': 'assistant', 'content': 'draft', '_pre_final_gate_synthetic': True},
                                            {'role': 'user', 'content': held['nudge'], '_pre_final_gate_synthetic': True}]}


def test_the_1154_reply_is_held_pre_send_naming_the_open_request(plugin, monkeypatch):
    # Live 11:54: both a reminder sentence and the restated second question would have minted tiles.
    store, req = _gate_open(plugin)
    held = _gate(plugin, monkeypatch, STATUS_1154 + '\n\n' + NEEDS_YOU_1154, [STEP_64AE, STEP_60B8])
    assert held and held.get('hold') is True, held
    assert req['requestId'] in held['nudge'] and req['title'] in held['nudge']
    assert STEP_64AE['verbatim_span'] in held['nudge'] and 'macOS CI for tests marked' in held['nudge']
    assert plugin.OPEN_ASK_NUDGE_HEAD in held['nudge']
    # The agent drops the restatements; the re-sent reply asks nothing new, so it passes and mints nothing.
    revised = STATUS_1154.replace(' Two macOS CI choices still need you.', '')
    assert _gate(plugin, monkeypatch, revised, [], **_after_nudge(held)) is None
    _send(plugin, revised, [])
    assert _open_titles(store) == [req['title']]
    assert store.get(OWNER, req['requestId']) == req  # untouched: revision, title and scope


def test_the_1317_headline_is_held_pre_send_and_its_revision_leaves_one_tile(plugin, monkeypatch):
    # Live 13:17 (2288f5b4 / us_a9defb1d27): a status headline whose last sentence points at the open pick.
    store, req = _gate_open(plugin)
    held = _gate(plugin, monkeypatch, HEADLINE_1317, [STEP_A9DE])
    assert held and held.get('hold') is True and req['requestId'] in held['nudge'], held
    revised = HEADLINE_1317.replace(' The only thing waiting on you is the macOS CI pick.', '')
    assert _gate(plugin, monkeypatch, revised, [], **_after_nudge(held)) is None
    _send(plugin, revised, [])
    assert _open_titles(store) == [req['title']] and store.get(OWNER, req['requestId']) == req


def test_a_headline_inside_an_agent_written_directive_mints_nothing_and_is_not_held(plugin, monkeypatch):
    # The text of a ::user-step marker is an attribute, not prose: no block holds the span, so the transform mints
    # nothing for it and the gate, which mirrors the transform, does not hold the reply either.
    store, req = _gate_open(plugin)
    text = plugin.core.directive(['us_a9defb1d27'], 'session-a', HEADLINE_1317)
    assert _gate(plugin, monkeypatch, text, [STEP_A9DE]) is None
    _send(plugin, text, [STEP_A9DE])
    assert _open_titles(store) == [req['title']] and store.get(OWNER, req['requestId']) == req


def test_the_check_holds_once_and_a_resent_ask_gets_its_own_tile(plugin, monkeypatch):
    # The agent says "different decision" by re-sending unchanged: never held twice, never merged.
    store, req = _gate_open(plugin)
    text = 'Which Linux runner should the babybear repo use? A: Blacksmith 4vcpu Linux, B: GitHub-hosted ubuntu.'
    step = {'text': 'Pick the babybear Linux runner', 'kind': 'choose', 'verbatim_span': text,
            'options': ['A: Blacksmith 4vcpu Linux', 'B: GitHub-hosted ubuntu']}
    held = _gate(plugin, monkeypatch, text, [step])
    assert held and held.get('hold') is True
    assert _gate(plugin, monkeypatch, text, [step], **_after_nudge(held)) is None
    _send(plugin, text, [step])
    assert sorted(_open_titles(store)) == sorted([req['title'], 'Pick the babybear Linux runner'])
    assert store.get(OWNER, req['requestId']) == req


def test_the_check_never_holds_without_an_open_request_or_at_the_budget(plugin, monkeypatch):
    text = 'Which Linux runner should the babybear repo use? A: Blacksmith 4vcpu Linux, B: GitHub-hosted ubuntu.'
    step = {'text': 'Pick the babybear Linux runner', 'kind': 'choose', 'verbatim_span': text,
            'options': ['A: Blacksmith 4vcpu Linux', 'B: GitHub-hosted ubuntu']}
    assert _gate(plugin, monkeypatch, text, [step]) is None  # nothing open: a first ask is never held
    _gate_open(plugin)
    assert _gate(plugin, monkeypatch, text, [step], attempt=2, hold_budget=2) is None  # budget spent: deliver


def test_the_check_does_not_hold_an_ask_provenance_maps_onto_the_open_tile(plugin, monkeypatch):
    # The extractor's corroborated repeat, and the same evidence sentence, already land on the open tile.
    store, req = _gate_open(plugin)
    step_id = plugin._ledger().read('session-a')['steps'][0]['id']
    span = "**If you pick C, or don't answer,** nothing changes."
    assert _gate(plugin, monkeypatch, 'Still open from earlier:\n\n' + span,
                 [dict(STEP_852F, text='Pick the macOS CI option')]) is None  # same evidence sentence
    quote = 'Pick macOS CI option A, B, or C (B recommended): that one still needs you.'
    assert _gate(plugin, monkeypatch, quote, [], repeats=[{'id': step_id, 'verbatim_span': quote}]) is None
    assert _open_titles(store) == [req['title']]


def test_a_gate_held_draft_publishes_no_tile(plugin, monkeypatch):
    # 2c (refuted live: the 11:08 draft was delivered, ask-gate verdict ok, turn ended text_response 5636 chars).
    # The pre_final_gate callback only judges; the transform that publishes runs on the delivered reply alone.
    monkeypatch.setattr(plugin, 'aux_llm', lambda *a: json.dumps({'steps': [
        {'text': 'Approve deploying build 9', 'kind': 'approve', 'verbatim_span': 'Want me to deploy build 9?',
         'options': []}, dict(STEP_852F)], 'resolved': []}))
    out = plugin.publication_buffer(session_id='session-a', platform='cli',
                                    final_response='Want me to deploy build 9?\n\n' + NEEDS_YOU_1108)
    assert out and out.get('hold') is True
    assert plugin.core.ActionStore().requests(OWNER) == []
    assert plugin._ledger().read('session-a').get('steps', []) == []


def test_an_open_ask_hold_never_publishes_and_a_publish_hold_wins(plugin, monkeypatch):
    # The open-request hold judges only; an unplanned approval in the same reply gets the publish nudge first.
    store, req = _gate_open(plugin)
    held = _gate(plugin, monkeypatch, STATUS_1154, [STEP_64AE])
    assert held['hold'] and _open_titles(store) == [req['title']]
    both = _gate(plugin, monkeypatch, 'Want me to deploy build 9?\n\n' + STATUS_1154, [
        {'text': 'Approve deploying build 9', 'kind': 'approve', 'verbatim_span': 'Want me to deploy build 9?',
         'options': []}, STEP_64AE])
    assert both['hold'] and both['nudge'] == plugin.PUBLISH_NUDGE


# -- publication never merges by title, menu or scope: the live shapes and every reviewer probe stay two tiles ----
# A false merge is worse than a duplicate: the answer to one ask would be recorded on the other. Each of these
# merged into one tile under some round of PR #93's identity rules (reviews 5897-5898xxxxxx; cards t_01a1d695aceb1bb1,
# t_01a1d6a6bd2f3f8b, t_01a1d6b234acd4ac, t_01a1d6c233649a46, t_01a1d6d66cd9a092, t_01a1d6ec29639a32). The gate
# above, not publication, is what keeps the live restatements to one tile.

@pytest.mark.parametrize('text,steps', [
    pytest.param(STATUS_1154, [STEP_64AE], id='1154-reminder'),
    pytest.param(NEEDS_YOU_1154, [STEP_60B8], id='1154-restated-question'),
    pytest.param(HEADLINE_1317, [STEP_A9DE], id='1317-headline'),
])
def test_publication_never_folds_a_live_restatement_by_what_it_says(tmp_path, text, steps):
    # Had the agent re-sent it unchanged after the hold, it is a visible, withdrawable duplicate, never a merge.
    store, ledger = env(tmp_path)
    tile = _open_macos_tile(store, ledger)
    [before] = live(store)
    out = reply(store, ledger, text, [dict(s) for s in steps])
    assert len(live(store)) == 2 and tile['id'] not in {s['id'] for s in out['steps']}
    assert store.get(OWNER, before['requestId']) == before


def test_same_letters_with_other_labels_is_a_new_ask(tmp_path):
    store, ledger = env(tmp_path)
    _open_macos_tile(store, ledger)
    text = 'One more: which cache store should the scheduler use? A: Redis, B: SQLite files, C: in-memory only.'
    reply(store, ledger, text, [{'text': 'Pick the scheduler cache store: A, B or C', 'kind': 'choose',
                                 'verbatim_span': text, 'options': ['A: Redis', 'B: SQLite files', 'C: in-memory only']}])
    assert len(live(store)) == 2


def test_a_new_question_beside_a_quote_of_the_open_scope_is_a_new_ask(tmp_path):
    store, ledger = env(tmp_path)
    _open_macos_tile(store, ledger)
    text = (f"The tests-os card still asks {TESTS_OS_ASK} Separately, should the new lane run nightly or "
            "weekly?")
    reply(store, ledger, text, [{'text': 'Pick how often the new lane runs', 'kind': 'choose',
                                 'verbatim_span': 'Separately, should the new lane run nightly or weekly?',
                                 'options': ['A: nightly', 'B: weekly']}])
    assert len(live(store)) == 2


def test_two_lettered_questions_in_one_new_reply_stay_two_tiles(tmp_path):
    store, ledger = env(tmp_path)
    reply(store, ledger, NEEDS_YOU_1108, [dict(STEP_852F), {**STEP_60B8, 'verbatim_span':
          "**macOS CI for the tests marked as posix/macOS-only.** These tests never run on macOS in CI today."}])
    assert len(live(store)) == 2


def test_a_boilerplate_scope_sentence_is_not_the_open_ask(tmp_path):
    store, ledger = env(tmp_path)
    reply(store, ledger, NEEDS_YOU_1108, [{'text': 'Pick macOS CI option A, B, or C', 'kind': 'choose',
                                          'verbatim_span': "It's also in your For-you list.",
                                          'options': ['A: lane plus jobs', 'B: sub-minute jobs only', 'C: no spend']}])
    assert len(live(store)) == 1
    text = ("New one: where should nightly backups go? S3 or GCS, or skip them.\n"
            "   - **If you pick C, or don't answer,** nothing changes.")
    reply(store, ledger, text, [{'text': 'Pick the backup store', 'kind': 'choose',
                                 'verbatim_span': "**If you pick C, or don't answer,** nothing changes.",
                                 'options': ['S3 bucket', 'GCS bucket', 'skip backups']}])
    assert len(live(store)) == 2


@pytest.mark.parametrize('t1,s1,t2,s2', [
    pytest.param('Should I deploy build 9 to the hub tonight? A: yes, B: no.',
                 {'text': 'Deploy build 9 tonight?', 'options': ['A: yes', 'B: no']},
                 'Separate question: should I rotate the GitHub deploy key now? A: yes, B: no.',
                 {'text': 'Rotate the deploy key now?', 'options': ['A: yes', 'B: no']}, id='generic-yes-no'),
    pytest.param('Should the release notes template stay as it is? A: keep it as is, B: change it now.',
                 {'text': 'Keep the release notes template?', 'options': ['A: keep it as is', 'B: change it now']},
                 'And the PR template, a separate file? A: keep it as is, B: change it now.',
                 {'text': 'Keep the PR template?', 'options': ['A: keep it as is', 'B: change it now']},
                 id='generic-keep-change'),
    pytest.param('Where should the Linux PR lane run? A: PR lane on the mini, B: PR lane on the hub.',
                 {'text': 'Pick where the Linux lane runs', 'options': ['A: PR lane on the mini', 'B: PR lane on the hub']},
                 'And the Windows lane, which is a separate card? A: PR lane on worker-6, B: PR lane on worker-7.',
                 {'text': 'Pick where the Windows lane runs',
                  'options': ['A: PR lane on worker-6', 'B: PR lane on worker-7']}, id='only-the-target-differs'),
])
def test_generic_menus_are_not_the_same_decision(tmp_path, t1, s1, t2, s2):
    store = _two_replies(tmp_path, t1, {**s1, 'kind': 'choose', 'verbatim_span': t1},
                         t2, {**s2, 'kind': 'choose', 'verbatim_span': t2})
    assert len(live(store)) == 2


_DEPLOY_SCOPE = 'Deploy build 9 to the hub and restart its gateway.\n\nIf you decline, nothing changes and the card stays blocked.'
_RUNNER_REPLY = ('Different thing: want me to delete the stale runner images on worker-3?\n\n'
                 'If you decline, nothing changes and the card stays blocked.')
_RUNNER_STEP = {'text': 'Approve deleting stale runner images on worker-3', 'kind': 'approve',
                'verbatim_span': 'If you decline, nothing changes and the card stays blocked.', 'options': []}


def test_a_producer_approvals_boilerplate_line_does_not_absorb_a_new_approval(tmp_path):
    store, ledger = env(tmp_path)
    store.create(dict(OWNER), approval('Deploy build 9 to the hub', markdown=_DEPLOY_SCOPE))
    n0 = len(live(store))
    reply(store, ledger, _RUNNER_REPLY, [dict(_RUNNER_STEP)])
    assert len(live(store)) == n0 + 1


def test_the_publication_gate_holds_a_new_approval_beside_an_unrelated_ready_one(plugin, monkeypatch):
    store = plugin.core.ActionStore()
    store.create(dict(OWNER), approval('Deploy build 9 to the hub', markdown=_DEPLOY_SCOPE))
    monkeypatch.setattr(plugin, 'aux_llm', lambda *a: json.dumps({'steps': [dict(_RUNNER_STEP)], 'resolved': []}))
    out = plugin.publication_buffer(session_id='session-a', platform='cli', final_response=_RUNNER_REPLY)
    assert out and out.get('hold') is True, out


def _two_replies(tmp_path, t1, s1, t2, s2):
    store, ledger = env(tmp_path)
    reply(store, ledger, t1, [s1])
    assert len(live(store)) == 1
    reply(store, ledger, t2, [s2])
    return store


_PR_MENU = ['A: rebase and merge it', 'B: close it unmerged', 'C: leave it for review']
_RUNNER_MENU = ['A: Blacksmith macOS runner (~$120/mo)', 'B: self-hosted mini runner (free)']


@pytest.mark.parametrize('t1,s1,t2,s2', [
    pytest.param(
        'PR #12 (login fix) is green. What should I do with it? A: rebase and merge it, B: close it unmerged, C: leave '
        'it for review.', {'text': 'Decide PR #12', 'options': _PR_MENU},
        'Separately, PR #13 (billing refactor) is green too. A: rebase and merge it, B: close it unmerged, C: leave '
        'it for review.', {'text': 'Decide PR #13', 'options': _PR_MENU}, id='r1-same-pr-menu-two-prs'),
    pytest.param(
        'hermes-mods macOS CI: A: Blacksmith macOS runner (~$120/mo), B: self-hosted mini runner (free)?',
        {'text': 'Pick the hermes-mods macOS runner', 'options': _RUNNER_MENU},
        'babybear macOS CI, a separate budget line: A: Blacksmith macOS runner (~$120/mo), B: self-hosted mini '
        'runner (free)?', {'text': 'Pick the babybear macOS runner', 'options': _RUNNER_MENU}, id='r2-cost-menu-two-repos'),
    pytest.param(
        'Database migration: A: go ahead with the proposed plan today, B: hold off until next week.',
        {'text': 'Decide the DB migration timing',
         'options': ['A: go ahead with the proposed plan today', 'B: hold off until next week']},
        'Logo redesign: A: go ahead with the proposed plan today, B: hold off until next week.',
        {'text': 'Decide the logo redesign timing',
         'options': ['A: go ahead with the proposed plan today', 'B: hold off until next week']}, id='r3-generic-labels'),
    pytest.param(
        'Which CI compute should we buy? A: Blacksmith 6vcpu runner (~$120/mo), B: GitHub larger runner (~$770/mo)?',
        {'text': 'Pick CI compute', 'options': ['A: Blacksmith 6vcpu runner ~$120/mo', 'B: GitHub larger runner ~$770/mo']},
        'Which artifact storage should we buy? A: Blacksmith storage add-on (~$120/mo), B: GitHub storage add-on '
        '(~$770/mo)?', {'text': 'Pick artifact storage',
                        'options': ['A: Blacksmith storage add-on ~$120/mo', 'B: GitHub storage add-on ~$770/mo']},
        id='r4-shared-cost-figures'),
])
def test_the_same_menu_for_another_subject_is_a_new_ask(tmp_path, t1, s1, t2, s2):
    store = _two_replies(tmp_path, t1, {**s1, 'kind': 'choose', 'verbatim_span': t1},
                         t2, {**s2, 'kind': 'choose', 'verbatim_span': t2})
    assert len(live(store)) == 2, [r['title'] for r in live(store)]


_GENERIC_PICK_Q = '"If you pick A, an agent does the work; if you pick B, nothing changes."'
_GENERIC_APPROVE_Q = '"If you approve, an agent runs it as planned; if you decline, nothing changes."'


def test_quoted_boilerplate_in_scope_and_span_is_not_the_open_ask(tmp_path):
    store, ledger = env(tmp_path)  # r5
    t1 = f'The cache-store card asks: {_GENERIC_PICK_Q} A is Redis, B is SQLite.'
    reply(store, ledger, t1, [{'text': 'Pick the cache store', 'kind': 'choose', 'verbatim_span': 'A is Redis, B is SQLite.',
                               'options': ['A: Redis', 'B: SQLite']}])
    t2 = f'The logo-colour card asks the same: {_GENERIC_PICK_Q} So should the logo be blue or green?'
    reply(store, ledger, t2, [{'text': 'Pick the logo colour', 'kind': 'choose', 'verbatim_span': t2,
                               'options': ['A: blue', 'B: green']}])
    assert len(live(store)) == 2, [r['title'] for r in live(store)]


def test_a_new_approval_quoting_a_producer_approvals_boilerplate_is_a_new_ask(tmp_path):
    store, ledger = env(tmp_path)  # r6
    store.create(dict(OWNER), approval('Deploy build 9 to the hub',
                                       markdown=f'The deploy card asks: {_GENERIC_APPROVE_Q}\n\nDeploy build 9 and restart.'))
    text = f'Same terms as the deploy, {_GENERIC_APPROVE_Q} Should I also rotate the GitHub deploy key now?'
    reply(store, ledger, text, [{'text': 'Approve rotating the deploy key', 'kind': 'approve', 'verbatim_span': text,
                                 'options': []}])
    assert len(live(store)) == 2, [r['title'] for r in live(store)]


def test_the_publication_gate_holds_a_new_approval_quoting_ready_boilerplate(plugin, monkeypatch):
    store = plugin.core.ActionStore()  # r7
    store.create(dict(OWNER), approval('Deploy build 9 to the hub',
                                       markdown=f'The deploy card asks: {_GENERIC_APPROVE_Q}\n\nDeploy build 9 and restart.'))
    text = f'Same terms as the deploy, {_GENERIC_APPROVE_Q} Should I also rotate the GitHub deploy key now?'
    monkeypatch.setattr(plugin, 'aux_llm', lambda *a: json.dumps({'steps': [
        {'text': 'Approve rotating the deploy key', 'kind': 'approve', 'verbatim_span': text, 'options': []}],
        'resolved': []}))
    out = plugin.publication_buffer(session_id='session-a', platform='cli', final_response=text)
    assert out and out.get('hold') is True, out


def test_the_open_card_ask_quoted_beside_a_new_question_in_one_sentence_is_a_new_ask(tmp_path):
    store, ledger = env(tmp_path)  # r8
    reply(store, ledger, NEEDS_YOU_1108, [dict(STEP_852F)])
    span = f'Beyond the card ask {TESTS_OS_ASK} should the new lane also run on Linux, yes or no?'
    reply(store, ledger, span, [{'text': 'Should the new lane also run on Linux?', 'kind': 'choose',
                                 'verbatim_span': span, 'options': ['A: yes', 'B: no']}])
    assert len(live(store)) == 2, [r['title'] for r in live(store)]


_R2_T2 = ('babybear macOS CI, a separate budget line: A: Blacksmith macOS runner (~$120/mo), B: self-hosted mini '
          'runner (free)?')


@pytest.mark.parametrize('text,t1,t2', [
    pytest.param('Pick the macOS CI runner',
                 'For hermes-mods, which macOS CI runner do you want? A: Blacksmith macOS runner (~$120/mo), B: '
                 'self-hosted mini runner (free).',
                 'For babybear, which macOS CI runner do you want? A: Blacksmith macOS runner (~$120/mo), B: '
                 'self-hosted mini runner (free).', id='p1-repo-named-only-in-the-evidence'),
    pytest.param('Pick the macOS runner',
                 'hermes-mods macOS CI: A: Blacksmith macOS runner (~$120/mo), B: self-hosted mini runner (free)?',
                 _R2_T2, id='p1b-r2-evidence-generic-text'),
    pytest.param('Pick the macOS CI runner',
                 'For hermes-mods:\nWhich one? A: Blacksmith macOS runner (~$120/mo), B: self-hosted mini runner (free).',
                 'For babybear:\nWhich one do you want? A: Blacksmith macOS runner (~$120/mo), B: self-hosted mini '
                 'runner (free).', id='p1c-repo-named-before-a-bare-menu-sentence'),
])
def test_the_same_generic_step_text_for_two_repos_named_in_the_evidence_is_a_new_ask(tmp_path, text, t1, t2):
    span2 = t2.split('\n')[-1]
    store = _two_replies(tmp_path, t1, {'text': text, 'kind': 'choose', 'options': _RUNNER_MENU,
                                        'verbatim_span': t1.split('\n')[-1]},
                         t2, {'text': text, 'kind': 'choose', 'options': _RUNNER_MENU, 'verbatim_span': span2})
    assert len(live(store)) == 2, [r['title'] for r in live(store)]


_OPEN_HM = ('For hermes-mods, which macOS CI runner do you want? A: Blacksmith macOS runner (~$120/mo), B: self-hosted '
            'mini runner (free).')
_MENU_TXT = 'A: Blacksmith macOS runner (~$120/mo), B: self-hosted mini runner (free).'
_S = {'text': 'Pick the macOS CI runner', 'kind': 'choose', 'options': list(_RUNNER_MENU)}
_BULLETS = '- Blacksmith macOS runner (~$120/mo)\n- self-hosted mini runner (free)'
_REMIND = 'That macOS CI runner pick still needs you.'

# (t1, t2, span2): every probe of reviews 5898199961 (M, R), 5898461042 (F, K) and 5898725409 (N, menu, scope).
# span2 None = the whole t2. Each folded a distinct decision onto the open tile under some identity rule.
PROBES = [
    pytest.param(_OPEN_HM, f'Next up is babybear. Which macOS CI runner do you want? {_MENU_TXT}',
                 f'Which macOS CI runner do you want? {_MENU_TXT}', id='M1-subject-in-the-sentence-before'),
    pytest.param(_OPEN_HM, f'Now babybear.\n\nWhich one do you want? {_MENU_TXT}',
                 f'Which one do you want? {_MENU_TXT}', id='M2-subject-in-the-paragraph-before'),
    pytest.param(_OPEN_HM, f'## babybear\n\nWhich one do you want? {_MENU_TXT}',
                 f'Which one do you want? {_MENU_TXT}', id='M2b-subject-in-a-heading'),
    pytest.param(_OPEN_HM, f'**babybear**\nWhich macOS CI runner do you want here? {_MENU_TXT}',
                 f'Which macOS CI runner do you want here? {_MENU_TXT}', id='M2c-subject-in-a-bold-lead'),
    pytest.param(f'For phase two, which macOS CI runner do you want? {_MENU_TXT}',
                 f'For phase three, which macOS CI runner do you want? {_MENU_TXT}', None, id='M3-number-words'),
    pytest.param(f'For Mac mini 2, which macOS CI runner do you want? {_MENU_TXT}',
                 f'For Mac mini 3, which macOS CI runner do you want? {_MENU_TXT}', None, id='M3b-single-digits'),
    pytest.param(f'For the left Mac, which macOS CI runner do you want? {_MENU_TXT}',
                 f'For the other Mac, which macOS CI runner do you want? {_MENU_TXT}', None, id='M3c-left-other'),
    pytest.param(f"For Will's laptop, which macOS CI runner do you want? {_MENU_TXT}",
                 f"For May's laptop, which macOS CI runner do you want? {_MENU_TXT}", None, id='M3d-will-may'),
    pytest.param('For hermes-mods (babybear stays on GitHub runners), which macOS CI runner do you want? '
                 + _MENU_TXT, f'For babybear, which macOS CI runner do you want? {_MENU_TXT}', None,
                 id='M5-other-subject-as-context-in-the-open-ask'),
    pytest.param(_OPEN_HM, 'The babybear macOS CI runner pick still needs you.', None, id='R1-reminder-other-subject'),
    pytest.param(_OPEN_HM, 'Now babybear.\n\nThat one still needs you.', 'That one still needs you.',
                 id='R2-bare-reminder-after-another-subject'),
    pytest.param('For hermes-mods (babybear stays on GitHub runners), which macOS CI runner do you want? '
                 + _MENU_TXT, 'The babybear macOS CI runner pick still needs you.', None,
                 id='R3-reminder-naming-the-open-asks-context-subject'),
    pytest.param(_OPEN_HM, f'For babybear: {_MENU_TXT}\n\n{_REMIND}', _REMIND, id='R4-reminder-beside-a-new-menu'),
    pytest.param(_OPEN_HM, f'For babybear, choose one:\n{_BULLETS}\n\n{_REMIND}', _REMIND, id='F1-bulleted-menu'),
    pytest.param(_OPEN_HM, 'For babybear: 1) Blacksmith macOS runner (~$120/mo), 2) self-hosted mini runner (free).'
                 f'\n\n{_REMIND}', _REMIND, id='F2-numbered-menu'),
    pytest.param(_OPEN_HM, 'For babybear: Blacksmith macOS runner (~$120/mo) or self-hosted mini runner (free).'
                 f'\n\n{_REMIND}', _REMIND, id='F3-x-or-y'),
    pytest.param(_OPEN_HM, 'For babybear: **A)** Blacksmith macOS runner (~$120/mo), **B)** self-hosted mini runner '
                 f'(free).\n\n{_REMIND}', _REMIND, id='F4-bold-letters'),
    pytest.param(_OPEN_HM, 'For babybear: A. Blacksmith macOS runner (~$120/mo), B. self-hosted mini runner (free).'
                 f'\n\n{_REMIND}', _REMIND, id='F5-dot-letters-inline'),
    pytest.param(_OPEN_HM, 'Next up is babybear. The macOS CI runner pick still needs you.',
                 'The macOS CI runner pick still needs you.', id='F6-subject-in-the-sentence-before'),
    pytest.param(_OPEN_HM, 'Now babybear.\n\nThe macOS CI runner pick still needs you.',
                 'The macOS CI runner pick still needs you.', id='F7-subject-in-the-paragraph-before'),
    pytest.param(_OPEN_HM, '## babybear\n\nThe macOS CI runner pick still needs you.',
                 'The macOS CI runner pick still needs you.', id='F8-heading-before'),
    pytest.param(_OPEN_HM, 'babybear is next. I need a decision on the macOS CI runner.',
                 'I need a decision on the macOS CI runner.', id='F9-need-a-decision'),
    pytest.param(_OPEN_HM, 'Now babybear. Also choose the macOS CI runner.', 'Also choose the macOS CI runner.',
                 id='F10-also-choose'),
    pytest.param(_OPEN_HM, 'babybear next. I need you to pick the macOS CI runner.',
                 'I need you to pick the macOS CI runner.', id='F11-need-you-to-pick'),
    pytest.param(_OPEN_HM, 'babybear is set up now. The only thing waiting on you is the macOS CI runner pick.',
                 'The only thing waiting on you is the macOS CI runner pick.', id='F12-1317-wording-after-a-status'),
    pytest.param(_OPEN_HM, 'The only thing waiting on you is the macOS CI runner pick.', None, id='K1-1317-headline'),
    pytest.param(_OPEN_HM, 'Two macOS CI runner choices still need you.', None, id='K6-plural'),
    pytest.param(_OPEN_HM, 'The macOS CI runner pick still needs you. Want me to merge #93 meanwhile?',
                 'The macOS CI runner pick still needs you.', id='K4-other-question'),
    pytest.param(_OPEN_HM, 'Moving babybear to Blacksmith needs your OK.\n\n' + _REMIND, _REMIND, id='K8-needs-your-ok'),
    pytest.param(_OPEN_HM, '> For babybear, should the macOS CI runner move to Blacksmith?\n\n' + _REMIND, _REMIND,
                 id='K9-block-quote'),
] + [pytest.param(_OPEN_HM, lead + '\n\n' + _REMIND, _REMIND, id=pid) for pid, lead in {
    # review 5898725409: asks with no trigger word
    'N1-green-light': 'Moving babybear to Blacksmith ($120/mo) just needs your green light.',
    'N2-unless-you-object': "I'll move babybear to Blacksmith ($120/mo) tomorrow unless you object.",
    'N3-vs': 'babybear also needs a runner: Blacksmith ($120/mo) vs the mini (free).',
    'N4-slash': 'babybear runner: Blacksmith ($120/mo) / the mini (free), your word.',
    'N5-say-the-word': "Say the word and I'll move babybear to Blacksmith ($120/mo).",
    'N6-thumbs-up': 'A thumbs up here moves babybear to Blacksmith ($120/mo).',
    'N7-waiting-for-word': 'babybear: moving it to Blacksmith costs $120/mo. Waiting for your word on that.',
    'N8-fine-to': 'Fine to move babybear to Blacksmith ($120/mo) too.',
    'N9-yes-no': 'Move babybear to Blacksmith ($120/mo): yes/no.',
    'N10-go-no-go': 'I still need a go/no-go on moving babybear to Blacksmith ($120/mo).',
    'N11-ping-me': 'Ping me if babybear should move to Blacksmith ($120/mo).',
    'N12-holding-for-you': 'Holding the babybear Blacksmith move ($120/mo) until you sign it off.',
    # review 5898725409: menus the lexical gate missed
    'MN1-brackets': 'For babybear: [A] Blacksmith macOS runner (~$120/mo) [B] self-hosted mini runner (free)',
    'MN2-option-1-2-no-punct': 'For babybear: option 1 Blacksmith macOS runner, option 2 self-hosted mini runner.',
    'MN3-roman': 'For babybear: (i) Blacksmith macOS runner, (ii) self-hosted mini runner.',
    'MN4-table': ('For babybear, the runners:\n\n| Runner | Cost |\n|---|---|\n| Blacksmith macOS | $120/mo |\n'
                  '| self-hosted mini | free |'),
    'MN5-bullets-full-sentences-no-colon': ('For babybear there are two routes.\n- Move it to the Blacksmith macOS '
                                            'runner, $120 a month.\n- Keep it on the self-hosted mini runner, free.'),
    'MN6-bold-lines': '**Blacksmith** — $120/mo for babybear\n**Mini** — free for babybear',
    'MN7-letters-split-by-text': ('For babybear:\nA) Blacksmith macOS runner, $120/mo.\nIt is the fastest.\n'
                                  'B) self-hosted mini runner, free.'),
    'MN8-emoji-numbers': 'For babybear: 1\ufe0f\u20e3 Blacksmith macOS runner 2\ufe0f\u20e3 self-hosted mini runner',
    'MN9-headings': '### babybear option 1: Blacksmith ($120/mo)\n\n### babybear option 2: the mini (free)',
    'MN10-first-second': 'For babybear, first route: Blacksmith ($120/mo). Second route: the mini (free).',
    'S2-open-menu-quoted-for-another-subject': ('For babybear as well: "A: Blacksmith macOS runner (~$120/mo), B: '
                                                'self-hosted mini runner (free)."'),
    'W1-single-quoted-ask': "The babybear card asks: 'Move babybear to Blacksmith now'. ",
}.items()]


@pytest.mark.parametrize('t1,t2,span2', PROBES)
def test_no_probe_ever_folds_a_distinct_decision_onto_the_open_tile(tmp_path, t1, t2, span2):
    store = _two_replies(tmp_path, t1, {**_S, 'verbatim_span': t1}, t2, {**_S, 'verbatim_span': span2 or t2})
    assert len(live(store)) == 2, [r['title'] for r in live(store)]


@pytest.mark.parametrize('t1,t2,span2', PROBES)
def test_every_probe_is_held_pre_send_never_mapped(plugin, monkeypatch, t1, t2, span2):
    # The gate never decides identity either: each probe is returned to the agent with the open request named.
    store, req = _gate_open(plugin, t1, {**_S, 'verbatim_span': t1})
    held = _gate(plugin, monkeypatch, t2, [{**_S, 'verbatim_span': span2 or t2}])
    assert held and held.get('hold') is True and req['requestId'] in held['nudge'], held


def test_scope_probes_stay_two_tiles(tmp_path):
    # Review 5898725409: a request sentence the open scope holds, and the live card ask quoted for another repo.
    t1 = (_OPEN_HM + ' Separately, please approve deleting the stale runner images on worker-3. '
          'A thumbs up moves the babybear lane too.')
    store = _two_replies(tmp_path, t1, {**_S, 'verbatim_span': t1},
                         'Please approve deleting the stale runner images on worker-3.\n\n' + _REMIND,
                         {**_S, 'verbatim_span': _REMIND})
    assert len(live(store)) == 2
    store, ledger = env(tmp_path / 'live')
    _open_macos_tile(store, ledger)
    reply(store, ledger, f'babybear has the same choice. The card asks: {BLACKSMITH_ASK}\n\n' + HEADLINE_1317,
          [dict(STEP_A9DE)])
    assert len(live(store)) == 2


def test_the_1317_headline_beside_a_new_unlettered_menu_is_its_own_tile(tmp_path):
    store, ledger = env(tmp_path)  # F14, the live shape
    _open_macos_tile(store, ledger)
    text = HEADLINE_1317 + ('\n\nFor babybear, a separate budget line:\n- Blacksmith 6vcpu macOS (~$26/day)\n'
                            '- sub-minute jobs only (~$4/day)\n- no spend')
    reply(store, ledger, text, [dict(STEP_A9DE)])
    assert len(live(store)) == 2


def test_directly_asks_still_blanks_short_quotes():
    assert core.directly_asks('The card log says "please approve it".') is False
    assert core.directly_asks('Its title was "Can you confirm?" earlier.') is False
    assert core.directly_asks('Can you confirm?') is True
