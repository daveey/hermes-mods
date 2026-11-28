"""Top-strip contracts use canonical producer shapes, never sampled cards."""
import asyncio
import copy
import importlib.util
import sys
from types import SimpleNamespace

import pytest

from test_snapshot_and_stream import api, fixture_adapter, run


def telemetry_adapter():
    adapter = fixture_adapter()
    adapter.hub = lambda: {'status': {'state': 'up', 'since': 10},
                          'history': {'start': 0, 'now': 86400, 'events': [
                              {'ts': 100, 'state': 'down'}, {'ts': 200, 'state': 'up'}]}}
    adapter.fleet = lambda: {'gateways': {'state': 'ready', 'machines': [
        {'state': 'active', 'slots': 3, 'running': [{'task': 'a'}]},
        {'state': 'launching', 'slots': 6, 'running': []}]},
        'ci': {'state': 'ready', 'machines': [{'slots': [
            {'state': 'busy'}, {'state': 'idle'}, {'state': 'offline'}, {'state': 'unknown'}]}]}}
    adapter.subscription = lambda: {'pools': [{'provider': 'pool-a', 'accounts': 2,
        'runway_s': 7200, 'read_at': 100, 'next_reset_at': 8000, 'pacing': 'paced'}]}
    return adapter


def test_canonical_telemetry_shapes_and_no_estimated_launch_pips():
    telemetry = run(telemetry_adapter())['telemetry']
    assert telemetry['hub']['band']['slots'] == [
        {'start': 0, 'end': 100, 'state': 'unknown'},
        {'start': 100, 'end': 200, 'state': 'down'},
        {'start': 200, 'end': 86400, 'state': 'up'}]
    assert telemetry['fleet']['label'] == '1 server'
    assert telemetry['fleet']['slots'] == [
        {'busy': True, 'state': 'busy'}, {'busy': False, 'state': 'idle'}, {'busy': False, 'state': 'idle'}]
    assert [s['busy'] for s in telemetry['ci']['slots']] == [True, False, None, None]
    assert telemetry['subscription']['label'] == 'pool-a · 2.0h runway'
    assert telemetry['subscription']['tags'][0]['pacing'] == 'paced'


@pytest.mark.parametrize(('method', 'sections'), [('hub', ['hub']), ('fleet', ['fleet', 'ci']),
                                                   ('subscription', ['subscription'])])
def test_source_failure_unknown_and_exact_recovery(method, sections):
    adapter = telemetry_adapter()
    before = run(adapter)
    original = getattr(adapter, method)
    def fail():
        raise RuntimeError('PRIVATE_SENTINEL /private/path')
    setattr(adapter, method, fail)
    missing = run(adapter)
    for section in sections:
        assert missing['telemetry'][section]['state'] == 'unknown'
        assert 'slots' not in missing['telemetry'][section]
    assert 'PRIVATE_SENTINEL' not in str(missing)
    assert missing['cursor'] != before['cursor']
    setattr(adapter, method, original)
    assert run(adapter)['cursor'] == before['cursor']


def test_partial_fleet_and_stale_ci_do_not_invent_idle_occupancy():
    adapter = telemetry_adapter()
    fleet = copy.deepcopy(adapter.fleet())
    fleet['gateways']['machines'][0]['slots'] = None
    fleet['ci']['state'] = 'stale'
    adapter.fleet = lambda: fleet
    result = run(adapter)
    assert result['telemetry']['fleet']['slots'] == []
    assert all(s == {'busy': None, 'state': 'unknown'} for s in result['telemetry']['ci']['slots'])
    assert {s['name'] for s in result['sources'] if s['state'] == 'unknown'} >= {'fleet', 'ci'}


def test_empty_history_is_not_uptime_and_empty_pools_not_a_forecast():
    adapter = telemetry_adapter()
    adapter.hub = lambda: {'status': {'state': 'up'}, 'history': {'start': 0, 'now': 86400, 'events': []}}
    adapter.subscription = lambda: {'pools': []}
    telemetry = run(adapter)['telemetry']
    assert telemetry['hub']['band']['slots'] == [{'start': 0, 'end': 86400, 'state': 'unknown'}]
    assert telemetry['subscription']['state'] == 'unknown'


def test_default_adapters_call_actual_producer_api_seams(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv('HUB_STATUS_NO_SAMPLER', '1')
    hub, runners, throughput = (api.producer(name) for name in ('hub-status', 'runners', 'throughput'))
    monkeypatch.setattr(hub, 'status', lambda: calls.append('status') or {'state': 'up'})
    monkeypatch.setattr(hub, 'events', lambda period: calls.append(('events', period)) or {'events': []})
    monkeypatch.setattr(runners, 'fleet_view', lambda: calls.append('fleet') or {'gateways': {}, 'ci': {}})
    import importlib
    runway = importlib.import_module(throughput.pkg().__name__ + '.runway')
    monkeypatch.setattr(runway, 'pacing_path', lambda root: calls.append(('path', root)) or tmp_path / 'saved.json')
    monkeypatch.setattr(runway, 'pools', lambda path, now: calls.append(('pools', path)) or [])
    monkeypatch.setattr(api, 'home', lambda: tmp_path)
    monkeypatch.setattr(throughput, 'throttle_payload', lambda *a, **k: pytest.fail('advisory/quota read'))
    defaults = api.ProducerAdapters()
    assert defaults.hub()['status']['state'] == 'up'
    assert defaults.fleet() == {'gateways': {}, 'ci': {}}
    assert defaults.subscription() == {'pools': []}
    assert calls == ['status', ('events', '24h'), 'fleet', ('path', tmp_path), ('pools', tmp_path / 'saved.json')]


def test_real_runway_reader_uses_saved_samples_only(monkeypatch, tmp_path):
    import json
    import importlib
    throughput = api.producer('throughput')
    runway = importlib.import_module(throughput.pkg().__name__ + '.runway')
    path = tmp_path / 'saved.json'
    path.write_text(json.dumps({'pool-a': {'samples': [
        [1000, 'account-a', 'seven_day', 10, 100000],
        [1600, 'account-a', 'seven_day', 20, 100000]]}}))
    monkeypatch.setattr(runway, 'pacing_path', lambda root: path)
    monkeypatch.setattr(api.time, 'time', lambda: 1600)
    value = api.read_subscription()
    assert value['pools'][0]['accounts'] == 1
    assert value['pools'][0]['runway_s'] == 4800
    assert 'account-a' not in str(value)
