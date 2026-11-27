"""Historical mixed-board replay; core receipts, not flow card samples, own counts."""
import importlib.util
from pathlib import Path


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / 'dashboard' / (name + '.py'))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_0720_mixed_board_receipts_preserve_known_charges():
    registry = [dict(id=k, label=k, kind='hold', threshold='limit')
                for k in ('lane_gate', 'storage_admission', 'llm_pool')]
    rows = [dict(governor='lane_gate', scope='hermes', cards=64, reading=10, threshold=50),
            dict(governor='storage_admission', cards=0), dict(governor='llm_pool', cards=0)]
    snapshots = [dict(board='hermes', available=True, stale=False, idle_total=64,
                      open_lanes=['hermes'], cards={}, governors=rows),
                 dict(board='default', available=True, stale=False, idle_total=None,
                      phase='preclaim_aborted', global_stop='storage_admission',
                      open_lanes=['other'], cards={}, governors=[
                          dict(governor='storage_admission', cards=2),
                          dict(governor='lane_gate', cards=None, unknown=True),
                          dict(governor='llm_pool', cards=None, unknown=True)]),
                 dict(board='askeve', available=True, stale=True, age_s=900,
                      open_lanes=['project'], cards={}, governors=[dict(governor='llm_pool', cards=1)])]
    result = module('governors').project(snapshots, [], registry)
    projected = {r['id']: r for r in result['rows']}
    gate = projected['lane_gate']
    assert (gate['state'], gate['cards_charged'], gate['reading'], gate['threshold']) == ('on', 64, 10, 50)
    assert gate['readings'][0]['cards'] == rows[0]['cards']
    assert projected['storage_admission']['state'] == 'on'
    assert projected['llm_pool']['state'] == 'unknown'  # healthy zero cannot certify an unreached sibling
    assert projected['llm_pool']['partial']
    assert result['counts']['unknown'] == 1
    default = next(s for s in projected['llm_pool']['scope'] if s['board'] == 'default')
    assert default['available'] is False and 'storage_admission' in default['reason']
    assert next(s for s in gate['scope'] if s['board'] == 'hermes')['available'] is True
    assert result['incomplete_lanes'] == ['other', 'project']
    assert any(s['board'] == 'askeve' and 'stale' in s['reason'] for s in result['scope'])
    assert result['idle_total'] is None


def test_fresher_smaller_sibling_does_not_replace_dominant_lane_reading():
    registry = [dict(id='lane_gate', label='Lane gate', kind='hold', threshold='budget')]
    result = module('governors').project([
        dict(board='default', available=True, age_s=20, idle_total=64, cards={}, governors=[
            dict(governor='lane_gate', scope='hermes', cards=64, reading=10, threshold=50)]),
        dict(board='sibling', available=True, age_s=1, idle_total=4, cards={}, governors=[
            dict(governor='lane_gate', scope='hermes', cards=4, reading=0, threshold=50)])], [], registry)
    row, = result['rows']
    assert row['cards_charged'] == 68
    assert row['reading'] == 10
    assert [(r['board'], r['cards'], r['reading']) for r in row['readings']] == [('default', 64, 10), ('sibling', 4, 0)]


def test_count_mismatch_does_not_erase_other_board_evidence():
    registry = [dict(id=k, label=k, kind='hold', threshold='limit') for k in ('lane_gate', 'llm_pool')]
    result = module('governors').project([
        dict(board='healthy', available=True, idle_total=64, cards={}, governors=[
            dict(governor='lane_gate', cards=64, reading=10, threshold=50), dict(governor='llm_pool', cards=0)]),
        dict(board='aborted', available=True, idle_total=None, cards={}, governors=[
            dict(governor='lane_gate', cards=None), dict(governor='llm_pool', cards=2)])], [], registry)
    assert [r['state'] for r in result['rows']] == ['on', 'on']
    assert result['idle_total'] is None
