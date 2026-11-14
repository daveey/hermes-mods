"""Change-keyed inventory (design t_01a1d563324d7e8e card L): the decoded snapshot and the inventory result are
reused while the state file's key, the config and the clock window are unchanged; any write, a rewound or deleted
snapshot, a time-threshold crossing and the REUSE_MAX_S ceiling compute it again."""
import json
import shutil
import sqlite3
from copy import deepcopy

import pytest

from plugins.runners import service as S
from plugins.runners.collector import Collector
from plugins.runners.service import Service
from plugins.runners.store import Store
from test_collector import F, Adapter, config


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = config(); cfg['hosts'] = [deepcopy(F['Host'])]
    path = tmp_path / 'state.sqlite'
    with Store(path) as store:
        Collector(store, cfg, Adapter(), clock=lambda: 1000, host_probe=lambda host: {}).tick()
    builds, decodes = [], []
    real_inventory, real_decode = Service._inventory, S._decode_snapshot
    monkeypatch.setattr(Service, '_inventory', lambda self, *a, **kw: builds.append(1) or real_inventory(self, *a, **kw))
    monkeypatch.setattr(S, '_decode_snapshot', lambda *a, **kw: decodes.append(1) or real_decode(*a, **kw))
    return cfg, path, builds, decodes


def svc(cfg, path, at):
    return Service(path, deepcopy(cfg), clock=lambda: at)  # dashboard: a fresh Service and config copy per call


def comparable(inv):
    inv = deepcopy(inv)
    inv['meta'].pop('request_id'); inv['meta'].pop('generated_at')
    return inv


def test_unchanged_inputs_reuse_the_decode_and_the_result(env):
    cfg, path, builds, decodes = env
    first = svc(cfg, path, 1001).inventory()
    n = len(decodes)
    assert n == 1  # exercise the accelerated decoder too, not only stdlib fallback
    second = svc(cfg, path, 1002).inventory()
    assert len(builds) == 1 and len(decodes) == n  # neither decoded nor built again
    assert comparable(first) == comparable(second)
    assert first['meta']['request_id'] != second['meta']['request_id']
    assert second['meta']['generated_at'] != first['meta']['generated_at']


def test_callers_get_independent_copies(env):
    cfg, path, builds, decodes = env
    svc(cfg, path, 1001).inventory()['data']['runners'][0]['id'] = 'mutated'
    a = svc(cfg, path, 1001).inventory()
    a['data']['runners'][0]['labels'] = ['mutated']
    b = svc(cfg, path, 1001).inventory()
    assert b['data']['runners'][0]['id'] == F['Runner']['id'] and b['data']['runners'][0]['labels'] != ['mutated']
    shared = svc(cfg, path, 1001).inventory(shared=True)
    assert comparable(shared) == comparable(b) and len(builds) == 1


def test_a_collector_write_is_picked_up(env):
    cfg, path, builds, decodes = env
    before = svc(cfg, path, 1001).inventory()
    with Store(path) as store:
        Collector(store, cfg, Adapter(), clock=lambda: 1030, host_probe=lambda host: {}).tick()
    after = svc(cfg, path, 1031).inventory()
    assert len(builds) == 2 and after['meta']['observed_at'] != before['meta']['observed_at']


def test_a_rewrite_with_the_same_snapshot_skips_the_decode(env):
    cfg, path, builds, decodes = env
    svc(cfg, path, 1001).inventory()
    n = len(decodes)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE collector_cache SET payload=payload WHERE key='snapshot'")
    svc(cfg, path, 1001).inventory()
    assert len(decodes) == n and len(builds) == 1  # the key moved, the text did not


def test_a_threshold_crossing_recomputes(env):
    cfg, path, builds, decodes = env
    ttl = cfg['thresholds']['inventory_stale_seconds']
    assert svc(cfg, path, 1001).inventory()['meta']['freshness'] == 'fresh'
    assert svc(cfg, path, 1000 + ttl).inventory()['meta']['freshness'] == 'fresh'  # `<=`: still fresh at X+D
    stale = svc(cfg, path, 1000 + ttl + 0.5).inventory()
    assert stale['meta']['freshness'] == 'stale' and len(builds) >= 2


def test_the_ceiling_recomputes_without_any_change(env, monkeypatch):
    cfg, path, builds, decodes = env
    monkeypatch.setattr(S, 'REUSE_MAX_S', 5.0)
    svc(cfg, path, 1001).inventory()
    svc(cfg, path, 1005.9).inventory()
    assert len(builds) == 1
    svc(cfg, path, 1006).inventory()
    assert len(builds) == 2


def test_a_clock_step_back_recomputes(env):
    cfg, path, builds, decodes = env
    svc(cfg, path, 1010).inventory()
    svc(cfg, path, 1005).inventory()
    assert len(builds) == 2


def test_a_config_change_recomputes(env):
    cfg, path, builds, decodes = env
    svc(cfg, path, 1001).inventory()
    changed = deepcopy(cfg); changed['thresholds']['inventory_stale_seconds'] = 1
    assert svc(changed, path, 1002).inventory()['meta']['freshness'] == 'stale'
    assert len(builds) == 2


def test_a_deleted_snapshot_falls_back(env):
    cfg, path, builds, decodes = env
    svc(cfg, path, 1001).inventory()
    with sqlite3.connect(path) as c:
        c.execute("DELETE FROM collector_cache WHERE key='snapshot'")
    assert svc(cfg, path, 1001).inventory()['data']['state'] == 'loading'


def test_a_rewound_state_file_falls_back(env, tmp_path):
    cfg, path, builds, decodes = env
    shutil.copy(path, tmp_path / 'older.sqlite')
    with Store(path) as store:
        Collector(store, cfg, Adapter(), clock=lambda: 1030, host_probe=lambda host: {}).tick()
    newer = svc(cfg, path, 1031).inventory()
    for suffix in ('-wal', '-shm'):
        (tmp_path / ('state.sqlite' + suffix)).unlink(missing_ok=True)
    (tmp_path / 'older.sqlite').replace(path)  # a restored backup: another inode, an older snapshot
    older = svc(cfg, path, 1031).inventory()
    assert older['meta']['observed_at'] != newer['meta']['observed_at']


def test_a_maintenance_write_is_picked_up(env):
    cfg, path, builds, decodes = env
    host = cfg['hosts'][0]
    assert svc(cfg, path, 1001).inventory()['data']['hosts'][0]['maintenance']['state'] == host['maintenance']['state']
    active = dict(state='active', reason='test', expires_at='1970-01-01T00:20:00Z')
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE IF NOT EXISTS repair_domain_maintenance (entity_id TEXT PRIMARY KEY, payload TEXT)')
        c.execute('INSERT OR REPLACE INTO repair_domain_maintenance VALUES (?,?)', (host['resource_id'], json.dumps(active)))
    assert svc(cfg, path, 1001).inventory()['data']['hosts'][0]['maintenance']['state'] == 'active'
    # its expiry (1200) is a flip instant: inactive right after it, without any write
    assert svc(cfg, path, 1200.5).inventory()['data']['hosts'][0]['maintenance']['state'] == 'inactive'
