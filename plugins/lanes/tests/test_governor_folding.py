"""Governors fold into lane rows: holds, fleet lines, stale sources and waits (generic fixtures)."""
import importlib.util
from pathlib import Path

import pytest


def module(name):
    spec = importlib.util.spec_from_file_location('fold_' + name, Path(__file__).parents[1] / 'dashboard' / (name + '.py'))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


gov, view = module('governors'), module('view')
IDS = [i for ids in gov._G.values() for i in ids]
REGISTRY = [dict(id=i, label=i.replace('_', ' ').title(), kind='hold', threshold='clears ' + i) for i in IDS]


def snap(board, rows, idle, **extra):
    return dict(board=board, available=True, stale=False, age_s=5, idle_total=idle, open_lanes=extra.pop('lanes', ['alpha']),
                cards=extra.pop('cards', {}), governors=rows, **extra)


def row(ident, cards=0, **extra):
    return dict(governor=ident, cards=cards, card_ids=[], **extra)


def raw_for(governors, cards, lanes=('alpha', 'beta')):
    return dict(governors=governors, cards=cards,
                lanes={n: dict(open=1, running=0, blocked_on_you=0, ready=0, held=0, target=0, actual=0) for n in lanes})


def project_view(governors, cards, lanes=('alpha', 'beta')):
    return view.project(dict(lanes={n: dict(open=1) for n in lanes}), raw_for(governors, cards, lanes), False)


def test_groups_cover_the_forty_two_registry_ids_once():
    assert len(gov.GROUPS) == 42 == len(IDS)
    assert set(gov.GROUPS.values()) == {'wait', 'reorder', 'lane_dial', 'fleet_dial', 'budget', 'safety', 'fault'}


def test_case1_replay_folds_to_lane_chip_fleet_line_and_stale_sources():
    rows = [row('lane_gate', 64, scope='alpha', reading=10, threshold=50), row('host_slots', 1, reading=1, threshold=1)]
    snapshots = [snap('main', rows, 65),
                 dict(board='aborted', available=True, stale=False, idle_total=None, phase='preclaim_aborted',
                      global_stop='storage_admission', open_lanes=['beta'], cards={},
                      governors=[row('storage_admission', None), row('lane_gate', None, unknown=True)]),
                 dict(board='old', available=True, stale=True, age_s=900, open_lanes=['gamma'], cards={}, governors=[])]
    result = gov.project(snapshots, [], REGISTRY)
    chip, = result['holds']['alpha']
    assert (chip['id'], chip['count'], chip['reading'], chip['threshold'], chip['group']) == ('lane_gate', 64, 10, 50, 'lane_dial')
    assert chip['clears'] == 'clears lane_gate' and chip['readings'][0]['lane'] == 'alpha'
    assert [(f['id'], f['count']) for f in result['fleet']] == [('host_slots', 1)]
    assert sorted(s['board'] for s in result['stale_sources']) == ['aborted', 'old']
    assert result['held_total'] == 65 and result['fleet_stop'] is False


def test_lane_gate_chip_prefers_lane_scoped_reading():
    rows = [row('lane_gate', 3, scope='other', reading=2, threshold=5), row('lane_gate', 64, scope='alpha', reading=10, threshold=50)]
    result = gov.project([snap('main', rows, 67)], [], REGISTRY)
    assert result['holds']['alpha'][0]['reading'] == 10 and result['holds']['other'][0]['reading'] == 2


def test_case2_scheduled_and_parent_cards_never_count_as_held():
    cards = [dict(id=f't{i}', board='main', lane='alpha', status='scheduled', step='waiting', reason='time') for i in range(100)]
    result = gov.project([snap('main', [row('schedule', 100)], 100)], cards, REGISTRY)
    assert result['held_total'] == 0 and result['holds'] == {} and result['fleet'] == []
    body = project_view(result, cards)
    lane = body['lanes']['alpha']
    assert lane['governor_held'] == 0 and lane['holds'] == []
    assert lane['waits'] == dict(parents=0, scheduled=100, deploy_ci=0, triage=0)
    assert 'top_reason' not in lane


def test_waits_break_down_parents_scheduled_deploy_ci_triage():
    def card(i, reason, **extra):
        return dict(id=f't{i}', board='main', lane='alpha', step='waiting', reason=reason, **extra)
    cards = [card(1, 'gov:dependencies'), card(2, 'gov:schedule', wait='card'), card(3, 'gov:schedule', wait='ci'),
             card(4, 'gov:schedule', wait='deploy'), card(5, 'gov:schedule', wait='slack'), card(6, 'triage'), card(7, 'run')]
    waits = view.waits_of(cards)
    assert waits == dict(parents=2, scheduled=1, deploy_ci=2, triage=1)


def test_case3_stale_board_only_marks_its_lanes_unknown():
    snapshots = [snap('main', [row('host_slots', 2, reading=1, threshold=1)], 2, lanes=['alpha']),
                 dict(board='old', available=True, stale=True, age_s=900, open_lanes=['beta'], cards={}, governors=[])]
    cards = [dict(id='a1', board='main', lane='alpha', status='todo', step='waiting', reason='dep')]
    result = gov.project(snapshots, cards, REGISTRY)
    assert [s['board'] for s in result['stale_sources']] == ['old']
    lanes = project_view(result, cards)['lanes']
    assert lanes['alpha']['governor_held'] == 0 and lanes['alpha']['waits']['parents'] == 1
    assert lanes['beta']['governor_held'] is None and lanes['beta']['holds'] == []
    assert lanes['beta']['waits'] == dict(parents=None, scheduled=None, deploy_ci=None, triage=None)
    assert result['held_total'] == 2  # known dial charges survive the stale sibling


def test_stale_board_without_lane_list_keeps_whole_fence():
    snapshots = [snap('main', [], 0), dict(board='old', available=False, stale=True, governors=[])]
    result = gov.project(snapshots, [], REGISTRY)
    assert result['incomplete_lanes'] is None
    assert all(l['governor_held'] is None for l in project_view(result, [])['lanes'].values())


def test_truncated_receipts_do_not_fabricate_lane_totals():
    s = snap('main', [row('unassigned', 5)], 5, truncated=True, cards={'t1': dict(governor='unassigned')})
    cards = [dict(id='t1', board='main', lane='alpha', step='ready', reason='slot')]
    result = gov.project([s], cards, REGISTRY)
    assert result['lane_accounting_available'] is False
    lane = project_view(result, cards)['lanes']['alpha']
    assert lane['governor_held'] is None and lane['holds'] == []


@pytest.mark.parametrize('cleared', [False, True])
def test_case4_safety_stop_is_one_fleet_line_and_clears(cleared):
    glob = [] if cleared else [dict(governor='core_update_drain', verdict='stop')]
    result = gov.project([snap('main', [row('core_update_drain', 0)], 0, globals=glob)], [], REGISTRY)
    assert result['fleet_stop'] is (not cleared)
    assert [f['id'] for f in result['fleet']] == ([] if cleared else ['core_update_drain'])
    if not cleared:
        assert result['fleet'][0]['stop'] and result['fleet'][0]['group'] == 'safety' and result['held_total'] == 0
    lanes = project_view(result, [])['lanes']
    assert all(l['fleet_stop'] is (not cleared) for l in lanes.values())


def test_fleet_dial_at_cap_without_charge_or_safety_count_zero_is_silent():
    glob = [dict(governor='host_slots', verdict='stop')]
    result = gov.project([snap('main', [row('host_slots', 0, reading=2, threshold=2), row('quiet', 0)], 0, globals=glob)], [], REGISTRY)
    assert result['fleet'] == [] and result['fleet_stop'] is False


def test_fleet_stop_ignores_stale_board_verdict():
    s = snap('main', [row('core_update_drain', 0)], 0, globals=[dict(governor='core_update_drain', verdict='stop')])
    s.update(stale=True)
    assert gov.project([s], [], REGISTRY)['fleet_stop'] is False


def test_case5_fault_and_budget_cards_count_as_you_not_held():
    charges = {'t1': dict(governor='unassigned'), 't2': dict(governor='model_pin'), 't3': dict(governor='spend_budget')}
    cards = [dict(id=i, board='main', lane='alpha', step='ready', reason='slot') for i in charges]
    result = gov.project([snap('main', [row('unassigned', 1), row('model_pin', 1), row('spend_budget', 1)], 3, cards=charges)],
                         cards, REGISTRY)
    assert result['held_total'] == 0
    chips = {c['id']: c for c in result['holds']['alpha']}
    assert set(chips) == {'unassigned', 'model_pin', 'spend_budget'}
    assert chips['unassigned']['group'] == 'fault' and chips['spend_budget']['group'] == 'budget'
    assert all(c['needs_you'] for c in chips.values())
    body = project_view(result, cards)
    assert body['status']['you'] == 3 and body['lanes']['alpha']['governor_held'] == 0
    assert body['lanes']['alpha']['status']['you'] == 3


@pytest.mark.parametrize('ident', ['unassigned', 'nonspawnable', 'model_pin', 'capsule', 'placement', 'refusal_fallback',
                                   'unrecorded', 'spend_budget', 'no_progress', 'respawn_guard', 'spawn_failed'])
def test_status_fault_ids_map_to_you_and_dials_do_not(ident):
    assert view.status_of('gov:' + ident) == 'you'
    assert view.status_of('gov:lane_gate') == 'gate' and view.status_of('gov:host_slots') == 'worker'


def test_card_derived_dial_counts_come_from_charges_and_chip_has_no_invented_reading():
    charges = {'t1': dict(governor='lane_paused'), 't2': dict(governor='host_slots')}
    cards = [dict(id=i, board='main', lane='alpha', step='ready', reason='slot') for i in charges]
    result = gov.project([snap('main', [row('lane_paused', 1), row('host_slots', 1)], 2, cards=charges)], cards, REGISTRY)
    assert [(c['id'], c['count'], c['reading']) for c in result['holds']['alpha']] == [('lane_paused', 1, None)]
    assert result['held_total'] == 2
    assert project_view(result, cards)['lanes']['alpha']['governor_held'] == 2
