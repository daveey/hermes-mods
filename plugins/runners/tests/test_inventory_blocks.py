"""Block carry must skip per-row checks, not skip any inventory semantics."""
from copy import deepcopy
import pytest
from plugins.runners import service as S
from test_inventory_memo import env, svc, comparable


def fleet(cfg, path):
    snap = deepcopy(svc(cfg, path, 1001)._snapshot())
    row = snap['runners'][0]
    state = snap['states'][row['id']]
    snap['runners'] = [dict(row, id=f'runner_{i:016x}') for i in range(128)]
    snap['states'] = {r['id']: deepcopy(state) for r in snap['runners']}
    return snap


def test_unchanged_blocks_avoid_per_row_carry_checks(env, monkeypatch):
    cfg, path, *_ = env
    snap = fleet(cfg, path)
    service = svc(cfg, path, 1001)
    first, carry = service._inventory(snap, None, copy_rows=False, carry=None, now=1001)
    newer = deepcopy(snap)
    for state in newer['states'].values():
        state.update(cycle='collection_new', processed_at=1002, healthy_cycles=9)
    calls = []
    same = S._same_state
    monkeypatch.setattr(S, '_same_state', lambda *a: calls.append(1) or same(*a))
    actual, built = service._inventory(newer, None, copy_rows=False, carry=carry, now=1002)
    expected = service._inventory(newer, None)
    assert comparable(actual) == comparable(expected)
    assert built.computed == 0
    assert calls == [], 'unchanged blocks must avoid Python per-row carry checks'


def test_scattered_changes_compare_each_state_only_once(env, monkeypatch):
    cfg, path, *_ = env
    snap = fleet(cfg, path)
    service = svc(cfg, path, 1001)
    _, carry = service._inventory(snap, None, copy_rows=False, carry=None, now=1001)
    newer = deepcopy(snap)
    for state in newer['states'].values():
        state.update(cycle='collection_new', processed_at=1002)
    for row in newer['runners'][::20]:
        row['labels'] = ['changed']
    calls = []
    same = S._same_state
    monkeypatch.setattr(S, '_same_state', lambda *a: calls.append(1) or same(*a))
    actual, built = service._inventory(newer, None, copy_rows=False, carry=carry, now=1002)
    assert comparable(actual) == comparable(service._inventory(newer, None))
    assert built.computed == len(newer['runners'][::20])
    assert calls == [], 'dirty blocks must not repeat normalized state comparisons'


@pytest.mark.parametrize('change', ['source', 'state', 'host', 'order', 'remove', 'add', 'rewind', 'expiry', 'ceiling', 'maintenance'])
def test_block_carry_matches_full_build(env, change):
    cfg, path, *_ = env
    snap = fleet(cfg, path)
    service = svc(cfg, path, 1001)
    _, carry = service._inventory(snap, None, copy_rows=False, carry=None, now=1001)
    newer = deepcopy(snap)
    at, overlay = 1002, None
    rid = newer['runners'][64]['id']
    if change == 'source': newer['runners'][64]['labels'] = ['changed']
    if change == 'state': newer['states'][rid]['activity'] = 'busy'
    if change == 'host':
        hid = newer['runners'][64]['host_id']
        newer.setdefault('host_results', {})[hid] = dict(at=999, management='unreachable', facts={})
    if change == 'order': newer['runners'].reverse()
    if change == 'remove': newer['runners'].pop(64)
    if change == 'add':
        row = dict(newer['runners'][0], id='runner_added')
        newer['runners'].append(row)
        newer['states'][row['id']] = deepcopy(newer['states'][rid])
    if change == 'rewind': at = 999
    if change == 'expiry': at = 1000 + cfg['thresholds']['inventory_stale_seconds'] + .5
    if change == 'ceiling': at = 1301
    if change == 'maintenance':
        import json
        overlay = [(cfg['hosts'][0]['resource_id'], json.dumps(dict(state='active', reason='test', expires_at='1970-01-01T00:20:00Z')))]
    service = svc(cfg, path, at)
    actual, built = service._inventory(newer, overlay, copy_rows=False, carry=carry, now=at)
    assert comparable(actual) == comparable(service._inventory(newer, overlay))
    if change in ('rewind', 'ceiling'): assert built.computed == len(newer['runners'])


def test_public_memo_cannot_extend_original_row_ceiling(env):
    cfg, path, *_ = env
    svc(cfg, path, 2000).inventory()
    svc(cfg, path, 2289).inventory()
    assert S._RESULTS[str(path)].carry.computed == 0
    svc(cfg, path, 2300).inventory()
    assert S._RESULTS[str(path)].carry.computed > 0


def test_multigeneration_mixed_age_blocks_keep_original_deadlines(env):
    cfg, path, *_ = env
    snap = fleet(cfg, path)
    service = svc(cfg, path, 2000)
    _, carry = service._inventory(snap, None, copy_rows=False, carry=None, now=2000)
    for at in (2100, 2200, 2299, 2300):
        newer = deepcopy(snap)
        if at >= 2200: newer['runners'][0]['labels'] = ['changed']
        for state in newer['states'].values(): state['cycle'] = f'cycle_{at}'
        service = svc(cfg, path, at)
        actual, carry = service._inventory(newer, None, copy_rows=False, carry=carry, now=at)
        assert comparable(actual) == comparable(service._inventory(newer, None))
        if at == 2300: assert carry.computed == 127


@pytest.mark.parametrize('change', ['missing', 'unknown', 'duplicate'])
def test_state_shape_and_duplicate_fallback(env, change):
    cfg, path, *_ = env
    snap = fleet(cfg, path)
    service = svc(cfg, path, 1001)
    _, carry = service._inventory(snap, None, copy_rows=False, carry=None, now=1001)
    newer = deepcopy(snap)
    state = newer['states'][newer['runners'][64]['id']]
    if change == 'missing': state.pop('offline_count')
    if change == 'unknown': state['unknown_field'] = True
    if change == 'duplicate': newer['runners'].insert(63, deepcopy(newer['runners'][64]))
    if change == 'unknown':
        with pytest.raises(TypeError):
            service._inventory(newer, None, copy_rows=False, carry=carry, now=1002)
        return
    actual, _ = service._inventory(newer, None, copy_rows=False, carry=carry, now=1002)
    assert comparable(actual) == comparable(service._inventory(newer, None))

