"""plugin.runners.fleet pushes (dashboard/plugin_api.py FleetPusher): on change, heartbeat, interest, size bound."""
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip('fastapi')
API = Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py'


@pytest.fixture
def module():
    spec = importlib.util.spec_from_file_location('runners_push_api_under_test', API)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make(module, fleets):
    clock, sent = [1000.0], []
    it = iter(fleets)
    p = module.FleetPusher(sent.append, lambda: ('default', next(it)), clock=lambda: clock[0], wall=lambda: 5.0)
    return p, sent, clock


def fleet(busy, at='2026-11-20T00:00:00Z'):
    return dict(contract_version=1, profile='default', generated_at=at, ci={}, gateways=dict(machines=[dict(busy=busy)]))


def test_pushes_on_change_heartbeats_otherwise_and_stops_without_interest(module):
    p, sent, clock = make(module, [fleet(1), fleet(1, at='later'), fleet(2), fleet(2), fleet(2)])
    assert p.step() is None and sent == []  # nobody read the fleet: nothing built
    p.note()
    assert p.step()['fleet']['gateways']['machines'] == [dict(busy=1)]
    clock[0] += 15
    assert p.step() is None  # same fleet (only generated_at moved): no push
    clock[0] += 15
    changed = p.step()
    assert changed['fleet']['gateways']['machines'] == [dict(busy=2)] and changed['profile'] == 'default'
    clock[0] += module.FLEET_HEARTBEAT_S
    beat = p.step()
    assert beat['heartbeat'] is True and 'fleet' not in beat and beat['hash'] == changed['hash']
    assert [b['seq'] for b in sent] == [1, 2, 3] and {b['feed'] for b in sent} == {module.FEED_ID}
    clock[0] += module.FLEET_INTEREST_S
    assert p.step() is None and len(sent) == 3


def test_payload_is_json_safe_and_size_bounded(module, monkeypatch):
    p, sent, _ = make(module, [dict(fleet(1), load=float('nan')), fleet(2)])
    p.note()
    json.loads(json.dumps(p.step(), allow_nan=False))
    monkeypatch.setattr(module, 'FLEET_PUSH_MAX_BYTES', 100)
    big = p.step()
    assert big['fleet'] is None and big['changed'] is True


def test_no_thread_without_a_gateway_transport(module):
    p, _, _ = make(module, [])
    p.note()
    assert p.thread is None


def realistic(now_s, cpu=0.31, busy=1):
    """A fleet as plugin_api builds it: fleet.gateway_workers + with_worker_events, so every time-derived field
    (generated_at, events_since, last_seen, load.at) moves with the clock."""
    import sys
    root = Path(__file__).resolve().parents[3]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from plugins.runners import fleet as F
    now_ms = now_s * 1000
    registry = dict(me='hub', machines=dict(
        hub=dict(state='active', max_card_runs=4, last_seen=now_ms - 5000, labels=['linux'],
                 metrics=dict(at=now_ms - 5000, cpu_load_1m=cpu * 8, cpus=8, mem_used_pct=40.0, disk_free_gb=100.0))))
    leases = [dict(holder='hub', board='default', task=f't_{i:04d}aaaa', title='Card', started_at=1_799_999_000 + i)
              for i in range(busy)]
    section = F.with_worker_events(F.gateway_workers(registry, leases, now_ms, registry_age_s=10), Path('/nonexistent'),
                                   now_s)
    return dict(contract_version=1, profile='default', generated_at=str(now_s), gateways=section,
                ci=dict(state='ready', observed_at=now_s - 3, machines=[]))


def test_a_realistic_fleet_rebuilt_every_check_is_not_pushed_until_it_changes(module):
    t0 = 1_800_000_000
    fleets = [realistic(t0), realistic(t0 + 15), realistic(t0 + 30, cpu=0.5), realistic(t0 + 45, busy=2),
              realistic(t0 + 60, busy=2, cpu=0.7)]
    # A minute later with no machine report in between: only the build clock moved.
    later = realistic(t0 + 60, busy=2, cpu=0.7)
    later.update(generated_at=str(t0 + 120))
    later['gateways'] = dict(later['gateways'], events_since=t0 + 120 - 86400)
    fleets.append(later)
    assert fleets[0]['gateways']['events_since'] != fleets[1]['gateways']['events_since']  # time-derived
    p, sent, clock = make(module, fleets)
    p.note()
    assert p.step()['fleet']  # first: the whole fleet
    clock[0] += 15
    assert p.step() is None  # rebuilt 15 s later: only clock fields moved, nothing pushed
    clock[0] += 15
    assert p.step() is None  # only the load moved: waits for the minute
    clock[0] += 15
    assert p.step()['fleet']['gateways']['machines'][0]['running'][1]['task'] == 't_0001aaaa'  # a card started: at once
    clock[0] += module.FLEET_HEARTBEAT_S
    again = p.step()
    assert again['fleet']['gateways']['machines'][0]['load'] is not None  # load moved: sent in place of a heartbeat
    clock[0] += module.FLEET_HEARTBEAT_S
    beat = p.step()  # only clock fields moved since: a heartbeat naming the fleet clients hold
    assert beat['heartbeat'] is True and beat['hash'] == again['hash'] and 'fleet' not in beat
    assert len(sent) == 4


def test_lifespan_shutdown_stops_the_fleet_pusher(module):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(module.router)
    with TestClient(app):
        p = module.fleet_pusher()
        assert not p.stopped.is_set()
    assert p.stopped.is_set() and 'p' not in module._fleet_pusher
