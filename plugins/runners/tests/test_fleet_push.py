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
