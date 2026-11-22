"""Regression tests for the 19-session status-panel audit (card t_01a2001662ee259d): one block per defect."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('panel_model_audit_test', ROOT / 'model.py')
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)
NOW = 1795281000


def bundle(*tasks, **extra):
    return {'computed_at': NOW, 'tasks': list(tasks), 'done': [], 'cards': {}, **extra}


def task(tid='t_abc123', status='running', **kw):
    return {'id': tid, 'gateway': 'remote', 'profile': 'default', 'board': 'default', 'title': 'Test card', 'status': status, **kw}


def v1(row, observed_at, run_id=7, state='running'):
    """A valid core v1 execution observation for a card."""
    return {'version': 1, 'identity': {'kind': 'card', 'work_id': row['id'], 'gateway': row['gateway'], 'profile': row['profile'],
                                       'board': row['board'], 'run_id': run_id},
            'owner': {'gateway': row['gateway'], 'profile': row['profile']}, 'state': state, 'source': 'kanban.card',
            'source_revision': 'tasks/x/last_heartbeat_at/1', 'evidence_refs': ['tasks/x/last_heartbeat_at/1'],
            'observed_at': observed_at, 'valid_until': observed_at + 180, 'last_activity': None, 'last_progress': None,
            'wait': None}


# ---- 2. clock skew ---------------------------------------------------------------------------------------------

def test_small_worker_clock_lead_is_lifted_not_demoted():
    row = task()
    seen = []

    def project(records, *, now):
        seen.append(now)
        return {'state': 'running', 'sourced': True}
    row['execution_observations'] = [v1(row, NOW + 54)]
    m = model.reduce_panel(bundle(row), now=NOW, projector=project)
    assert seen == [NOW + 54]
    assert len(m['sections']['running']) == 1 and not m['sections']['waiting']


def test_large_clock_lead_still_reaches_core_unchanged():
    row = task()
    seen = []
    row['execution_observations'] = [v1(row, NOW + model.SKEW_S + 1)]
    model.reduce_panel(bundle(row), now=NOW, projector=lambda r, *, now: seen.append(now) or {'state': 'unknown', 'unknown_reason': 'clock_inconsistent'})
    assert seen == [NOW]


def test_core_projector_keeps_running_card_in_progress_with_54s_skew():
    pytest.importorskip('hermes_cli.execution_observations')
    row = task(current_run_started_at=NOW - 600)
    row['execution_observations'] = [v1(row, NOW + 54)]
    m = model.reduce_panel(bundle(row), now=NOW)
    assert [r['id'] for r in m['sections']['running']] == ['t_abc123'], m['sections']
    assert 'clock inconsistent' not in str(m['sections'])
    row['execution_observations'] = [v1(row, NOW + 600)]  # genuinely inconsistent: still unknown
    assert 'clock inconsistent' in model.reduce_panel(bundle(row), now=NOW)['sections']['waiting'][0]['detail']


# ---- 3. replica rows have no run identity ----------------------------------------------------------------------

def placeholder(row, **kw):
    return {'version': 1, 'identity': {'kind': 'card', 'work_id': row['id'], 'gateway': row['gateway'], 'profile': row['profile'], 'board': row['board']},
            'owner': {'gateway': row['gateway'], 'profile': row['profile']}, 'state': 'unknown', 'source': 'kanban.card',
            'source_revision': None, 'evidence_refs': [], 'observed_at': None, 'valid_until': None,
            'last_known_state': kw.get('last_known'), 'unknown_reason': 'missing_qualified_identity'}


def remote_running(**kw):
    row = task('t_01a2000b4395a149', 'running', gateway='worker-a4', remote=True, source='origin-obligation', run_machine='worker-a4',
               run_model='gpt-6.1-sol', started_at=NOW - 1080, synced_at=NOW - 20, **kw)
    row['execution_observations'] = [placeholder(row, last_known='running')]
    return row


def test_remote_running_card_without_run_identity_shows_as_running_not_unknown():
    pytest.importorskip('hermes_cli.execution_observations')
    m = model.reduce_panel(bundle(remote_running()), now=NOW)
    assert not m['sections']['waiting'], m['sections']['waiting']
    r = m['sections']['running'][0]
    assert r['detail'] == 'worker-a4 · gpt-6.1-sol · 18m'
    assert r['evidence_state'] == 'reported'
    assert any('Reported by worker-a4' in d for d in r['details'])
    assert 'invalid requested identity' not in str(m)


def test_reported_running_needs_a_fresh_read_and_a_remote_row():
    row = remote_running()
    assert 'status unknown' in model.reduce_panel({**bundle(row), 'computed_at': NOW - 500}, now=NOW)['sections']['waiting'][0]['detail']
    stale = {**row, 'synced_at': NOW - 4000}
    assert 'status unknown' in model.reduce_panel(bundle(stale), now=NOW)['sections']['waiting'][0]['detail']
    local = {k: v for k, v in row.items() if k not in ('remote', 'source')}
    assert 'status unknown' in model.reduce_panel(bundle(local), now=NOW)['sections']['waiting'][0]['detail']


@pytest.mark.parametrize('synced_at, allowed', [(None, False), (NOW + 5000, False), (NOW + 1, False),
                                               (NOW - 300, True), (NOW - 301, False), (NOW, True),
                                               ('bad', False), (str(NOW), False), (True, False),
                                               (float('nan'), False), (float('inf'), False)])
def test_reported_running_requires_numeric_bounded_sync(synced_at, allowed):
    row = remote_running()
    if synced_at is None:
        row.pop('synced_at')
    else:
        row['synced_at'] = synced_at
    m = model.reduce_panel(bundle(row), now=NOW, projector=lambda r, **kw: {'state': 'unknown', 'unknown_reason': 'no_run_identity'})
    assert bool(m['sections']['running']) is allowed
    if not allowed:
        assert 'status unknown' in m['sections']['waiting'][0]['detail']


@pytest.mark.parametrize('observed_at, state, reason', [(NOW - 181, 'running', 'source expired'),
                                                      (NOW, 'completed', 'unsupported state')])
def test_reported_running_never_masks_real_expired_or_nonrunning_observation(observed_at, state, reason):
    pytest.importorskip('hermes_cli.execution_observations')
    row = remote_running()
    row['execution_observations'] = [v1(row, observed_at, state=state)]
    m = model.reduce_panel(bundle(row), now=NOW)
    assert not m['sections']['running']
    assert f'status unknown ({reason})' in m['sections']['waiting'][0]['detail']


def test_parked_remote_rows_show_their_true_state_without_a_provenance_tail():
    pytest.importorskip('hermes_cli.execution_observations')
    for status, shown in (('scheduled', 'scheduled · its owner has not published what wakes it'), ('triage', 'in triage · waiting to be specified and promoted'),
                          ('todo', 'not started')):
        row = task(f't_{status}', status, gateway='worker-a1', remote=True, source='replica', synced_at=NOW - 5)
        row['execution_observations'] = [placeholder(row)]
        m = model.reduce_panel(bundle(row), now=NOW)
        out = [r for s in ('waiting', 'ready') for r in m['sections'][s]][0]
        assert out['detail'] == shown and 'invalid' not in out['detail'] and 'board status' not in out['detail']
        assert any('Reported by worker-a1' in d for d in out['details'])


# ---- 4. waiting text -------------------------------------------------------------------------------------------

def waiting(on, **kw):
    return model.reduce_panel(bundle(task(status='scheduled', wait={'wait_state': 'external_running', 'waiting_on': on, 'waiting_since': NOW - 120}, **kw)), now=NOW)['sections']['waiting'][0]


def test_ci_wait_is_short_with_pr_and_a_link_action():
    r = waiting('ci https://github.com/softmax-universe/hermes/actions/runs/44951903463', pr_url='https://github.com/softmax-universe/hermes/pull/3881')
    assert r['detail'] == 'waiting on CI · PR #3881 · 2m'
    assert [a['url'] for a in r['actions'] if a['kind'] == 'open_url'] == ['https://github.com/softmax-universe/hermes/actions/runs/44951903463']
    assert 'Waiting on: ci https://github.com/softmax-universe/hermes/actions/runs/44951903463' in r['details']
    assert 'http' not in r['detail']


def test_ci_wait_without_a_pr_or_url():
    r = waiting('ci local:worker-a4:2658784')
    assert r['detail'] == 'waiting on CI · 2m' and not [a for a in r['actions'] if a['kind'] == 'open_url']


def test_deploy_wait_hides_hashes():
    r = waiting('deploy hermes-hub scheduled hub-core-update / mods-pull for core106bf5b0abcdef12…and mods10902a1a99aa…')
    assert r['detail'] == 'waiting on hub deploy · 2m'
    assert any('106bf5b0' in d for d in r['details'])


def test_generic_wait_drops_urls_and_hashes_and_stays_short():
    r = waiting('release by https://example.com/x/y for 0123abcd4567ef89 and then some very long explanation ' * 3)
    assert 'http' not in r['detail'] and '0123abcd' not in r['detail'] and len(r['detail']) < 120


@pytest.mark.parametrize('on', [
    'external local:worker-a1:123456',
    'external service example-agent.service PID 12345',
    'external service PID12345',
    'external card t_0123456789abcdef on worker-a1',
])
def test_external_wait_uses_category_not_executor_identifiers(on):
    r = waiting(on)
    assert r['detail'] == 'waiting on external work · 2m'
    assert f'Waiting on: {on}' in r['details']


@pytest.mark.parametrize('on, expected', [
    ('card t_0123456789abcdef on remote', 'waiting on parent card'),
    ('producer card t_0123456789abcdef (scheduled), then its release', 'waiting on parent card'),
    ('an agent to release it: authority t_0123456789abcdef is archived', 'waiting on agent work'),
])
def test_generic_dependency_identifiers_stay_in_details(on, expected):
    r = waiting(on)
    assert r['detail'] == expected + ' · 2m'
    assert f'Waiting on: {on}' in r['details']


@pytest.mark.parametrize('source, owner, gateway', [('live', None, 'hub'), ('replica', 'remote', 'remote')])
def test_wait_names_hydrated_parent_and_opens_exact_owner(source, owner, gateway):
    cid = 't_0123456789abcdef'
    row = task(status='scheduled', wait={'wait_state': 'waiting', 'waiting_on': f'producer card {cid} (scheduled), then its release', 'waiting_since': NOW - 120})
    b = bundle(row, cards={cid: {'board': 'other', 'detail': {'source': source, 'owner': owner, 'task': {'id': cid, 'title': 'Publish the release'}}}})
    b.update(gateway='hub', profile='default')
    r = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert r['detail'] == 'waiting on: Publish the release · 2m'
    action = next(a for a in r['actions'] if a['label'] == 'Open parent card')
    assert action['target'] == {'id': cid, 'gateway': gateway, 'profile': 'default', 'board': 'other'}
    assert any(cid in d for d in r['details'])
    b['cards'][cid]['detail'].update(source='replica', owner=None)
    r = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert not any(a['label'] == 'Open parent card' for a in r['actions'])


@pytest.mark.parametrize('on', ['the clock until 2026-11-21 00:00 UTC', 'ci https://example.com/run', 'deploy release', 'an agent to release it: authority t_0123456789abcdef is archived'])
def test_parent_metadata_does_not_replace_unrelated_waits(on):
    cid = 't_0123456789abcdef'
    row = task(status='scheduled', upstream={'id': cid, 'title': 'Other work', 'gateway': 'remote', 'profile': 'default'}, wait={'wait_state': 'waiting', 'waiting_on': on})
    r = model.reduce_panel(bundle(row), now=NOW)['sections']['waiting'][0]
    assert r['detail'] == model.wait_phrase(row, NOW)
    assert not any(a['label'] == 'Open parent card' for a in r['actions'])


def test_own_wait_and_unhydrated_primary_never_link_to_self_or_secondary():
    cid, other = 't_0123456789abcdef', 't_abcdef0123456789'
    cards = {cid: {'detail': {'source': 'live', 'task': {'id': cid, 'title': 'Own work'}}}}
    for own, on in ((cid, f'card {cid}'), ('t_aaaaaaaa', f'external comments on {other} from source owner {cid}')):
        row = task(own, status='scheduled', wait={'wait_state': 'waiting', 'waiting_on': on})
        b = bundle(row, cards=cards)
        b.update(gateway='hub', profile='default')
        r = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
        assert 'Own work' not in r['detail']
        assert not any(a['label'] == 'Open parent card' for a in r['actions'])


def test_parent_wait_keeps_identity_in_details_not_status():
    upstream = {'id': 't_0123456789abcdef', 'status': 'blocked'}
    r = model.reduce_panel(bundle(task(status='todo', blocked_upstream=upstream)), now=NOW)['sections']['waiting'][0]
    assert r['detail'] == 'waiting on blocked parent card'
    assert any(upstream['id'] in d for d in r['details'])
    upstream['contained'] = {'authority': {'id': 't_release'}}
    r = model.reduce_panel(bundle(task(status='todo', blocked_upstream=upstream)), now=NOW)['sections']['waiting'][0]
    assert r['detail'] == 'waiting on contained parent card'
    assert any('t_release' in d for d in r['details'])
    r = model.reduce_panel(bundle(task(status='todo', upstream={'id': 't_parent', 'status': 'running'})), now=NOW)['sections']['waiting'][0]
    assert r['detail'] == 'waiting on parent card'
    assert 'Parent card: t_parent (running)' in r['details']


def test_no_row_ends_with_a_board_status_provenance_tail():
    for status in ('blocked', 'scheduled', 'triage', 'todo', 'ready', 'review'):
        for remote in (False, True):
            row = task(status=status, remote=remote, **({'source': 'replica'} if remote else {}))
            m = model.reduce_panel(bundle(row), now=NOW)
            for s in m['sections'].values():
                for r in s:
                    assert not r['detail'].endswith('board status'), r['detail']


# ---- 6. done rows ----------------------------------------------------------------------------------------------

def test_done_rows_carry_no_redundant_done_label():
    done = task(status='done', completed_at=NOW - 3)
    m = model.reduce_panel(bundle(done=[done, {**done, 'id': 't_two', 'result': 'Merged as abc'}]), now=NOW)
    assert [r['detail'] for r in m['sections']['done']] == ['', 'Merged as abc'] or sorted(r['detail'] for r in m['sections']['done']) == ['', 'Merged as abc']


# ---- 7. run model fallback -------------------------------------------------------------------------------------

def test_running_row_without_run_model_falls_back_to_the_runs_route_event():
    row = task('t_01a20009c01143a5', gateway='remote', run_machine='worker-a2', current_run_id=9, started_at=NOW - 240)
    row['execution_observations'] = [{'identity': {'kind': 'card', 'work_id': row['id'], 'gateway': 'remote', 'profile': 'default', 'board': 'default'}, 'state': 'running'}]
    events = [{'id': 1, 'kind': 'model_tier', 'run_id': 8, 'payload': {'model': 'old-model'}},
              {'id': 2, 'kind': 'model_tier', 'run_id': 9, 'payload': {'model': 'tier-model'}},
              {'id': 3, 'kind': 'run_route', 'run_id': 9, 'payload': {'model': 'gpt-6-astra'}}]
    b = bundle(row, cards={row['id']: {'detail': {'events': events}}})
    r = model.reduce_panel(b, now=NOW, projector=lambda recs, *, now: {'state': 'running', 'sourced': True})['sections']['running'][0]
    assert r['detail'] == 'worker-a2 · gpt-6-astra · 4m'
    events.pop()  # no run_route: the tier event of the SAME run, never another run's
    assert model.reduce_panel(b, now=NOW, projector=lambda recs, *, now: {'state': 'running', 'sourced': True})['sections']['running'][0]['detail'] == 'worker-a2 · tier-model · 4m'
    assert model.route_model({'current_run_id': 11}, {'events': events}) == ''


# ---- 8. title glyphs -------------------------------------------------------------------------------------------

def test_emoji_glyphs_are_stripped_from_titles_but_text_symbols_stay():
    row = task(status='todo', title='Hermes bug: cross_session: \u26d4 Session local-<n> is a Kanban worker \U0001f6a8 \u2705 ok \u2192 \u26a0 x')
    t = model.reduce_panel(bundle(row), now=NOW)['sections']['ready'][0]['title']
    assert t == 'Hermes bug: cross_session: Session local-<n> is a Kanban worker ok \u2192 \u26a0 x'
    assert model.clean('\u26d4\ufe0f') == ''


# ---- 1. answered asks ------------------------------------------------------------------------------------------

def answered(**kw):
    return {'requestId': 'req-9', 'revision': 1, 'state': 'open', 'title': 'Reload the desktop window (Cmd+R) once sync reaches your Mac',
            'owner': {'gatewayId': 'remote', 'profile': 'default', 'storedSessionId': 'chat'},
            'choices': [{'id': 'answer-done', 'label': 'Done'}], 'scope': {'markdown': 'Reload.', 'sourceRefs': []},
            'resolution': {'choiceId': 'answer-done', 'operationId': 'op-1', 'recordedAt': '2026-11-21T21:35:05+00:00', 'revision': 1}, **kw}


def test_answered_ask_is_one_status_line_not_an_ask_card():
    at = answered()['resolution']['recordedAt']
    now = model.datetime.fromisoformat(at).timestamp() + 60
    m = model.reduce_panel(bundle(), now=now, requests=[answered()])
    assert not m['sections']['needs_you']
    [row] = m['sections']['waiting']
    assert row['kind'] == 'decision' and row['decision'] == {'request_id': 'req-9', 'text': 'Done · delivers when this turn ends'}
    assert row['actions'] == []
    # confirmed delivery (the request resolved): the line is gone
    assert not model.reduce_panel(bundle(), now=now, requests=[answered(state='resolved')])['sections']['waiting']


def test_answered_ask_attaches_to_its_card_and_keeps_it_out_of_needs_you():
    ref = {'type': 'kanban', 'gatewayId': 'remote', 'profile': 'default', 'board': 'default', 'id': 't_abc123'}
    req = answered(scope={'markdown': 'x', 'sourceRefs': [ref]})
    hr = {'action': 'Pick', 'why_human': 'Tried: x. Only you can: y.', 'interaction': 'approval', 'evidence': 'e'}
    now = model.datetime.fromisoformat(req['resolution']['recordedAt']).timestamp() + 30
    m = model.reduce_panel(bundle(task(status='blocked', human_request=hr, wait={'wait_state': 'human'}), requests=[req]), now=now)
    assert not m['sections']['needs_you']
    [row] = m['sections']['waiting']
    assert row['id'] == 't_abc123' and row['decision']['text'] == 'Done · delivers when this turn ends' and row['detail'] == ''
    assert m['header']['label'] == 'decision recorded'
    # the card runs again: the status line follows it
    m = model.reduce_panel(bundle(task(), requests=[req]), now=now, projector=lambda r, *, now: {'state': 'running', 'sourced': True},)
    assert not [r for r in m['sections']['waiting'] if r['kind'] == 'decision']


def live_ask_with_pending_decision():
    ref = {'type': 'kanban', 'gatewayId': 'remote', 'profile': 'default', 'board': 'default', 'id': 't_abc123'}
    old = answered(scope={'markdown': 'Older ask', 'sourceRefs': [ref]})
    live = answered(requestId='req-live', resolution=None, title='A new decision', scope={'markdown': 'New ask', 'sourceRefs': [ref]})
    hr = {'action': 'Pick', 'why_human': 'Tried: x. Only you can: y.', 'interaction': 'approval', 'evidence': 'e'}
    return model.reduce_panel(bundle(task(status='blocked', human_request=hr, wait={'wait_state': 'human'}), requests=[old, live]), now=NOW)


def test_live_ask_keeps_older_answered_decision_on_same_card():
    m = live_ask_with_pending_decision()
    [row] = m['sections']['needs_you']
    assert row['request_id'] == 'req-live'
    assert row['decision']['request_id'] == 'req-9'
    assert row['decision']['text']
    assert not m['sections']['waiting'], 'do not duplicate the owner card'


def test_long_undelivered_decision_says_so():
    req = answered()
    now = model.datetime.fromisoformat(req['resolution']['recordedAt']).timestamp() + 3600
    assert model.reduce_panel(bundle(), now=now, requests=[req])['sections']['waiting'][0]['decision']['text'] == 'Done · not delivered yet, recorded 1 h ago'


def test_header_label_never_says_unresolved_for_working_or_answered_sessions():
    run = task()
    run['execution_observations'] = [{'identity': {'kind': 'card', 'work_id': run['id'], 'gateway': 'remote', 'profile': 'default', 'board': 'default'}, 'state': 'running'}]
    m = model.reduce_panel(bundle(run, outcome_json='{"kind":"unresolved","reason":"interrupted"}'), now=NOW, projector=lambda r, *, now: {'state': 'running', 'sourced': True})
    assert m['header']['state'] == 'unresolved' and m['header']['label'] == 'working'  # canonical state kept, label human
    assert model.reduce_panel(bundle(outcome_json='{"kind":"unresolved"}'), now=NOW)['header']['label'] == 'interrupted'
    now = model.datetime.fromisoformat(answered()['resolution']['recordedAt']).timestamp() + 5
    m = model.reduce_panel(bundle(outcome_json='{"kind":"unresolved"}'), now=now, requests=[answered()])
    assert m['header']['label'] == 'decision recorded'
