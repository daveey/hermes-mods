"""Operational evidence is bound, independently aged, and never provider truth."""
from copy import deepcopy
import pytest

from plugins.runners.collector import Collector, iso, readiness, operational_evidence
from plugins.runners.models import validate
from plugins.runners.service import Service
from plugins.runners.store import Store
from test_collector import F
from test_host_health import setup

PAIRS = [('drained', 'systemd'), ('maintenance', 'maintenance_marker'),
         ('disk_pressure', 'admission_journal'), ('root_headroom', 'runner_journal')]


def build(tmp_path, *, kind='disk_pressure', source='admission_journal', state='held', at=1000, recorded=1000, disk=100, service='running', include=True):
    def probe(host):
        ref = host['service_refs'][0]
        facts = dict(disk_free_bytes=disk*1024**3, services={ref: service}, tools={})
        if include:
            facts['operational_evidence'] = {ref: [dict(kind=kind, source=source, state=state, observed_at=at, recorded_at=recorded)]}
        return facts
    cfg, path, adapter = setup(tmp_path, probe)
    with Store(path) as store:
        snap = Collector(store, cfg, adapter, clock=lambda:1000, host_probe=probe).tick()
    return cfg, Service(path, cfg, clock=lambda:1001), snap


@pytest.mark.parametrize('kind,source', PAIRS)
def test_current_holds_block_checks_and_counts(tmp_path, kind, source):
    cfg, svc, snap = build(tmp_path, kind=kind, source=source)
    checks, state = readiness(cfg, snap['runners'][0], snap['host_results'], 1001)
    assert state == 'fail' and checks.failed
    result = svc.inventory()
    validate('InventoryResponse', result)
    row = result['data']['runners'][0]
    assert row['operational_evidence'][0] == dict(kind=kind, source=source, state='held', freshness='fresh', observed_at=iso(1000), expires_at=iso(1180), recorded_at=iso(1000))
    assert row['registration'] == 'online' and row['activity'] == 'idle'
    assert row['lifecycle'] == F['Runner']['lifecycle']
    assert result['data']['counts']['verified_ready'] == 0
    assert svc.inspect(row['id'])['data']['runner']['operational_evidence'] == row['operational_evidence']


@pytest.mark.parametrize('at,expected', [(800, 'stale'), (None, 'unknown'), (1002, 'stale')])
def test_own_timestamp_never_freshened(tmp_path, at, expected):
    _, svc, _ = build(tmp_path, at=at, recorded=at)
    result = svc.inventory()
    row = result['data']['runners'][0]
    assert row['operational_evidence'][0]['freshness'] == expected
    assert row['readiness'] == 'not_checked'
    assert result['data']['counts']['verified_ready'] == 0


@pytest.mark.parametrize('condition', ['host_stale', 'unreachable', 'snapshot_stale'])
def test_context_cannot_make_evidence_current(tmp_path, condition):
    cfg, svc, snap = build(tmp_path)
    host = snap['host_results'][cfg['hosts'][0]['id']]
    if condition == 'host_stale': host['at'] = 700
    if condition == 'unreachable': host['management'] = 'unreachable'
    if condition == 'snapshot_stale': snap['at'] = 700
    result = svc._inventory(snap)
    assert result['data']['runners'][0]['operational_evidence'][0]['freshness'] != 'fresh'
    assert result['data']['counts']['verified_ready'] == 0


@pytest.mark.parametrize('mismatch', ['runner_id', 'host_id', 'service_ref'])
def test_exact_binding_only(tmp_path, mismatch):
    cfg, svc, snap = build(tmp_path)
    cfg['bindings'][0][mismatch] = 'unrelated'
    row = svc._inventory(snap)['data']['runners'][0]
    assert not row.get('operational_evidence')


def test_historical_label_is_never_hold(tmp_path):
    _, svc, _ = build(tmp_path, kind='label_withdrawn', source='supervisor_log', state='unknown', recorded=500)
    result = svc.inventory()
    row = result['data']['runners'][0]
    assert row['operational_evidence'][0]['freshness'] == 'unknown'
    assert row['operational_evidence'][0]['recorded_at'] == iso(500)
    assert row['readiness'] == 'pass'
    assert result['data']['counts']['verified_ready'] == 1


@pytest.mark.parametrize('disk,service,expected', [(1,'running','fail'), (10,'running','warn'), (100,'unknown','not_checked')])
def test_clear_does_not_promote_other_checks(tmp_path, disk, service, expected):
    _, svc, _ = build(tmp_path, state='clear', disk=disk, service=service)
    result = svc.inventory()
    assert result['data']['runners'][0]['readiness'] == expected
    assert result['data']['counts']['verified_ready'] == 0


def test_legacy_runner_and_probe_remain_compatible(tmp_path):
    row = deepcopy(F['Runner']); row.pop('operational_evidence', None)
    validate('Runner', row)
    _, svc, _ = build(tmp_path, include=False)
    result = svc.inventory()
    validate('InventoryResponse', result)
    assert not result['data']['runners'][0].get('operational_evidence')
    assert result['data']['counts']['verified_ready'] == 1


def test_clear_current_evidence_preserves_ready(tmp_path):
    _, svc, _ = build(tmp_path, state='clear')
    assert svc.inventory()['data']['counts']['verified_ready'] == 1


@pytest.mark.parametrize('state', ['held', 'clear'])
@pytest.mark.parametrize('boundary', ['host', 'observation'])
def test_expiry_is_exclusive(tmp_path, state, boundary):
    cfg, _, snap = build(tmp_path, state=state)
    host = snap['host_results'][cfg['hosts'][0]['id']]
    item = next(iter(host['facts']['operational_evidence'].values()))[0]
    if boundary == 'host':
        item['observed_at'] = 1100
    else:
        host['at'] = 1100
    runner = snap['runners'][0]
    assert operational_evidence(cfg, runner, snap['host_results'], 1179.999)[0]['freshness'] == 'fresh'
    assert operational_evidence(cfg, runner, snap['host_results'], 1180)[0]['freshness'] == 'stale'
    assert readiness(cfg, runner, snap['host_results'], 1180)[1] == 'not_checked'


@pytest.mark.parametrize('state', ['held', 'clear'])
def test_future_recorded_at_cannot_be_fresh(tmp_path, state):
    _, svc, _ = build(tmp_path, state=state, recorded=1002)
    result = svc.inventory()
    row = result['data']['runners'][0]
    assert row['operational_evidence'][0]['freshness'] == 'stale'
    assert row['readiness'] == 'not_checked'
    assert result['data']['counts']['verified_ready'] == 0


@pytest.mark.parametrize('field', ['at', 'recorded'])
@pytest.mark.parametrize('bad', [1e300, 10**400, True, '1000', [], {}, float('inf'), float('nan')])
def test_invalid_cached_timestamp_is_unknown_not_a_crash(tmp_path, field, bad):
    _, svc, _ = build(tmp_path, **{field: bad})
    result = svc.inventory()
    validate('InventoryResponse', result)
    item = result['data']['runners'][0]['operational_evidence'][0]
    assert item['state'] == item['freshness'] == 'unknown'
    assert item['observed_at'] is item['expires_at'] is item['recorded_at'] is None
    assert result['data']['counts']['verified_ready'] == 0


def test_expiry_overflow_is_unknown(tmp_path):
    _, svc, _ = build(tmp_path, at=253402300799)
    result = svc.inventory()
    validate('InventoryResponse', result)
    item = result['data']['runners'][0]['operational_evidence'][0]
    assert item['state'] == item['freshness'] == 'unknown'
    assert item['expires_at'] is None
