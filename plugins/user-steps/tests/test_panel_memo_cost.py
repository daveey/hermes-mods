"""Production-sized successful memo path: no global JSON decode on each chat."""
from contextlib import closing
import json
import sqlite3
import time

import pytest

from test_panel_replica_membership import replica_panel  # noqa: F401
from test_panel_membership import sources  # noqa: F401


def test_production_inventory_memo_cost_and_same_version_changes(replica_panel, monkeypatch):
    p = replica_panel
    p.s.obligation()
    p.link()
    # Hub profile: 26,794 task publications / 133 MB of JSON. Include nested
    # payloads, not cheap tiny records, and unrelated namespaces/chat links.
    count = 27000
    body = list(range(1200))
    def rows():
        for i in range(count):
            tid = f't_scale_{i}'
            task = dict(id=tid, status='done', created_at=i,
                        session_id='other-chat', fixture_payload=body)
            yield ('other', 'tasks', f'other/default/{tid}', json.dumps(task), 1)
    with closing(sqlite3.connect(p.s.paths['replica'])) as c, c:
        c.executemany('INSERT INTO replica(gw,coll,key,data,version) VALUES (?,?,?,?,?)', rows())
    p.run()  # fill compact inventory and canonical successful-owner memo
    assert len(p.calls) == 1
    # Use the actual loaded capture module, not a new module with an empty cache.
    loader = p.mirror.remote.membership_capture.__func__.__globals__['load_file']
    from test_withdrawal import ROOT
    module = loader(ROOT / 'panel_membership.py')
    original = module._object
    decodes = []
    def decode(raw):
        decodes.append(len(raw))
        return original(raw)
    monkeypatch.setattr(module, '_object', decode)
    costs = []
    for _ in range(3):
        start = time.thread_time()
        p.run()
        costs.append(time.thread_time() - start)
        assert p.mirror.panel_remote_unchanged
    # Deterministic source-work bound is primary; CPU ceiling catches a future
    # full-body scan/serialization regression without CI scheduling wall jitter.
    assert len(decodes) < 100, len(decodes)
    assert min(costs) < .9, costs
    assert len(p.calls) == 1
    assert len(module._INVENTORY) <= module._INVENTORY_MAX

    from test_panel_timing import timing, checked
    mirror_module = p.mirror.reconcile_panel.__func__.__globals__
    # Base-referenced control includes disabled-trace hot-path overhead too.
    # Freeze the old helper, rather than comparing two modes of new code only.
    import ast
    import inspect
    import statistics
    import types
    from inventory_base_reference import _inventory as base_inventory
    source = inspect.getsource(module._inventory).replace(
        '_inventory_digest(raw)',
        'hashlib.sha256(raw if isinstance(raw, bytes) else raw.encode()).digest()')
    assert ast.dump(ast.parse(source)) == ast.dump(
        ast.parse(inspect.getsource(base_inventory)))
    baseline = types.FunctionType(base_inventory.__code__, module.__dict__)
    current = module._inventory
    base_costs, traced_costs = [], []
    for _ in range(7):
        with monkeypatch.context() as control:
            control.setattr(module, '_inventory', baseline)
            start = time.thread_time()
            p.run()
            base_costs.append(time.thread_time() - start)
            assert p.mirror.panel_remote_unchanged
        assert module._inventory is current
        trace = timing.Recorder()
        start = time.thread_time()
        with mirror_module['panel_trace'](trace):
            p.run()
        traced_costs.append(time.thread_time() - start)
        assert p.mirror.panel_remote_unchanged
        out = checked(trace)
        assert out['stages']['inventory_hash_sample']['calls'] >= count // 64
    print('TIMING_OVERHEAD', dict(base_inventory_cpu_s=base_costs, traced_cpu_s=traced_costs))
    assert statistics.median(traced_costs) < statistics.median(base_costs) + .05, (base_costs, traced_costs)

    # Publication version and global DB identity are NOT trusted cache keys.
    # Changing a previously unrelated payload at the same version can add this
    # chat to membership, and malformed JSON/identity must still fail closed.
    key = 'other/default/t_scale_0'
    with closing(sqlite3.connect(p.s.paths['replica'])) as c, c:
        c.execute("UPDATE replica SET data=? WHERE key=?", ('{', key))
    assert p.stamp() is None
    p.run()
    assert not p.mirror.panel_remote_unchanged


@pytest.mark.parametrize('raw', [
    b'\xef\xbb\xbf{"id":"t_odd"}',
    b'{"id":"t_odd","title":"\xed\xa0\x80"}',
])
def test_text_inventory_preserves_strict_decode_fence(replica_panel, raw):
    p = replica_panel
    p.s.obligation()
    p.link()
    assert p.stamp() is not None
    # BLOBs historically reach json.loads(bytes); TEXTs reach strict SQLite
    # UTF-8 decoding then json.loads(str). Warm a valid BLOB cache entry first
    # so identical bytes cannot hide the stronger TEXT fence after a type change.
    p.s.sql('replica', 'INSERT INTO replica VALUES (?,?,?,?,1)',
            ('remote', 'tasks', 'default/default/t_odd', raw))
    assert p.stamp() is not None
    p.s.sql('replica', "UPDATE replica SET data=CAST(data AS TEXT) WHERE key=?",
            ('default/default/t_odd',))
    for _ in range(2):
        assert p.stamp() is None
        p.run()
        assert not p.mirror.panel_remote_unchanged


def test_same_version_inventory_arrival_invalidates(replica_panel):
    p = replica_panel
    p.s.obligation()
    p.link()
    p.s.put(p.s.task('t_arrival', session='other-chat'))
    before = p.stamp()
    assert before is not None
    p.run()
    p.run()
    assert p.mirror.panel_remote_unchanged
    p.s.put(p.s.task('t_arrival'))  # same version, newly links this chat
    assert p.stamp() != before
    p.run()
    assert not p.mirror.panel_remote_unchanged


def test_inventory_decoder_cache_is_bounded_and_scope_qualified(replica_panel, monkeypatch):
    p = replica_panel
    from test_withdrawal import ROOT
    loader = p.mirror.remote.membership_capture.__func__.__globals__['load_file']
    module = loader(ROOT / 'panel_membership.py')
    monkeypatch.setattr(module, '_INVENTORY_MAX', 3)
    module._INVENTORY.clear()
    def links(task, gateway, profile):
        return [gateway + '/' + profile]
    raw = json.dumps(dict(id='t', created_at=1))
    a = module._inventory(raw, 'default/t', 'one', 'default', links)
    b = module._inventory(raw, 'default/t', 'two', 'default', links)
    assert a[2] != b[2]
    for i in range(10):
        module._inventory(json.dumps(dict(id=str(i))), 'default/' + str(i), 'one', 'default', links)
    assert len(module._INVENTORY) == 3
    # Cached values contain no full task bodies and no database handles.
    assert all(isinstance(v[2], frozenset) for v in module._INVENTORY.values())
