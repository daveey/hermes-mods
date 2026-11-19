"""Card L equivalence: over random snapshot writes and clock steps, the memoized inventory (snapshot memo, result
memo, per-row carry-over) always equals a memo-free build of the same snapshot at the same instant."""
import json
import random
import sqlite3
from copy import deepcopy

import pytest

from plugins.runners import service as S
from plugins.runners.collector import iso
from plugins.runners.service import Service
from test_lifecycle_projection import build

RUNNERS = 12


def comparable(inv):
    inv = deepcopy(inv)
    inv['meta'].pop('request_id'); inv['meta'].pop('generated_at')
    return inv


def setup_fleet(tmp_path):
    cfg, svc, snap = build(tmp_path)
    path = svc.path
    base, state = snap['runners'][0], snap['states'][snap['runners'][0]['id']]
    for i in range(1, RUNNERS):  # unbound rows on the same host, so host changes reach them too
        rid = base['id'][:-4] + f'{i + 1:04x}'
        snap['runners'].append(dict(base, id=rid, provider_runner_id=str(100 + i)))
        snap['states'][rid] = dict(state, at=1000 - 7 * i)
    # Duplicate IDs intentionally disable carry; this fleet must exercise reuse.
    assert len(snap['runners']) == len({row['id'] for row in snap['runners']}) == RUNNERS
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE IF NOT EXISTS repair_domain_maintenance (entity_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    return cfg, path, snap


def write(path, snap):
    with sqlite3.connect(path) as c:
        c.execute("UPDATE collector_cache SET payload=? WHERE key='snapshot'", (json.dumps(snap),))


def reference(cfg, path, now):
    """A memo-free build: decode the row and read the overlay directly."""
    with sqlite3.connect(path) as c:
        snap = json.loads(c.execute("SELECT payload FROM collector_cache WHERE key='snapshot'").fetchone()[0])
        overlay = tuple(c.execute('SELECT entity_id,payload FROM repair_domain_maintenance ORDER BY entity_id'))
    return Service(path, deepcopy(cfg), clock=lambda: now)._inventory(snap, overlay)


def mutate(rng, snap, cfg, path, now):
    kind = rng.choice(['tick', 'tick', 'state', 'runner', 'host', 'evidence', 'maint', 'unmaint', 'snapshot',
                       'provenance', 'none'])
    rid = rng.choice(snap['runners'])['id']
    hid = cfg['hosts'][0]['id']
    host = snap['host_results'][hid]
    if kind == 'tick':  # the hub's common case: only the collector's per-cycle bookkeeping moves
        cycle = f'collection_{int(now * 1000):032x}'
        for st in snap['states'].values():
            st['processed_at'] = now
            if rng.random() < 0.8:
                st.update(cycle=cycle, absent_cycles=st.get('absent_cycles', 0) + 1,
                          healthy_cycles=rng.randrange(3))
    elif kind == 'state':
        snap['states'][rid].update(at=now - rng.choice([0, 5, 119, 120, 121, 200]),
                                   registration=rng.choice(['online', 'offline', 'missing']))
    elif kind == 'runner':
        row = next(r for r in snap['runners'] if r['id'] == rid)
        row['labels'] = [f'l{rng.randrange(3)}']
    elif kind == 'host':
        host['at'] = now - rng.choice([0, 10, 179, 180, 181, 400])
        host['facts']['disk_free_bytes'] = rng.choice([1, 10, 100]) * 1024**3
    elif kind == 'evidence':
        for items in host['facts'].get('operational_evidence', {}).values():
            for item in items:
                item['observed_at'] = now - rng.choice([0, 30, 179, 181])
                item['state'] = rng.choice(['held', 'clear'])
    elif kind == 'snapshot':
        snap['at'] = now - rng.choice([0, 60, 119, 121])
    elif kind == 'provenance':  # the collector's own provenance expiries are compared with the clock too
        snap['at'] = now
        assert snap.get('provenance')
        for item in snap['provenance']:
            item.update(state='fresh', expires_at=iso(now + rng.choice([0.5, 2, 15, 45])))
    if kind in ('maint', 'unmaint'):
        with sqlite3.connect(path) as c:
            if kind == 'maint':
                m = dict(state='active', reason='test', expires_at=iso(now + rng.choice([1, 15, 45])))
                c.execute('INSERT OR REPLACE INTO repair_domain_maintenance VALUES (?,?)',
                          (cfg['hosts'][0]['resource_id'], json.dumps(m)))
            else:
                c.execute('DELETE FROM repair_domain_maintenance')
    elif kind != 'none':
        write(path, snap)


@pytest.mark.parametrize('seed', range(16))
def test_memoized_inventory_equals_a_fresh_build(tmp_path, monkeypatch, seed):
    rng = random.Random(seed)
    cfg, path, snap = setup_fleet(tmp_path)
    write(path, snap)
    monkeypatch.setattr(S, '_READS', {}); monkeypatch.setattr(S, '_RESULTS', {})
    now, reused = 1001.0, 0
    for step in range(120):
        if rng.random() < 0.6:
            mutate(rng, snap, cfg, path, now)
        # small steps, exact threshold instants and long jumps
        now += rng.choice([0, 0.4, 1, 3, 7, 30, 61])
        if rng.random() < 0.1:
            now = rng.choice([snap['at'] + 120, snap['at'] + 120 + 1e-4, snap['at'] + 180])
        shared = rng.random() < 0.5
        got = Service(path, deepcopy(cfg), clock=lambda: now).inventory(shared=shared)
        want = reference(cfg, path, now)
        assert comparable(got) == comparable(want), (seed, step, now)
        memo = S._RESULTS[str(path)]
        reused += len(memo.carry.rows) - memo.carry.computed
    assert reused > 0  # the carry-over was exercised
