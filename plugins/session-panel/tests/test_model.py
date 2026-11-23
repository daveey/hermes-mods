"""Public structural counterparts of private hub goldens (raw replay attached to the card)."""
import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('panel_model_test', ROOT / 'model.py')
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)
NOW = 1795281000


def canonical_outcome(kind='done', revision=9):
    stripe = {'done': 'done', 'running': 'none', 'decision': 'stuck', 'unresolved': 'attention', 'waiting': 'waiting', 'muted': 'blocked'}[kind]
    return {'kind': kind, 'status': {'status_version': 1, 'revision': revision, 'generation': 3,
        'identity': {'gateway': 'edge', 'profile': 'default', 'session_id': 'chat', 'owner_epoch': 'epoch'},
        'kind': kind, 'coverage': {'state': 'complete', 'reasons': []},
        'presentation': {'stripe': stripe, 'reason_codes': ['fixture']}}}


def bundle(*tasks, **extra):
    return {'computed_at': NOW, 'tasks': list(tasks), 'done': [], 'cards': {}, **extra}


def task(tid='t_abc123', status='blocked', **kw):
    return {'id': tid, 'gateway': 'remote', 'profile': 'default', 'board': 'default',
            'title': 'Test card', 'status': status, **kw}


def human():
    return {'action': 'Pick a route. Unblock = execute it; archive = drop it.',
            'why_human': 'Tried: checked both routes. Only you can: approve the extra budget.',
            'interaction': 'approval',
            'evidence': 'Both route estimates were checked against the published rate card.',
            'scope': 'Situation: A proof needs more runs and the live test can run today.\nOptions:\nA) Fund proof: costs money and time; reversible.\nC) Test live: cheap bounded test; reversible.\nRecommend: C - test before spending more.',
            'choices': [{'label': 'C: test live (recommended)', 'value': 'C'}, {'label': 'A: fund proof', 'value': 'A'}]}


def request(state='open'):
    h = human()
    return {'requestId': 'req-1', 'revision': 3, 'state': state, 'title': h['action'],
            'owner': {'gatewayId': 'remote', 'profile': 'default', 'storedSessionId': 'chat'},
            'scope': {'markdown': h['why_human'] + '\n\n' + h['scope'], 'sourceRefs': [{'type': 'kanban', 'gatewayId': 'remote', 'profile': 'default', 'board': 'default', 'id': 't_abc123'}]},
            'choices': [{'id': 'card-0', 'label': h['choices'][0]['label']}, {'id': 'card-1', 'label': h['choices'][1]['label']}]}


def project(records, *, now):
    # Injected boundary only; production uses core's canonical projector, never this fixture.
    return {'state': records[0]['state'], 'sourced': True}


def observed(row, kind='card', state='running'):
    return {**row, 'execution_observations': [{'identity': {'kind': kind, 'work_id': row['id'], 'gateway': row.get('gateway'), 'profile': row.get('profile'), 'board': row.get('board')}, 'state': state}]}


def held_bundle(status='triage', remote=False, kind='decision_retained', **kw):
    row = task(status=status, remote=remote, source='replica' if remote else 'live',
               wait={'wait_state': 'human'} if kw.get('human_request') else {'wait_state': 'waiting', 'waiting_on': 'triage'}, **kw)
    payload = {'disposition': {'reason': 'scope_stopped', 'next_step': 'Reconcile the remaining allowance.'}} if kind == 'decision_retained' else {'reason': 'The stage allowance is exhausted.'}
    return bundle(row, cards={row['id']: {'detail': {'task': dict(row), 'events': [{'kind': kind, 'payload': payload}]}}})


def test_held_cards_name_agent_not_human_even_without_execution_evidence():
    for status in ('triage', 'blocked'):
        for remote in (False, True):
            for kind in ('decision_retained', 'spend_budget_triaged', 'decision_requested'):
                b = held_bundle(status, remote, kind)
                m = model.reduce_panel(b, now=NOW)
                row = m['sections']['waiting'][0]
                assert not m['sections']['needs_you'] and not m['sections']['stuck']
                assert row['wait_kind'] == 'agent'
                assert row['detail'].startswith("waiting on this chat's agent to decide: ")
                assert 'allowance' in row['detail']
                assert row['gateway'] == 'remote'
                assert m['header']['wait_kind'] == 'agent'
                assert m == model.reduce_panel(b, now=NOW)
                b['cards']['t_abc123']['detail']['events'][0]['payload'] = {'reason': 'Choose the next scope.'}
                assert m['revision'] != model.reduce_panel(b, now=NOW)['revision']


def test_triage_live_human_request_or_bound_open_request_is_red():
    for with_card_ask in (False, True):
        b = held_bundle(human_request=human() if with_card_ask else None, block_kind='needs_input')
        if not with_card_ask:
            b['requests'] = [request()]
        m = model.reduce_panel(b, now=NOW)
        assert len(m['sections']['needs_you']) == 1
        assert not m['sections']['waiting']


def test_held_stale_source_remains_unknown_not_claimed_agent_custody():
    b = held_bundle(remote=True, stale=True)
    row = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert 'status unknown' in row['detail']
    assert row.get('wait_kind') != 'agent'


def test_current_nonhuman_wait_does_not_revive_preserved_human_fields():
    b = held_bundle(human_request=human(), block_kind='needs_input')
    b['tasks'][0]['wait'] = {'wait_state': 'waiting', 'waiting_on': 'triage'}
    m = model.reduce_panel(b, now=NOW)
    assert not m['sections']['needs_you']
    assert m['sections']['waiting'][0]['wait_kind'] == 'agent'


def test_new_block_episode_supersedes_old_decision_evidence():
    b = held_bundle(status='blocked')
    b['tasks'][0]['wait'] = {'wait_state': 'stalled', 'waiting_on': 'external job'}
    b['cards']['t_abc123']['detail']['events'].append({'kind': 'blocked', 'payload': {'reason': 'external job stopped'}})
    m = model.reduce_panel(b, now=NOW)
    assert not m['sections']['waiting']
    assert m['sections']['stuck'][0].get('wait_kind') != 'agent'


def test_stale_producer_does_not_vouch_for_header_and_overdue_clock_keeps_owner():
    for on in ('CI job 42', 'deploy release 42', 'the clock until 2026-11-25 12:00 UTC'):
        b = held_bundle(remote=True, stale=True)
        b['tasks'][0]['wait']['waiting_on'] = on
        m = model.reduce_panel(b, now=NOW)
        assert 'wait_kind' not in m['sections']['waiting'][0]
        assert 'wait_kind' not in m['header']
    b = held_bundle()
    b['tasks'][0]['wait']['waiting_on'] = 'the clock until 2026-11-20 12:00 UTC'
    for wire in (False, True):
        row = model.reduce_panel(b, now=NOW, wire=wire)['sections']['waiting'][0]
        assert row['wait_kind'] == 'clock' and 'overdue' in row['detail']
        assert 'agent to decide' not in row['detail']


def test_shared_triage_producer_precedes_agent_hold():
    for on, kind in (('CI job 42', 'ci'), ('deploy release 42', 'deploy'), ('the clock until 2026-11-25 12:00 UTC', 'clock')):
        parent = task('t_abcdef', status='triage', title='Publish the release', wait={'wait_state': 'waiting', 'waiting_on': on})
        b = bundle(task('t_111111', status='todo', upstream=parent),
                   task('t_222222', status='todo', upstream=parent), cards={parent['id']: {'detail': {'task': parent}}})
        m = model.reduce_panel(b, now=NOW)
        assert all(row['wait_kind'] == kind for row in m['sections']['waiting'])
        assert m['groups'][0]['wait_kind'] == kind
        assert m['header']['wait_kind'] == kind


def test_producer_waits_stay_light_blue_and_do_not_become_owner_decisions():
    for on, kind in (('CI job 42', 'ci'), ('deploy release 42', 'deploy'),
                     ('the clock until 2026-11-25 12:00 UTC', 'clock')):
        b = held_bundle()
        b['tasks'][0]['wait']['waiting_on'] = on
        row = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
        assert row['wait_kind'] == kind
        assert "agent to decide" not in row['detail']
    parent = task('t_abcdef', status='running', title='Publish the release')
    b = bundle(task(status='todo', upstream=parent),
               cards={parent['id']: {'detail': {'task': parent}}})
    row = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert row['wait_kind'] == 'card' and '(running)' in row['detail']


def test_contained_hold_preserves_release_actor_and_shared_held_group():
    b = held_bundle()
    b['tasks'][0]['wait'] = {'wait_state': 'waiting', 'contained': {'authority': {'id': 't_abcdef'}},
                            'waiting_on': 'release by card t_abcdef (todo)'}
    row = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert row['wait_kind'] == 'agent'
    assert 't_abcdef' in ' '.join([row['detail'], *row['details']])
    parent = task('t_abcdef', status='triage', title='Decide the next stage')
    b = bundle(task('t_111111', status='todo', upstream=parent),
               task('t_222222', status='todo', upstream=parent),
               cards={parent['id']: {'detail': {'task': parent}}})
    m = model.reduce_panel(b, now=NOW)
    assert len(m['groups']) == 1
    assert m['groups'][0]['section'] == 'waiting'
    assert m['groups'][0]['wait_kind'] == 'agent'
    assert "this chat's agent" in m['groups'][0]['detail']


def test_click_admission_is_canonical_owner_bound_and_revisioned():
    r = request()
    r['owner']['gatewayId'] = 'install:source'
    r['scope']['complete'] = True
    r['choices'][0]['effect'] = 'answer'
    b = bundle(requests=[r], gateway='source', gateway_aliases=['install:source'])
    original = copy.deepcopy(b)
    first = model.reduce_panel(b, now=NOW)
    action = first['sections']['needs_you'][0]['actions'][-1]
    assert action['source_owner'] is True
    assert action['owner'] == r['owner']
    assert action['admission'] == {
        'state': 'open', 'resolved': False, 'scope_complete': True,
        'choices': [{'id': c['id'], 'effect': c.get('effect'), 'label': c['label']} for c in r['choices']]}
    assert b == original
    unbound = model.reduce_panel(bundle(requests=[r]), now=NOW)
    assert 'admission' not in unbound['sections']['needs_you'][0]['actions'][-1]
    assert first['revision'] == model.reduce_panel(b, now=NOW)['revision']
    b['gateway_aliases'] = ['install:foreign']
    foreign = model.reduce_panel(b, now=NOW)
    assert foreign['sections']['needs_you'][0]['actions'][-1]['source_owner'] is False
    assert foreign['revision'] != first['revision']
    for invalid in (False, None, 'true'):
        r['scope']['complete'] = invalid
        assert model.reduce_panel(b, now=NOW)['sections']['needs_you'][0]['actions'][-1]['admission']['scope_complete'] is False
    r['scope'].update(complete=True, markdown=' ')
    assert model.reduce_panel(b, now=NOW)['sections']['needs_you'][0]['actions'][-1]['admission']['scope_complete'] is False
    r['state'] = 'resolved'
    r['resolution'] = {'operationId': 'done'}
    assert not model.reduce_panel(b, now=NOW)['sections']['needs_you']


def test_own_letter_ask_eve_shape_and_canonical_ids():
    b = bundle(task(human_request=human(), wait={'wait_state': 'human'}), requests=[request()])
    m = model.reduce_panel(b, now=NOW)
    ask = m['sections']['needs_you'][0]
    assert [(o['letter'], o['choice_id']) for o in ask['options']] == [('C', 'card-0'), ('A', 'card-1')]
    assert ask['options'][0]['recommended'] is True
    assert ask['options'][0]['detail'] == 'Cheap bounded test; reversible.'
    assert ask['options'][1]['detail'] == 'Costs money and time; reversible.'
    assert ask['gateway'] == 'remote'
    assert ask['only_you'] == 'Only you can: approve the extra budget.'
    assert ask['actions'][-1]['revision'] == 3
    assert m['header']['counts']['needs_you'] == 1


def test_canonical_header_never_rederived_from_rows():
    m = model.reduce_panel(bundle(task(status='running'), outcome_json=canonical_outcome('decision')), now=NOW)
    assert m['header']['state'] == 'decision'
    assert not m['sections']['needs_you']
    assert model.reduce_panel(bundle(), now=NOW)['header']['state'] == 'idle'


def assert_derived(b, state):
    m = model.reduce_panel(b, now=NOW, projector=project)
    assert m['header']['state'] == state
    assert m['header']['source'] == 'derived'
    assert m['header']['status'] is None
    assert m == model.reduce_panel(b, now=NOW, projector=project)
    return m


def test_derived_needs_you_precedes_stuck_running_and_waiting():
    b = bundle(task(human_request=human(), wait={'wait_state': 'human'}),
               observed(task('t_run', status='running')),
               task('t_stuck', status='scheduled', wait={'wait_state': 'stalled'}),
               task('t_wait', status='scheduled'))
    assert assert_derived(b, 'needs-you')['header']['label'] == 'needs you'


def test_derived_stuck_precedes_running_and_waiting():
    assert_derived(bundle(task(status='scheduled', wait={'wait_state': 'stalled'}),
                          observed(task('t_run', status='running')), task('t_wait', status='scheduled')), 'stuck')


def test_derived_running_precedes_waiting():
    assert_derived(bundle(observed(task(status='running')), task('t_wait', status='scheduled')), 'running')


def test_derived_waiting_includes_ready_work_not_idle():
    assert_derived(bundle(task(status='scheduled')), 'waiting')
    assert_derived(bundle(task(status='ready')), 'waiting')
    assert_derived(bundle(task(status='todo')), 'waiting')


def test_derived_idle_empty_and_all_cards_settled():
    assert_derived(bundle(), 'idle')
    assert_derived(bundle(done=[task(status='done')]), 'idle')
    assert_derived(bundle(outcome_json={'kind': 'running'}), 'idle')
    assert_derived(bundle(input_errors=['session outcome unavailable']), 'idle')


def test_derived_unknown_only_for_unreadable_row_sources():
    for error in ('user-steps requests unavailable', 'rentals unavailable', 'process inventory unknown: missing identity'):
        assert_derived(bundle(input_errors=[error]), 'unknown')
        assert_derived(bundle(task(human_request=human(), wait={'wait_state': 'human'}), input_errors=[error]), 'unknown')
    # An unreadable source cannot override a valid canonical status.
    m = model.reduce_panel(bundle(outcome_json=canonical_outcome(), input_errors=['rentals unavailable']), now=NOW)
    assert m['header']['source'] == 'canonical' and m['header']['state'] == 'done'


def test_remote_retiring_delivery_is_not_terminal_even_after_card_departure():
    for departed in (False, True):
        for note in ('Delivering…', 'Delivery to remote not confirmed (120 min)'):
            r = request(state='cancelled')
            r.update(resolution={'operationId': 'retiring', 'choiceId': 'card-0', 'revision': 3},
                     delivery={'state': 'delivering', 'text': note})
            b = bundle(*([] if departed else [task(human_request=human(), wait={'wait_state': 'human'})]), requests=[r])
            result = model.reduce_panel(b, now=NOW)
            assert result == model.reduce_panel(b, now=NOW)
            rows = result['sections']['waiting']
            assert len(rows) == 1 and rows[0]['decision']['text'] == note
            assert rows[0]['detail'] == ''
            assert not result['sections']['needs_you']
            assert not any(a['kind'] == 'decide' for a in rows[0]['actions'])
            assert 'request cancelled' not in json.dumps(result)
            r.pop('delivery')  # terminal cancellation clears the pending decision line
            settled = model.reduce_panel(b, now=NOW)
            assert settled['revision'] != result['revision']
            assert not any(row.get('decision') for row in settled['sections']['waiting'])


def retiring_owner_cases():
    """Owner evidence stays separate from a retiring answer's delivery status."""
    return {
        'running': observed(task(status='running', run_model='m', started_at=NOW - 600)),
        'stuck': task(status='scheduled', wait={'wait_state': 'stalled'}),
        'unknown': task(status='running'),
        'stale': task(status='running', stale=True),
    }


def test_retiring_delivery_preserves_each_owner_rows_own_status():
    for name, row in retiring_owner_cases().items():
        baseline = model.reduce_panel(bundle(row), now=NOW, projector=project)
        section, plain = next((s, rows[0]) for s, rows in baseline['sections'].items() if rows)
        assert plain['detail'], name
        for note in ('Delivering…', 'Delivery to remote not confirmed (120 min)'):
            r = request(state='cancelled')
            r.update(resolution={'operationId': 'retiring', 'choiceId': 'card-0', 'revision': 3},
                     delivery={'state': 'delivering', 'text': note})
            result = model.reduce_panel(bundle(row, requests=[r]), now=NOW, projector=project)
            owner = result['sections'][section][0]
            assert owner['detail'] == plain['detail'], name
            assert owner['details'] == plain['details'], name
            assert owner['decision']['text'] == note
            assert result['header']['counts'] == baseline['header']['counts']
            assert not result['sections']['needs_you']
            assert not any(a['kind'] == 'decide' for a in owner['actions'])
            assert 'request cancelled' not in json.dumps(result)


def test_block_loop_triage_live_ask_but_no_block_kind_guessing():
    m = model.reduce_panel(bundle(task(status='triage', block_recurrences=4, human_request=human(), block_kind='needs_input')), now=NOW)
    assert len(m['sections']['needs_you']) == 1
    m = model.reduce_panel(bundle(task(block_kind='needs_input')), now=NOW)
    assert not m['sections']['needs_you']
    row = m['sections']['waiting'][0]
    assert row['detail'] == 'blocked · no reason published'
    assert not row['detail'].endswith('board status')
    assert row['details'] == ['Board status; no live run evidence for this card.']  # provenance lives in Details only


def test_stale_human_block_kind_external_running_regression():
    row = task(status='scheduled', block_kind='needs_input', human_request=None,
               wait={'wait_state': 'external_running', 'waiting_on': 'external paired device log'})
    m = model.reduce_panel(bundle(row), now=NOW)
    assert not m['sections']['needs_you']
    assert 'external paired device log' in m['sections']['waiting'][0]['detail']


def test_stuck_uses_progress_not_creation_and_evidence_precedes_stuck():
    row = observed(task(status='running', activity_at=NOW - 8000, created_at=NOW - 100000))
    m = model.reduce_panel(bundle(row), now=NOW, projector=project)
    assert m['sections']['stuck'][0]['reason'] == 'no-progress'
    row.pop('activity_at')
    assert len(model.reduce_panel(bundle(row), now=NOW, projector=project)['sections']['running']) == 1
    row.pop('execution_observations')
    assert len(model.reduce_panel(bundle(row), now=NOW)['sections']['waiting']) == 1


def test_stalled_precedes_fresh_parked_unknown_execution():
    m = model.reduce_panel(bundle(task(status='scheduled', wait={'wait_state': 'stalled', 'waiting_on': 'a missing producer'})), now=NOW)
    assert m['sections']['stuck'][0]['reason'] == 'stalled'


def test_running_subagents_background_processes_rentals_count_as_rows():
    sub = observed({'id': 'child', 'title': 'Research'}, 'subagent')
    proc = observed({'id': 'proc', 'command': 'run tests'}, 'process')
    m = model.reduce_panel(bundle(subagents=[sub], processes=[proc], rentals=[{'id': 'box', 'name': 'Machine'}]), now=NOW, projector=project)
    assert m['header']['counts']['running'] == 3
    assert [r['kind'] for r in m['sections']['running']] == ['subagent', 'process', 'machine']
    proc.pop('execution_observations')
    m = model.reduce_panel(bundle(processes=[proc]), now=NOW)
    assert not m['sections']['running'] and m['sections']['waiting']


def test_retired_requests_dont_revive_but_blocked_owner_remains_visible():
    m = model.reduce_panel(bundle(task(human_request=human(), wait={'wait_state': 'human'}), requests=[request('withdrawn')]), now=NOW)
    assert not m['sections']['needs_you']
    assert m['sections']['waiting'][0]['detail'] == 'blocked · request withdrawn'


def test_terminal_stale_does_not_reopen_and_done_dedup_is_owner_qualified():
    one = task(status='done', completed_at=NOW - 3)
    two = {**one, 'gateway': 'other'}
    m = model.reduce_panel(bundle({**one, 'stale': True}, done=[one, one, two]), now=NOW)
    assert not m['sections']['waiting']
    assert m['header']['counts']['done'] == 2


def test_queued_mapped_ready_and_blocked_upstream_waiting():
    m = model.reduce_panel(bundle(task(status='todo'), task('t_def456', status='ready', blocked_upstream={'id': 't_parent', 'status': 'blocked'})), now=NOW)
    assert m['sections']['ready'][0]['queued'] is True
    assert 'blocked parent' in m['sections']['waiting'][0]['detail']


def test_timed_wait_pt_and_overdue():
    row = task(status='scheduled', wait={'wait_state': 'waiting', 'waiting_on': 'the clock until 2026-11-21 00:00 UTC', 'waiting_since': NOW - 2000})
    m = model.reduce_panel(bundle(row), now=NOW)
    assert 'PST' in m['sections']['waiting'][0]['detail']
    assert 'overdue' in m['sections']['waiting'][0]['detail']


def test_pure_deterministic_json_revision():
    b = bundle(task(human_request=human(), wait={'wait_state': 'human'}))
    original = copy.deepcopy(b)
    a = model.reduce_panel(b, now=NOW)
    assert a == model.reduce_panel(b, now=NOW)
    assert b == original
    assert json.loads(json.dumps(a)) == a
    assert len(a['revision']) == 64
    assert a['header']['counts'] == {k: len(v) for k, v in a['sections'].items()}


def test_cross_owner_alias_is_never_attached_by_bare_card_id():
    foreign = request()
    foreign['scope']['sourceRefs'][0]['gatewayId'] = 'OTHER'
    m = model.reduce_panel(bundle(task(human_request=human(), wait={'wait_state': 'human'}), requests=[foreign]), now=NOW)
    card = next(r for r in m['sections']['needs_you'] if r['source'] == 'card')
    assert card['request_id'] is None
    assert all(a['kind'] != 'decide' for a in card['actions'])
    assert not model._matches({'id': 't_abc123'}, foreign['scope']['sourceRefs'][0])


def test_own_install_alias_attaches_request_to_card_but_foreign_install_does_not():
    own = request()
    own['scope']['sourceRefs'][0]['gatewayId'] = 'install:abc'
    b = bundle(task(human_request=human(), wait={'wait_state': 'human'}), requests=[own])
    m = model.reduce_panel({**b, 'gateway': 'remote', 'gateway_aliases': ['install:abc']}, now=NOW)
    assert len(m['sections']['needs_you']) == 1
    assert m['sections']['needs_you'][0]['request_id'] == own['requestId']
    m = model.reduce_panel({**b, 'gateway': 'remote', 'gateway_aliases': ['install:zzz']}, now=NOW)
    assert next(r for r in m['sections']['needs_you'] if r['source'] == 'card')['request_id'] is None


def test_boolean_recommendation_and_explanation_in_native_question():
    r = {'requestId': 'q', 'revision': 1, 'state': 'open', 'title': 'Choose a route',
         'choices': [{'id': 'fast', 'label': 'Fast', 'recommended': True, 'explain': 'No extra cost'}, {'id': 'slow', 'label': 'Slow'}]}
    a = model.reduce_panel(bundle(requests=[r]), now=NOW)['sections']['needs_you'][0]
    assert a['options'][0]['recommended'] is True
    assert a['options'][0]['detail'] == 'No extra cost'
    assert a['options'][0]['choice_id'] == 'fast'


def test_planless_approval_shows_no_choice_and_says_why():
    # user-steps turns an approve step without a plan into needs_context with only a revise choice.
    r = {'requestId': 'q', 'revision': 1, 'state': 'needs_context', 'kind': 'approval',
         'title': 'Approve building the filter as planned',
         'scope': {'markdown': 'Approve building the filter as planned', 'complete': False},
         'choices': [{'id': 'revise', 'label': 'Request changes', 'effect': 'revise'}]}
    m = model.reduce_panel(bundle(requests=[r]), now=NOW)
    a = m['sections']['needs_you'][0]
    assert a['options'] == [] and a['recommend_letter'] == ''
    assert 'posted no plan' in a['situation']
    assert a['request_id'] == 'q'
    # An open request with the same choice still renders it (only needs_context is special).
    a = model.reduce_panel(bundle(requests=[{**r, 'state': 'open'}]), now=NOW)['sections']['needs_you'][0]
    assert [o['name'] for o in a['options']] == ['Request changes']


def test_running_row_shows_where_model_and_run_time():
    row = task(status='running', run_machine='worker-a4', run_model='claude-opus-5-5', current_run_started_at=NOW - 750,
               activity_at=NOW - 30, activity_kind='tool', assignee='default')
    m = model.reduce_panel(bundle(observed(row)), now=NOW, projector=project)
    r = m['sections']['running'][0]
    assert r['detail'] == 'worker-a4 · opus-5-5 · 12m'
    assert 'model claude-opus-5-5' in r['details'][0] and 'on worker-a4' in r['details'][0]
    assert r['details'][1] == 'started 12 min ago · last event tool just now'
    assert r['quiet'] is False


def test_run_meta_pool_class_model_and_quiet():
    row = {'id': 't_x', 'run_machine': 'worker-pool', 'run_model': 'class:deep', 'started_at': NOW - 7200}
    meta = model.run_meta(row, 'running', NOW - 1500, NOW)
    assert meta['detail'] == 'pool · deep · quiet 25m'
    assert meta['quiet'] is True
    assert model.run_meta({'id': 't_y'}, 'review', 0, NOW)['detail'] == 'in review'
