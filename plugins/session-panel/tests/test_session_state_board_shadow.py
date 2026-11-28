"""Paired legacy/v2 classification from real disposable-board reads. [daveey]"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

pytest.importorskip('hermes_session_state_unified')
from tests.session_state import conftest as harness
from tests.session_state.p0_semantic_board import DATA, prepare, trace
from hermes_session_state_collect import collect
from hermes_session_state_panel_inputs import panel_bundle
from hermes_state_session_status_snapshot import assemble
from hermes_state_session_status import reduce_status
from session_state_v2_fixtures import ROOT, load

kanban_root = harness.kanban_root
registry = harness.registry
board = harness.board
db = harness.db
clock = harness.clock
store = harness.store

legacy_model = load('_p2_shadow_legacy_model', ROOT / 'plugins/session-panel/model.py')
ALLOWLIST = json.loads(Path(__file__).with_name('session_state_shadow_allowlist.json').read_text())


def verify(record):
    expected = ALLOWLIST[record['scenario']]
    assert expected['reason']
    assert record['differences'] == expected['differences'], record['differences']


def test_shadow_manifest_coverage():
    assert len(DATA['scenarios']) == 17
    assert set(ALLOWLIST) == {c['id'] for c in DATA['scenarios']} | {
        'resolution_' + v for v in ('committed', 'failed', 'reblocked', 'sibling', 'shared_parent')}


@pytest.mark.parametrize('old,new', [({}, {'x': None}), ({'x': 1}, {'x': 2}),
                                   ({'rows': []}, {'rows': [{'request_id': 'wrong'}]}),
                                   ({'a': [1, 2]}, {'a': [2, 1]})])
def test_shadow_rejects_unallowed_difference(old, new):
    record = {'scenario': 'human_brief', 'differences': differences(old, new)}
    with pytest.raises(AssertionError):
        verify(record)



def leaves(value, path=''):
    if isinstance(value, (dict, list)) and value:
        items = value.items() if isinstance(value, dict) else enumerate(value)
        return {p: leaf for key, item in items for p, leaf in leaves(item, path + '/' + str(key)).items()}
    return {path: value}


def differences(left, right):
    """Exact leaves, including absence versus null and additions/removals."""
    old, new = leaves(left), leaves(right)
    missing = {'absent': True}
    return {p: [old.get(p, missing), new.get(p, missing)] for p in sorted(old.keys() | new.keys())
            if p not in old or p not in new or old[p] != new[p]}


def paired(store, db, sid, clock):
    captured = []
    original = store._collect

    def capture(*args, **kwargs):
        inputs = original(*args, **kwargs)
        captured.append(deepcopy(inputs))
        return inputs

    store._collect = capture
    try:
        actual = store.recompute(sid)
    finally:
        store._collect = original
    assert actual == store.get(sid)
    assert len(captured) == 1
    inputs = captured[0]
    snapshot, base = assemble(inputs['parts'], now=clock.now)
    status = reduce_status(snapshot, previous=base)
    # Feed-model classifier boundary, same collected graph/runtime/request evidence.
    # No legacy I/O adapter or historical golden is substituted for actual reads.
    bundle = panel_bundle(inputs, now=clock.now, resolutions=[])
    # V1 hydration is id-keyed, v2 owner-keyed. These fixtures have unique IDs;
    # assert that before projecting so a collision can never be silently lost.
    records = list(bundle['cards'].values())
    bundle['cards'] = {r['detail']['task']['id']: r for r in records}
    assert len(bundle['cards']) == len(records)
    bundle['outcome_json'] = {'status': status}
    legacy = legacy_model.reduce_panel(bundle, now=clock.now, wire=True)
    left = {'sections': legacy['sections'], 'groups': legacy['groups'],
            'counts': legacy['header']['counts'],
            'pips': [{k: v for k, v in p.items() if k != 'label'} for p in legacy['header']['pips']],
            'stripe': status['presentation']['stripe'], 'kind': status['kind']}
    right = {'sections': actual['sections'], 'groups': actual['groups'],
             'counts': actual['header']['counts'], 'pips': actual['header']['pips'],
             'stripe': actual['header']['stripe'], 'kind': actual['legacy']['kind']}
    return actual, {'inputs': inputs, 'legacy_snapshot': snapshot, 'legacy_status': status,
                    'legacy_panel_bundle': bundle, 'legacy': left, 'v2': right,
                    'differences': differences(left, right)}


@pytest.mark.parametrize('case', DATA['scenarios'], ids=lambda case: case['id'])
def test_disposable_board_shadow(case, db, board, store, clock, registry, kanban_root,
                                 monkeypatch, record_property):
    sid, replica, dispose = prepare(case, db, board, store, clock, registry, kanban_root, monkeypatch)
    try:
        state, record = paired(store, db, sid, clock)
        record.update(scenario=case['id'], board_trace=trace(db, board, sid, state, replica))
        record_property('p2_shadow', json.dumps(record, default=list))
        verify(record)
        print('SHADOW ' + case['id'] + ' ' + json.dumps(record['differences'], sort_keys=True))
        assert state['identity']['tip'] == sid
    finally:
        dispose()


@pytest.mark.parametrize('variant', ['committed', 'failed', 'reblocked', 'sibling', 'shared_parent'])
def test_resolution_shadow(variant, db, board, store, clock, registry, kanban_root,
                           monkeypatch, record_property):
    from hermes_cli import kanban_db as kb
    from hermes_session_state_requests import register_request_provider
    from tests.session_state.p0_semantic_board import seed_card

    case = next(c for c in DATA['scenarios'] if c['id'] == 'human_brief')
    sid, replica, dispose = prepare(case, db, board, store, clock, registry, kanban_root, monkeypatch)
    try:
        inputs = collect(db, sid)
        requests = deepcopy(inputs['requests'])
        key = next(iter(inputs['dependency_graph']['rows']))
        tid = inputs['dependency_graph']['rows'][key]['id']
        if variant == 'sibling':
            requests.append({**deepcopy(requests[0]), 'requestId': 'p0-sibling'})
        if variant == 'shared_parent':
            board.execute('UPDATE tasks SET session_id=NULL WHERE id=?', (tid,))
            for child in ('t_3333333333333333', 't_4444444444444444'):
                seed_card(board, dict(id=child, title='Dependent ' + child,
                                     status='todo', session_id=sid), now=clock.now)
                board.execute('INSERT INTO task_links(parent_id,child_id) VALUES (?,?)', (tid, child))
            board.commit()
        dispose()
        dispose = register_request_provider('p0-shadow', lambda context: deepcopy(requests))
        before = store.recompute(sid)
        resolution = dict(request_id=requests[0]['requestId'], card=key,
                          episode=before['cards'][key]['block_episode'], action='answer', choice_id='small',
                          label='Small', by='you', at=clock.now, delivery='failed' if variant == 'failed' else 'committed')
        assert store.add_resolution(sid, resolution)
        if variant == 'reblocked':
            assert kb.unblock_task(board, tid)
            clock.now += 1
            assert kb.block_task(board, tid, kind='needs_input', human_request=case['card']['human_request'])
            requests[0]['requestId'] = 'p0-new-episode'
        state, record = paired(store, db, sid, clock)
        record.update(scenario='resolution_' + variant, resolutions=store.resolutions(sid),
                      board_trace=trace(db, board, sid, state, replica))
        record_property('p2_shadow', json.dumps(record, default=list))
        verify(record)
        print('SHADOW ' + variant + ' ' + json.dumps(record['differences'], sort_keys=True))
        assert state['cards'][key]['red'] is (variant in ('failed', 'reblocked', 'sibling'))
        if variant == 'sibling':
            assert [a['id'] for a in state['asks']] == ['p0-sibling']
        if variant == 'shared_parent':
            assert len(state['cards']) == 3
            assert state['cards'][key]['linked'] is False
            assert state['groups']
            assert 'Small' in state['groups'][0]['detail']
            assert state['groups'][0]['clock_text']['detail'][-1] == {
                'format': 'ago', 'timestamp': clock.now}
            assert all(g.get('section') != 'needs_you' for g in state['groups'])
    finally:
        dispose()
