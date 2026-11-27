"""Hub-owned chart visuals and draft-cap previews retain observation provenance."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest


def load():
    spec = importlib.util.spec_from_file_location('lane_metrics_present_test', Path(__file__).parents[1] / 'dashboard/metrics.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot(cells):
    return {'lanes': {'work': {'ticks': {'window': '6h', 'bucket_s': 450, 'cap': 4, 'cells': cells},
                               'status': {'run': 3}, 'spend': {'hour': 1.23},
                               'limits': {'max_workers': 4}},
                      'missing': {'ticks': None, 'spend': None}}}


def cell(running, cap=4, state='partial', rejected=0):
    return dict(t=100, running=running, cap=cap, state=state, rejected=rejected)


def test_actual_visual_states_scales_and_missing_samples():
    cells = [cell(0, state='empty'), cell(1), cell(4, state='full'),
             cell(2, state='rejecting', rejected=2), cell(None, state='unknown', rejected=None),
             cell(2, cap=None), cell(4, cap=0, state='rejecting')]
    out = load().present(snapshot(cells), None)
    rendered = out['lanes']['work']['ticks']['cells']
    assert [c['state'] for c in rendered] == [c['state'] for c in cells]
    assert [c['visual'] for c in rendered] == [
        dict(height=.15, opacity=1), dict(height=.25, opacity=.375),
        dict(height=1, opacity=1), dict(height=1, opacity=1), dict(height=1, opacity=1),
        dict(height=.5, opacity=.5), dict(height=1, opacity=1)]
    assert 'previews' not in out['lanes']['work']['ticks']
    assert out['lanes']['missing'] == dict(ticks=None, spend=None)


def test_hub_previews_every_stop_counts_and_unknown_provenance():
    cells = [cell(0, state='empty'), cell(1), cell(2), cell(3),
             cell(None, state='unknown', rejected=None), cell(1, state='rejecting', rejected=4)]
    before = snapshot(cells)
    out = load().present(before, 3)
    ticks = out['lanes']['work']['ticks']
    previews = ticks['previews']
    assert list(previews) == ['0', '1', '2', '3', 'unlimited']
    assert [p['affected'] for p in previews.values()] == [4, 2, 1, 0, 0]
    for key, preview in previews.items():
        cap = None if key == 'unlimited' else int(key)
        assert preview['total'] == 6 and preview['unknown'] == 1 and preview['now'] == 3
        for original, forecast in zip(cells, preview['cells']):
            assert forecast['cap'] == cap and forecast['forecast'] is True
            assert forecast['running'] == original['running']
            assert forecast['rejected'] == original['rejected']  # never invent historical rejections
        assert preview['cells'][4]['state'] == 'unknown'
        assert preview['cells'][4]['visual'] == dict(height=1, opacity=1)
    assert [c['state'] for c in previews['2']['cells']] == [
        'empty', 'partial', 'full', 'rejecting', 'unknown', 'partial']
    assert [c['state'] for c in previews['0']['cells']] == [
        'empty', 'rejecting', 'rejecting', 'rejecting', 'unknown', 'rejecting']
    assert [c['state'] for c in previews['unlimited']['cells']] == [
        'empty', 'partial', 'partial', 'partial', 'unknown', 'partial']
    assert previews['2']['cells'][1]['visual'] == dict(height=.5, opacity=.5)
    assert previews['unlimited']['cells'][1]['visual']['height'] == pytest.approx(1 / 3)
    # Actual historical rejections remain red; unlimited draft isn't an actual rejection.
    assert ticks['cells'][-1]['state'] == 'rejecting'
    assert 'forecast' not in ticks['cells'][-1]


def test_present_is_nonmutating_and_does_not_alias_cached_inputs_or_previews():
    source = snapshot([cell(1), cell(None, rejected=None)])
    original = deepcopy(source)
    metrics = load()
    out = metrics.present(source, 2)
    assert source == original and out is not source
    assert out['fleet_slots'] == 2
    out['lanes']['work']['status']['run'] = 999
    out['lanes']['work']['spend']['hour'] = 999
    out['lanes']['work']['limits']['max_workers'] = 999
    ticks = out['lanes']['work']['ticks']
    ticks['previews']['1']['cells'][0]['visual']['height'] = 999
    assert ticks['previews']['2']['cells'][0]['visual']['height'] != 999
    assert ticks['cells'][0]['visual']['height'] != 999
    assert source == original
    assert metrics.present(source, 2) == metrics.present(source, 2)


@pytest.mark.parametrize('slots', [None, -1, True, 2.5, '3'])
def test_unknown_or_invalid_capacity_clears_old_previews_without_mutation(slots):
    metrics = load()
    known = metrics.present(snapshot([cell(1)]), 2)
    old = deepcopy(known)
    unknown = metrics.present(known, slots)
    assert 'previews' not in unknown['lanes']['work']['ticks']
    assert known == old


@pytest.mark.parametrize('cells', [[], [cell(None, state='unknown', rejected=None)]])
def test_zero_capacity_empty_and_all_unknown_series(cells):
    ticks = load().present(snapshot(cells), 0)['lanes']['work']['ticks']
    assert list(ticks['previews']) == ['0', 'unlimited']
    for preview in ticks['previews'].values():
        assert preview['total'] == len(cells)
        assert preview['unknown'] == len(cells)
        assert preview['affected'] == 0


def test_partial_visual_has_visible_floor_and_bounded_ratio():
    cells = [cell(1, cap=100), cell(200, cap=100)]
    rendered = load().present(snapshot(cells), 0)['lanes']['work']['ticks']['cells']
    assert rendered[0]['visual'] == dict(height=.15, opacity=.255)
    assert rendered[1]['visual'] == dict(height=1, opacity=.75)
