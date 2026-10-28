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


def reply(store, ledger, text, steps, repeats=None, session='session-a', seen=None):
    payload = {'steps': steps, 'resolved': []}
    if repeats is not None:
        payload['repeats'] = repeats

    def llm(system, user):
        if seen is not None:
            seen.append((system, user))
        return json.dumps(payload)
    return core.process_reply(text, session, ledger=ledger, action_store=store,
                              action_owner=dict(OWNER, storedSessionId=session), llm=llm)


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


def test_extractor_same_title_on_a_later_reply_reuses_the_open_tile(tmp_path):
    # Hub chat ..37ae77: round 2 re-extracted both card asks with the SAME text and a new span: two more tiles.
    store, ledger = env(tmp_path)
    titles = ['Decide on t_aaaa1111: register one spare test runner',
              'Decide on t_bbbb2222: approve a dedicated build machine']
    first = reply(store, ledger, CARDS_1, card_steps(CARDS_1.split('\n')[1:], titles))
    second = reply(store, ledger, CARDS_2, card_steps(CARDS_2.split('\n')[1:], titles))
    assert len(live(store)) == 2
    assert [s['id'] for s in second['steps']] == [s['id'] for s in first['steps']]  # framed onto the same tiles


def test_extractor_reworded_ask_naming_the_same_card_reuses_the_open_tile(tmp_path):
    # Round 3 of the same chat reworded the title ("Decide whether to register ... (t_aaaa1111)").
    store, ledger = env(tmp_path)
    reply(store, ledger, CARDS_1, card_steps(CARDS_1.split('\n')[1:], [
        'Decide on t_aaaa1111: register one spare test runner',
        'Decide on t_bbbb2222: approve a dedicated build machine']))
    reply(store, ledger, CARDS_3, card_steps(CARDS_3.split('\n')[1:], [
        'Decide whether to register one spare test runner (t_aaaa1111)',
        'Approve a dedicated build machine (t_bbbb2222)']))
    assert sorted(r['title'] for r in live(store)) == ['Decide on t_aaaa1111: register one spare test runner',
                                                       'Decide on t_bbbb2222: approve a dedicated build machine']


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


def test_extractor_restating_a_published_title_does_not_mint_a_second_tile(tmp_path):
    # Hub chat ..67cb2d: the reply bolded the producer request's own title; the model paraphrased it.
    store, ledger = env(tmp_path)
    title = 'Approve the notify fix (accept not-found exit 1 + regression test)?'
    store.create(OWNER, approval(title))
    out = reply(store, ledger, f'**{title}**\n\nThe plan is in the request.', [{
        'text': 'Approve the notify fix (exit-1 not-found handling, regression tests)', 'kind': 'approve',
        'verbatim_span': f'**{title}**', 'options': []}])
    assert len(live(store)) == 1 and out['steps'] == []


def test_extractor_choice_menu_of_the_open_approval_is_not_a_second_ask(tmp_path):
    # Same chat: "**Your choices:** Approve fix / Change plan / Decline" became its own choice tile.
    store, ledger = env(tmp_path)
    store.create(OWNER, approval('Approve the notify fix?', labels=('Approve fix', 'Change plan', 'Decline')))
    text = ('**Your choices:**\n- **Approve fix:** an agent does steps 1-6.\n- **Change plan:** tell me what to '
            'change.\n- **Decline:** nothing changes.')
    reply(store, ledger, text, [{'text': 'Choose: approve fix, change plan, or decline the notify repair',
                                 'kind': 'choose', 'verbatim_span': '**Your choices:**',
                                 'options': ['Approve fix', 'Change plan', 'Decline']}])
    assert len(live(store)) == 1


def test_extractor_two_readings_of_one_reminder_sentence_mint_nothing(tmp_path):
    # Same chat, 21:39: one sentence produced an approve AND a choose step next to the open approval.
    store, ledger = env(tmp_path)
    store.create(OWNER, approval('Approve the notify fix (accept not-found exit 1 + regression tests)?'))
    span = 'The fix to `notify.py` is still waiting for your approval.'
    reply(store, ledger, "I've updated the plan. " + span, [
        {'text': 'Approve the notify fix (accept not-found exit 1 + regression tests)', 'kind': 'approve',
         'verbatim_span': span, 'options': []},
        {'text': 'Choose whether to approve the notify repair, change the plan, or decline it', 'kind': 'choose',
         'verbatim_span': span, 'options': ['Approve', 'Change the plan', 'Decline']}])
    assert len(live(store)) == 1


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

def test_tool_reask_with_the_same_title_and_plan_revises_the_open_request(plugin, tmp_path):
    first = publish(plugin, {'request': payload_of(approval('Approve the notify fix?'))})['request']
    again = publish(plugin, {'request': payload_of(approval('Approve the notify fix?'))})
    store = plugin.core.ActionStore()
    assert [r['requestId'] for r in live(store)] == [first['requestId']]
    assert again['request']['requestId'] == first['requestId'] and again['request']['revision'] == 2
    assert 'revised' in again['note'] and first['requestId'] in again['note']


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


def test_tool_reask_collapses_existing_duplicates_and_keeps_extracted_aliases(plugin, tmp_path):
    store, ledger = plugin.core.ActionStore(), plugin.core.Ledger()
    title = 'Approve the notify fix?'
    for _ in range(2):  # two extracted duplicates, as the extractor left them before this fix
        step = {'text': title, 'kind': 'approve', 'verbatim_span': title, 'options': [], 'session': 'session-a',
                'id': plugin.core.new_step_id(), 'status': 'open', 'created': 1, 'paragraph': title}
        plugin.core.ActionStore.create(store, OWNER, plugin.core.legacy_request(OWNER, step, dict(
            gatewayId=OWNER['gatewayId'], profile='default', type='user_step', id=step['id'])))
    older, newer = live(store)
    out = publish(plugin, {'request': payload_of(approval(title, markdown='The full exact plan.'))})['request']
    assert out['requestId'] == newer['requestId'] and out['scope']['complete'] and out['aliases'] == newer['aliases']
    assert [r['requestId'] for r in live(store)] == [newer['requestId']]
    gone = store.view(OWNER, older['requestId'])
    assert gone['state'] == 'withdrawn' and newer['requestId'] in gone['withdrawal']['reason']
    assert 'resolution' not in gone  # supersede is not a decision


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
