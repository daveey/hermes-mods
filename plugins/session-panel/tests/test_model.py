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
    m = model.reduce_panel(bundle(task(status='running'), outcome_json='{"kind":"needs_user"}'), now=NOW)
    assert m['header']['state'] == 'needs_user'
    assert not m['sections']['needs_you']
    assert model.reduce_panel(bundle(), now=NOW)['header']['state'] == 'unknown'


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
