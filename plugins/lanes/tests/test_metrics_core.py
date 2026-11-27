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
