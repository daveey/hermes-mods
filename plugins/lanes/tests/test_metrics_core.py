"""Real dispatcher samples -> real production hub view, with core accounting parity."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

base = pytest.importorskip('tests.hermes_cli.test_kanban_lanes')
samples = pytest.importorskip('hermes_cli.kanban_lane_samples')
from hermes_cli import kanban_db as kb, kanban_lanes as lanes
from hermes_cli.kanban_db_dispatch import dispatch_once

conn = base.conn
configured = base.configured
person = base.person


def api_module():
    spec = importlib.util.spec_from_file_location('lane_metrics_core_api', Path(__file__).parents[1] / 'dashboard/plugin_api.py')
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    obj._projector_enabled = False
    return obj


@pytest.mark.parametrize('running,cap,ready,state', [
    (0, 2, 0, 'empty'), (1, 2, 0, 'partial'), (1, 1, 0, 'full'), (1, 1, 1, 'rejecting')])
def test_real_dispatcher_to_projected_view(conn, configured, running, cap, ready, state):
    mapping = copy.deepcopy(base.LANES)
    mapping['babybear']['max_workers'] = cap
    configured(mapping)
    for i in range(running):
        assert kb.claim_task(conn, base._ready_in(conn, 'babybear', f'running {i}'))
    for i in range(ready):
        base._ready_in(conn, 'babybear', f'waiting {i}')
    result = dispatch_once(conn, spawn_fn=lambda *_a, **_k: pytest.fail('unexpected spawn'),
                           max_spawn=2, max_in_progress=10, reconcile_orphans=False)
    assert not result.spawned
    api = api_module()
    try:
        body = api._lane_view(24., '24h', False)
        row = body['lanes']['babybear']
        assert row['ticks']['window'] == '24h'
        assert row['ticks']['bucket_s'] == 1800
        assert len(row['ticks']['cells']) == 48
        assert row['ticks']['cells'][-1]['state'] == state
        assert row['ticks']['cells'][-1]['rejected'] == ready
        assert row['limits']['max_workers'] == cap
        assert row['spend'] == dict(hour=0., day=0., week=0.)
    finally:
        if api._observations.cache_info().currsize:
            api._observations().join(2)


def test_spend_matches_real_core_usage_for_each_window(conn, configured):
    api = api_module()
    now = 1000000
    tid = base._ready_in(conn, 'babybear', 'usage')
    assert kb.claim_task(conn, tid)
    run = conn.execute('SELECT id FROM task_runs WHERE task_id=?', (tid,)).fetchone()[0]
    # The core event append seam preserves real auxiliary tables and triggers.
    for age, cost in [(10, 2.1), (10, 3.2), (3700, 8.5), (86400, 10.), (604799, 11.)]:
        kb._append_event(conn, tid, 'worker_result', payload={'usage': {'cost_usd': cost}}, run_id=run)
        conn.execute('UPDATE task_events SET created_at=? WHERE id=(SELECT MAX(id) FROM task_events)', (now-age,))
    pol = lanes.policy()
    value = api._sibling('metrics').spend(conn, pol, now=now)
    for key, seconds in [('hour', 3600), ('day', 86400), ('week', 604800)]:
        real = lanes.usage(conn, pol, window_s=seconds, now=now)
        for name in pol.lanes:
            assert value[name][key] == real[name]['cost_usd']
    assert value['babybear'] == dict(hour=3.2, day=8.5, week=11.)


def test_fleet_capacity_uses_real_core_freshness_not_last_good(monkeypatch):
    """An outage in core retains discovery but must remove authoritative lane capacity."""
    from types import SimpleNamespace
    from hermes_cli import kanban_worker_node as node

    clock, calls, failing = [0.], [], [False]
    def fetch():
        calls.append(clock[0])
        if failing[0]:
            raise OSError('fixture registry unavailable')
        return {'machines': [
            {'name': 'active', 'state': 'active', 'max_card_runs': 6},
            {'name': 'held', 'state': 'active', 'hold': True, 'max_card_runs': 100},
            {'name': 'retired', 'state': 'retired', 'max_card_runs': 100},
        ]}
    # Replace core cache bindings, not the real process cache or Board identity.
    monkeypatch.setattr(node, '_REGISTRY', dict(at=None, value=None, retry=False, ok_at=None))
    monkeypatch.setattr(node, '_FETCH', fetch)
    monkeypatch.setattr(node, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    spec = importlib.util.spec_from_file_location('lane_metrics_core_registry', Path(__file__).parents[1] / 'dashboard/metrics.py')
    metrics = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(metrics)
    assert metrics._fetch_registry_slots() == 6
    assert calls == [0.]
    clock[0] = node.REGISTRY_TTL_S
    failing[0] = True
    assert node.registry()['machines']['active']['max_card_runs'] == 6
    assert metrics._fetch_registry_slots() is None
    assert calls == [0., node.REGISTRY_TTL_S]  # require_fresh obeys core outage retry TTL
    clock[0] += node.REGISTRY_RETRY_S
    failing[0] = False
    assert metrics._fetch_registry_slots() == 6


@pytest.mark.parametrize('missing_first', [False, True])
def test_real_dispatch_with_no_table_sibling_preserves_source_bands(conn, configured, tmp_path, missing_first):
    from contextlib import closing
    from hermes_cli import kanban_db_connect as kbc
    mapping = copy.deepcopy(base.LANES)
    mapping['babybear']['max_workers'] = 1
    configured(mapping)
    assert kb.claim_task(conn, base._ready_in(conn, 'babybear', 'running'))
    base._ready_in(conn, 'babybear', 'waiting')
    dispatch_once(conn, spawn_fn=lambda *_a, **_k: pytest.fail('unexpected spawn'),
                  max_spawn=2, max_in_progress=10, reconcile_orphans=False)
    api = api_module()
    metrics = api._sibling('metrics')
    pol = lanes.policy()
    now = __import__('time').time()
    healthy = metrics.project(conn, conn, pol, now=now)
    metrics.qualify(healthy, 'default')
    with closing(kbc.connect(tmp_path / 'unserved.db')) as missing:
        assert not missing.execute("SELECT 1 FROM sqlite_master WHERE name='lane_samples'").fetchone()
        absent = metrics.project(missing, missing, pol, now=now)
    metrics.qualify(absent, 'askeve')
    a, b = (absent, healthy) if missing_first else (healthy, absent)
    merged = metrics.merge(a, b)
    # Repeat reduction must retain all provenance, not nest or drop a source.
    absent2 = copy.deepcopy(absent)
    metrics.qualify(absent2, 'unserved')
    merged = metrics.merge(merged, absent2)
    presented = metrics.present({'lanes': merged}, 6)
    cell = presented['lanes']['babybear']['ticks']['cells'][-1]
    assert cell['running'] is None and cell['rejected'] is None
    parts = {c['source']: c for c in cell['components']}
    assert set(parts) == {'default', 'askeve', 'unserved'}
    assert parts['default']['state'] == 'rejecting'
    assert parts['default']['running'] == 1 and parts['default']['rejected'] >= 1
    assert parts['askeve']['state'] == 'unknown' and parts['askeve']['reason'] == 'no samples'
    assert '_tick_sources' not in presented['lanes']['babybear']
    assert metrics.merge(absent, absent2)['babybear']['ticks'] is None
    import shutil
    import subprocess
    node_tests = Path(__file__).resolve().parents[3] / 'desktop-plugins/lanes/tests'
    if not shutil.which('node') or not (node_tests / 'node_modules/jsdom').exists():
        pytest.skip('pinned Node DOM dependencies unavailable')
    projected = api._sibling('view').project({'configured': True, 'lanes': merged},
                                            {'configured': True, 'cards': [], 'lanes': {}}, True)
    presented = metrics.present(projected, 6)
    result = subprocess.run(['node', str(node_tests / 'render-source-ticks.mjs')],
                            input=json.dumps(presented), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
