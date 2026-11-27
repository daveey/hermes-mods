"""Hub-owned chart visuals; draft-cap previews are computed client-side from per-cell running values."""
from copy import deepcopy
import importlib.util
import json
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


def test_draft_cap_previews_are_client_side_running_values_retained():
    cells = [cell(0, state='empty'), cell(1), cell(2), cell(3),
             cell(None, state='unknown', rejected=None), cell(1, state='rejecting', rejected=4)]
    out = load().present(snapshot(cells), 3)
    ticks = out['lanes']['work']['ticks']
    assert 'previews' not in ticks and out['fleet_slots'] == 3
    # The client recolours from these observed values; no invented rejections.
    for original, rendered in zip(cells, ticks['cells']):
        assert rendered['running'] == original['running']
        assert rendered['rejected'] == original['rejected']
        assert 'forecast' not in rendered
    assert ticks['cells'][4]['visual'] == dict(height=1, opacity=1)
    assert ticks['cells'][-1]['state'] == 'rejecting'


def test_present_is_nonmutating_and_does_not_alias_cached_inputs():
    source = snapshot([cell(1), cell(None, rejected=None)])
    original = deepcopy(source)
    metrics = load()
    out = metrics.present(source, 2)
    assert source == original and out is not source
    assert out['fleet_slots'] == 2
    out['lanes']['work']['status']['run'] = 999
    out['lanes']['work']['spend']['hour'] = 999
    out['lanes']['work']['limits']['max_workers'] = 999
    out['lanes']['work']['ticks']['cells'][0]['visual']['height'] = 999
    assert source == original
    assert metrics.present(source, 2) == metrics.present(source, 2)


@pytest.mark.parametrize('slots', [None, -1, True, 2.5, '3', 0, 24])
def test_legacy_cached_previews_are_stripped_without_mutation(slots):
    metrics = load()
    known = metrics.present(snapshot([cell(1)]), 2)
    known['lanes']['work']['ticks']['previews'] = {'0': {}}
    old = deepcopy(known)
    out = metrics.present(known, slots)
    assert 'previews' not in out['lanes']['work']['ticks']
    assert known == old


def test_payload_size_at_24_slots_typical_lanes_under_200kb():
    cells = [cell(i % 5, cap=24) for i in range(48)]
    view = {'lanes': {f'lane{i}': snapshot(cells)['lanes']['work'] for i in range(12)}}
    size = len(json.dumps(load().present(view, 24)).encode())
    assert size < 200_000, size


@pytest.mark.parametrize('reverse', [False, True])
def test_merge_present_unaligned_buckets_remain_unknown_in_both_orders(reverse):
    metrics = load()
    rows = []
    for source, start, running in [('alpha-board', 100, 1), ('beta-board', 101, 3)]:
        cells = [dict(cell(running, state='rejecting', rejected=2), t=start + i * 450)
                 for i in range(2)]
        row = snapshot(cells)['lanes']
        row['work']['spend'] = dict(hour=1., day=2., week=3.)
        metrics.qualify(row, source)
        rows.append(row)
    if reverse:
        rows.reverse()
    before = deepcopy(rows)
    merged = metrics.merge(*rows)
    out = metrics.present({'lanes': merged}, 8)['lanes']['work']
    assert '_tick_sources' not in out
    expected_source = 'beta-board' if reverse else 'alpha-board'
    unaligned_source = 'alpha-board' if reverse else 'beta-board'
    for index, bucket in enumerate(out['ticks']['cells']):
        assert bucket['t'] == (101 if reverse else 100) + index * 450
        assert bucket['state'] == 'unknown'
        assert bucket['running'] is None and bucket['rejected'] is None
        assert len(bucket['components']) == 2
        parts = {part['source']: part for part in bucket['components']}
        known = parts[expected_source]
        assert known['running'] == (3 if reverse else 1)
        assert known['state'] == 'rejecting' and known['rejected'] == 2
        unknown = parts[unaligned_source]
        assert unknown == dict(t=bucket['t'], source=unaligned_source, state='unknown',
                               running=None, cap=None, rejected=None, reason='unaligned samples',
                               visual=dict(height=1, opacity=1))
    assert rows == before


def test_partial_visual_has_visible_floor_and_bounded_ratio():
    cells = [cell(1, cap=100), cell(200, cap=100)]
    rendered = load().present(snapshot(cells), 0)['lanes']['work']['ticks']['cells']
    assert rendered[0]['visual'] == dict(height=.15, opacity=.255)
    assert rendered[1]['visual'] == dict(height=1, opacity=.75)
