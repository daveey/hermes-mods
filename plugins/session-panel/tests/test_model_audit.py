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
    for status, shown in (('scheduled', 'scheduled · its owner has not published what wakes it'), ('triage', "waiting on this chat's agent to decide: review the held scope and choose the next step · in triage · entry time unavailable"),
                          ('todo', 'not started')):
        row = task(f't_{status}', status, gateway='worker-a1', remote=True, source='replica', synced_at=NOW - 5)
        row['execution_observations'] = [placeholder(row)]
        m = model.reduce_panel(bundle(row), now=NOW)
        out = [r for s in ('waiting', 'ready') for r in m['sections'][s]][0]
        assert out['detail'] == shown and 'invalid' not in out['detail'] and 'board status' not in out['detail']
        assert any('Reported by worker-a1' in d for d in out['details'])


@pytest.mark.parametrize('status', ['scheduled', 'blocked', 'triage', 'review', 'ready', 'todo'])
@pytest.mark.parametrize('age', [30, 60, 120])
def test_parked_board_status_survives_old_bundle_with_explicit_age(status, age):
    row = task(status=status)
    row['execution_observations'] = [placeholder(row)]
    project = lambda r, **kw: {'state': 'unknown', 'unknown_reason': 'no_run_identity'}
    result = model.reduce_panel(bundle(row, computed_at=NOW - age), now=NOW, projector=project)
    out = next(r for s in result['sections'].values() for r in s)
    assert out['parked'] is True
    assert 'status unknown' not in out['detail']
    assert ('panel data' in out['detail']) is (age > 60)
    assert out['evidence_state'] == 'unknown'
    if age > 60:
        assert 'as of ' in out['detail'] and '2m old' in out['detail']
        assert 'wait_kind' not in out  # historical producer, not a live delivery
        wire = model.reduce_panel(bundle(row, computed_at=NOW - age), now=NOW, projector=project, wire=True)
        later = model.reduce_panel(bundle(row, computed_at=NOW - age), now=NOW + 10, projector=project, wire=True)
        assert wire['revision'] == later['revision']


@pytest.mark.parametrize('status, reason, read_at', [
    ('running', 'no_run_identity', NOW - 120),
    ('scheduled', 'source_expired', NOW - 120),
    ('scheduled', 'clock_inconsistent', NOW - 120),
    ('scheduled', 'ambiguous_execution_identity', NOW - 120),
    ('unknown', 'no_run_identity', NOW - 120),
    ('scheduled', 'no_run_identity', NOW + 120),
])
def test_old_parked_status_does_not_hide_contradictions(status, reason, read_at):
    row = task(status=status, execution_observations=[placeholder(task(status=status))])
    result = model.reduce_panel(bundle(row, computed_at=read_at), now=NOW,
                                projector=lambda r, **kw: {'state': 'unknown', 'unknown_reason': reason})
    out = result['sections']['waiting'][0]
    assert 'status unknown' in out['detail'] and not out.get('parked')


# ---- 4. waiting text -------------------------------------------------------------------------------------------

def waiting(on, **kw):
    return model.reduce_panel(bundle(task(status='scheduled', wait={'wait_state': 'external_running', 'waiting_on': on, 'waiting_since': NOW - 120}, **kw)), now=NOW)['sections']['waiting'][0]


def test_ci_wait_is_short_with_pr_and_a_link_action():
    r = waiting('ci https://github.com/softmax-universe/hermes/actions/runs/44951903463', pr_url='https://github.com/softmax-universe/hermes/pull/3881')
    assert r['detail'] == 'waiting on CI · PR #3881 · 2m on remote'
    assert [a['url'] for a in r['actions'] if a['kind'] == 'open_url'] == ['https://github.com/softmax-universe/hermes/actions/runs/44951903463']
    assert [a.get('inline') for a in r['actions'] if a['kind'] == 'open_url'] == ['CI']
    assert 'Waiting on: ci https://github.com/softmax-universe/hermes/actions/runs/44951903463' in r['details']
    assert 'http' not in r['detail']


def test_ci_wait_without_a_pr_or_url():
    r = waiting('ci local:worker-a4:2658784')
    assert r['detail'] == 'waiting on CI · 2m on remote' and not [a for a in r['actions'] if a['kind'] == 'open_url']
    assert r['wait_kind'] == 'ci'


def test_delivery_wait_kind_only_on_ci_and_deploy_rows():
    assert waiting('deploy hermes-hub mods-pull')['wait_kind'] == 'deploy'
    assert waiting('ci run 1')['wait_kind'] == 'ci'
    assert 'wait_kind' not in waiting('the result of a review')
    assert 'wait_kind' not in waiting('cinema tickets')


def test_header_and_pip_go_light_blue_only_when_every_wait_is_delivery():
    def header(*ons):
        tasks = [task(f't_{i}', status='scheduled', wait={'wait_state': 'external_running', 'waiting_on': on, 'waiting_since': NOW - 120})
                 for i, on in enumerate(ons)]
        return model.reduce_panel(bundle(*tasks), now=NOW)['header']
    h = header('deploy hermes-hub mods-pull')
    assert h['wait_kind'] == 'deploy' and [p.get('wait_kind') for p in h['pips'] if p['section'] == 'waiting'] == ['deploy']
    mixed = header('ci run 1', 'the result of a review')
    assert 'wait_kind' not in mixed and all('wait_kind' not in p for p in mixed['pips'])


def test_deploy_wait_hides_hashes():
    r = waiting('deploy hermes-hub scheduled hub-core-update / mods-pull for core106bf5b0abcdef12…and mods10902a1a99aa…')
    assert r['detail'] == 'waiting on hub deploy · 2m on remote'
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
    assert r['detail'] == 'waiting on external work · 2m on remote'
    assert f'Waiting on: {on}' in r['details']


@pytest.mark.parametrize('on, expected', [
    ('card t_0123456789abcdef on remote', 'waiting on parent card'),
    ('producer card t_0123456789abcdef (scheduled), then its release', 'waiting on parent card'),
    ('an agent to release it: authority t_0123456789abcdef is archived', 'waiting on agent work'),
])
def test_generic_dependency_identifiers_stay_in_details(on, expected):
    r = waiting(on)
    assert r['detail'] == expected + ' · 2m on remote'
    assert f'Waiting on: {on}' in r['details']


def test_wait_chain_shows_deepest_parent_location_and_reason():
    parent, leaf = 't_1111111111111111', 't_2222222222222222'
    row = task(status='todo', upstream={'id': parent, 'title': 'Intermediate', 'gateway': 'remote', 'profile': 'default'})
    b = bundle(row, cards={
        parent: {'detail': {'owner': 'remote', 'task': {'id': parent, 'title': 'Intermediate', 'status': 'todo'},
                           'links': {'parents': [leaf]}}},
        leaf: {'detail': {'owner': 'worker-one', 'task': {'id': leaf, 'title': 'Reconcile cutover',
                            'status': 'scheduled', 'hostname': 'worker-one-host'},
                          'events': [{'kind': 'scheduled', 'payload': {'awaiting': {
                              'kind': 'chat', 'why': 'Choose which cutover premise to retain'}}}]}}
    })
    r = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert 'Reconcile cutover' in r['detail']
    assert 'on worker-one (worker-one-host)' in r['detail']
    assert 'asks origin: Choose which cutover premise to retain' in r['detail']
    assert next(a for a in r['actions'] if a['label'] == 'Open dependency')['target']['id'] == leaf
    assert next(a for a in r['actions'] if a['label'] == 'Open dependency')['role'] == 'dependency'
    # An unrelated completed branch cannot replace the deepest unfinished parent.
    b['cards'][parent]['detail']['links']['parents'].append('t_3333333333333333')
    b['cards']['t_3333333333333333'] = {'detail': {'task': {'id': 't_3333333333333333', 'title': 'Finished', 'status': 'done'}}}
    assert model.reduce_panel(b, now=NOW)['sections']['waiting'][0]['detail'] == r['detail']
    # A cycle is bounded and cannot hide the original leaf reason.
    b['cards'][leaf]['detail']['links'] = {'parents': [parent]}
    assert model.reduce_panel(b, now=NOW)['sections']['waiting'][0]['detail'] == r['detail']
    # Actual graph evidence also works without an upstream summary, across all root parents.
    row.pop('upstream')
    b['cards'][row['id']] = {'detail': {'task': row, 'links': {'parents': ['t_3333333333333333', parent]}}}
    assert model.reduce_panel(b, now=NOW)['sections']['waiting'][0]['detail'] == r['detail']
    # Full owner text stays inspectable; inline location/reason remains short and excludes hashes/URLs.
    why = b['cards'][leaf]['detail']['events'][0]['payload']['awaiting']
    why['why'] += ' ' + 'a' * 40 + ' https://example.com/build ' + 'long reason ' * 40
    compact = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert 'a' * 40 not in compact['detail'] and 'https://' not in compact['detail']
    assert 'https://example.com/build' in ' '.join(compact['details'])
    b['cards'][leaf]['detail']['task']['status'] = 'running'
    live = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert '(running)' in live['detail'] and 'Choose which' not in live['detail']


@pytest.mark.parametrize('source, owner, gateway', [('live', None, 'hub'), ('replica', 'remote', 'remote')])
def test_wait_names_hydrated_parent_and_opens_exact_owner(source, owner, gateway):
    cid = 't_0123456789abcdef'
    row = task(status='scheduled', wait={'wait_state': 'waiting', 'waiting_on': f'producer card {cid} (scheduled), then its release', 'waiting_since': NOW - 120})
    b = bundle(row, cards={cid: {'board': 'other', 'detail': {'source': source, 'owner': owner, 'task': {'id': cid, 'title': 'Publish the release'}}}})
    b.update(gateway='hub', profile='default')
    r = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert r['detail'] == f'waiting on: Publish the release on {gateway} · 2m'
    action = next(a for a in r['actions'] if a['label'] == 'Open dependency')
    assert action['target'] == {'id': cid, 'gateway': gateway, 'profile': 'default', 'board': 'other'}
    assert any(cid in d for d in r['details'])
    b['cards'][cid]['detail'].update(source='replica', owner=None)
    r = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert not any(a['label'] == 'Open dependency' for a in r['actions'])


def test_awaited_card_outside_this_chat_shows_its_live_status():
    # A review card a worker created is no row of this chat: its running status rides on the waiting line.
    cid = 't_0123456789abcdef'
    row = task(status='todo', wait={'wait_state': 'waiting', 'waiting_on': f'card {cid}', 'waiting_since': NOW - 120})
    b = bundle(row, cards={cid: {'detail': {'source': 'live', 'task': {'id': cid, 'title': 'Review PR', 'status': 'running'}}}})
    b.update(gateway='hub', profile='default')
    r = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert r['detail'] == 'waiting on: Review PR (running) on hub · 2m'
    assert f'Blocking card: {cid} (running)' in r['details']
    b['cards'][cid]['detail']['task'].pop('status')
    r = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert r['detail'] == 'waiting on: Review PR on hub · 2m'
    assert f'Blocking card: {cid} (status unknown)' in r['details']


@pytest.mark.parametrize('on', ['the clock until 2026-11-21 00:00 UTC', 'ci https://example.com/run', 'deploy release', 'an agent to release it: authority t_0123456789abcdef is archived'])
def test_parent_metadata_does_not_replace_unrelated_waits(on):
    cid = 't_0123456789abcdef'
    row = task(status='scheduled', upstream={'id': cid, 'title': 'Other work', 'gateway': 'remote', 'profile': 'default'}, wait={'wait_state': 'waiting', 'waiting_on': on})
    r = model.reduce_panel(bundle(row), now=NOW)['sections']['waiting'][0]
    assert r['detail'] == model.wait_phrase(row, NOW) + ' on remote'
    assert not any(a['label'] == 'Open dependency' for a in r['actions'])


def test_own_wait_and_unhydrated_primary_never_link_to_self_or_secondary():
    cid, other = 't_0123456789abcdef', 't_abcdef0123456789'
    cards = {cid: {'detail': {'source': 'live', 'task': {'id': cid, 'title': 'Own work'}}}}
    for own, on in ((cid, f'card {cid}'), ('t_aaaaaaaa', f'external comments on {other} from source owner {cid}')):
        row = task(own, status='scheduled', wait={'wait_state': 'waiting', 'waiting_on': on})
        b = bundle(row, cards=cards)
        b.update(gateway='hub', profile='default')
        r = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
        assert 'Own work' not in r['detail']
        assert not any(a['label'] == 'Open dependency' for a in r['actions'])


@pytest.mark.parametrize('graph_key', ['upstream', 'blocked_upstream'])
@pytest.mark.parametrize('on', [
    'card t_aaaaaaaaaaaaaaaa',
    'card t_bbbbbbbbbbbbbbbb',
    'producer card t_bbbbbbbbbbbbbbbb (scheduled), then its release',
    'external comments on t_bbbbbbbbbbbbbbbb from source owner t_cccccccccccccccc',
    'external vendor ticket update',
])
def test_explicit_primary_never_loses_to_unrelated_graph(graph_key, on):
    other = 't_cccccccccccccccc'
    row = task('t_aaaaaaaaaaaaaaaa', status='scheduled', **{graph_key: {'id': other, 'title': 'Unrelated work', 'gateway': 'remote', 'profile': 'default'}},
               wait={'wait_state': 'waiting', 'waiting_on': on, 'waiting_since': NOW - 120})
    b = bundle(row, cards={other: {'detail': {'source': 'replica', 'owner': 'remote', 'task': {'id': other, 'title': 'Unrelated work'}}}})
    b.update(gateway='hub', profile='default')
    r = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    if on == 'card t_aaaaaaaaaaaaaaaa':
        assert r['detail'] == 'waiting on an update · 2m on remote'
    assert 'Unrelated work' not in r['detail']
    assert not any(a['label'] == 'Open dependency' for a in r['actions'])


@pytest.mark.parametrize('on', ['', 'card t_bbbbbbbbbbbbbbbb', 'external comments on t_bbbbbbbbbbbbbbbb'])
def test_primary_and_unpublished_graph_wait_resolve_intended_card(on):
    primary, other = 't_bbbbbbbbbbbbbbbb', 't_cccccccccccccccc'
    row = task('t_aaaaaaaaaaaaaaaa', status='scheduled', upstream={'id': primary if not on else other},
               wait={'wait_state': 'waiting', 'waiting_on': on, 'waiting_since': NOW - 120})
    cards = {cid: {'detail': {'source': 'replica', 'owner': 'remote', 'task': {'id': cid, 'title': title}}}
             for cid, title in ((primary, 'Intended work'), (other, 'Unrelated work'))}
    b = bundle(row, cards=cards)
    b.update(gateway='hub', profile='default')
    r = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert r['detail'].startswith('waiting on: Intended work')
    assert next(a for a in r['actions'] if a['label'] == 'Open dependency')['target']['id'] == primary


@pytest.mark.parametrize('on', ['the clock until 2026-11-23 12:00 UTC', 'the clock until 2026-11-21 00:00 UTC', 'ci https://example.com/run', 'deploy release'])
def test_hydrated_graph_never_replaces_explicit_noncard_wait(on):
    row = task('t_aaaaaaaaaaaaaaaa', status='scheduled', wait={'wait_state': 'waiting', 'waiting_on': on})
    cid = 't_bbbbbbbbbbbbbbbb'
    b = bundle(row, cards={row['id']: {'detail': {'links': {'parents': [cid]}}},
                           cid: {'detail': {'owner': 'remote', 'task': {'id': cid, 'title': 'Unrelated work', 'status': 'blocked', 'profile': 'default'}}}})
    r = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert r['detail'] == model.wait_phrase(row, NOW) + ' on remote'
    assert 'Unrelated work' not in str(r)
    assert not any(a['label'] == 'Open dependency' for a in r['actions'])


@pytest.mark.parametrize('source, expected', [('live', True), ('replica', False)])
@pytest.mark.parametrize('depth', [1, 2])
def test_graph_leaf_inherits_serving_gateway_only_for_live_sources(source, expected, depth):
    row = task('t_aaaaaaaaaaaaaaaa', status='blocked')
    cid = 't_bbbbbbbbbbbbbbbb'
    b = bundle(row, cards={row['id']: {'detail': {'links': {'parents': [cid]}}},
                           cid: {'board': 'other', 'detail': {'source': source, 'task': {'id': cid, 'title': 'Local leaf', 'status': 'scheduled'},
                                'events': [{'kind': 'scheduled', 'payload': {'awaiting': {'kind': 'clock', 'why': 'Cooldown window'}}}]}}})
    b.update(gateway='hub', profile='default')
    if depth == 2:
        mid = 't_cccccccccccccccc'
        b['cards'][row['id']]['detail']['links']['parents'] = [mid]
        b['cards'][mid] = {'detail': {'source': 'live', 'task': {'id': mid, 'title': 'Intermediate', 'status': 'blocked'}, 'links': {'parents': [cid]}}}
        row['wait'] = {'wait_state': 'waiting', 'waiting_on': f'card {mid}'}
    r = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert ('Local leaf' in r['detail']) is expected
    actions = [a for a in r['actions'] if a['label'] == 'Open dependency']
    assert bool(actions) is (expected or depth == 2)
    if expected:
        assert 'Cooldown window' in r['detail'] and 'on hub' in r['detail']
        assert actions[0]['target'] == {'id': cid, 'gateway': 'hub', 'profile': 'default', 'board': 'other'}


@pytest.mark.parametrize('on', ['the clock until 2026-11-23 12:00 UTC', 'the clock until 2026-11-21 00:00 UTC'])
def test_owner_reason_preserves_clock_countdown_and_overdue(on):
    row = task(status='scheduled', wait={'wait_state': 'waiting', 'waiting_on': on})
    b = bundle(row, cards={row['id']: {'detail': {'events': [{'kind': 'scheduled', 'payload': {'awaiting': {'kind': 'clock', 'why': 'Cooldown window'}}}]}}})
    r = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert model.wait_phrase(row, NOW) in r['detail']
    assert 'Blocking reason: Cooldown window' in r['details']


@pytest.mark.parametrize('on', ['the clock until 2026-11-23 12:00 UTC', 'ci https://example.com/run', 'deploy release', 'card t_dddddddddddddddd'])
def test_leaf_traversal_obeys_intermediate_explicit_producer(on):
    mid, other, primary = 't_bbbbbbbbbbbbbbbb', 't_cccccccccccccccc', 't_dddddddddddddddd'
    row = task(status='scheduled', wait={'wait_state': 'waiting', 'waiting_on': f'card {mid}'})
    b = bundle(row, cards={mid: {'detail': {'owner': 'remote', 'task': {'id': mid, 'title': 'Actual producer', 'status': 'scheduled',
                                     'wait': {'waiting_on': on}}, 'links': {'parents': [other]}}},
                           other: {'detail': {'owner': 'remote', 'task': {'id': other, 'title': 'Unrelated ancestor', 'status': 'blocked'}}},
                           primary: {'detail': {'owner': 'remote', 'task': {'id': primary, 'title': 'Named primary', 'status': 'scheduled'}}}})
    b.update(gateway='remote', profile='default')
    r = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert 'Unrelated ancestor' not in r['detail']
    expected = primary if on.startswith('card ') else mid
    assert next(a for a in r['actions'] if a['label'] == 'Open dependency')['target']['id'] == expected


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
    # The section says done; rows carry only when (age) and what (result), never a 'done' status word.
    assert sorted(r['detail'] for r in m['sections']['done']) == ['just now', 'just now · Merged as abc']


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
    assert m['header']['label'] == 'waiting'  # derived section, not request detail
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


def test_legacy_kind_is_not_protocol_and_rows_supply_explicit_derived_header():
    run = task()
    run['execution_observations'] = [{'identity': {'kind': 'card', 'work_id': run['id'], 'gateway': 'remote', 'profile': 'default', 'board': 'default'}, 'state': 'running'}]
    m = model.reduce_panel(bundle(run, outcome_json='{"kind":"unresolved","reason":"interrupted"}'), now=NOW, projector=lambda r, *, now: {'state': 'running', 'sourced': True})
    assert m['header']['state'] == 'running' and m['header']['source'] == 'derived'
    assert m['header']['status'] is None  # legacy kind is not the protocol
    assert model.reduce_panel(bundle(outcome_json='{"kind":"unresolved"}'), now=NOW)['header']['label'] == 'idle'
    now = model.datetime.fromisoformat(answered()['resolution']['recordedAt']).timestamp() + 5
    m = model.reduce_panel(bundle(outcome_json='{"kind":"unresolved"}'), now=now, requests=[answered()])
    assert m['header']['label'] == 'waiting'  # derived section, not request detail
