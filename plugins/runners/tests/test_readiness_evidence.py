"""Per-check projection must never hide an independent readiness failure."""
from copy import deepcopy
import pytest
from plugins.runners.collector import iso
from plugins.runners.models import validate
from test_lifecycle_projection import build


def projected(svc, snap):
    result = svc._inventory(snap)
    validate('InventoryResponse', result)
    return result['data']['runners'][0]


@pytest.mark.parametrize('service,kind,source,explained_service', [
    ('running', 'maintenance', 'maintenance_marker', False),
    ('stopped', 'drained', 'systemd', True),
    ('failed', 'drained', 'systemd', False),
    ('missing', 'drained', 'systemd', False),
    ('stopped', 'maintenance', 'maintenance_marker', False),
    ('stopped', 'disk_pressure', 'admission_journal', False),
])
def test_exact_hold_cause(tmp_path, service, kind, source, explained_service):
    _, svc, snap = build(tmp_path, service=service, kind=kind, source=source)
    row = projected(svc, snap)
    e = row['readiness_evidence']
    key = 'operational:' + kind + ':' + source
    assert key in e['failed'] and key in e['hold_explained_failures']
    assert ('service' in e['hold_explained_failures']) == explained_service
    assert set(e['hold_explained_failures']) <= set(e['failed'])
    assert e['observed_at'] == iso(1000) and e['expires_at'] == iso(1180)
    assert e['freshness'] == 'fresh'
    assert svc.inspect(row['id'])['data']['runner']['readiness_evidence'] == e


@pytest.mark.parametrize('failure', ['disk', 'tool:git', 'service'])
def test_held_never_hides_independent_failure(tmp_path, failure):
    cfg, svc, snap = build(tmp_path, kind='drained', source='systemd', service='stopped')
    facts = snap['host_results'][cfg['hosts'][0]['id']]['facts']
    if failure == 'disk': facts['disk_free_bytes'] = 1024**3
    if failure == 'tool:git': facts['tools'] = {'git': False}
    if failure == 'service': facts['services'][cfg['bindings'][0]['service_ref']] = 'failed'
    e = projected(svc, snap)['readiness_evidence']
    assert failure in e['failed'] and failure not in e['hold_explained_failures']


def test_busy_draining_preserves_activity_and_checks(tmp_path):
    _, svc, snap = build(tmp_path, kind='drained', source='systemd')
    rid = snap['runners'][0]['id']
    snap['states'][rid]['activity'] = 'busy'
    row = projected(svc, snap)
    assert row['activity'] == 'busy'
    assert row['readiness_evidence']['failed'] == ['operational:drained:systemd']
    assert row['readiness_evidence']['hold_explained_failures'] == row['readiness_evidence']['failed']


@pytest.mark.parametrize('at,state', [(800, 'held'), (None, 'held'), (1000, 'unknown'), (1002, 'held')])
def test_noncurrent_hold_cannot_explain_stopped_service(tmp_path, at, state):
    _, svc, snap = build(tmp_path, kind='drained', source='systemd', service='stopped', at=at, state=state)
    e = projected(svc, snap)['readiness_evidence']
    assert e['hold_explained_failures'] == []
    assert 'service' in e['failed']
    assert 'operational:drained:systemd' in e['required']


@pytest.mark.parametrize('condition,expected', [('unreachable','unavailable'), ('host_stale','stale'), ('snapshot_stale','stale')])
def test_age_and_unavailable(tmp_path, condition, expected):
    cfg, svc, snap = build(tmp_path)
    host = snap['host_results'][cfg['hosts'][0]['id']]
    if condition == 'unreachable': host['management'] = 'unreachable'
    if condition == 'host_stale': host['at'] = 700
    if condition == 'snapshot_stale': snap['at'] = 700
    e = projected(svc, snap)['readiness_evidence']
    assert e['freshness'] == expected
    assert e['hold_explained_failures'] == []
    assert e['observed_at'] == iso(host['at'])
    assert e['expires_at'] == iso(host['at'] + 180)


def test_unbound_and_provider_spoof(tmp_path):
    cfg, svc, snap = build(tmp_path)
    fake = dict(required=[],passed=[],failed=[],warned=[],hold_explained_failures=['disk'],
                observed_at=iso(1001),expires_at=iso(9999),freshness='fresh')
    snap['runners'][0]['readiness_evidence'] = fake
    e = projected(svc, snap)['readiness_evidence']
    assert e != fake and e['observed_at'] == iso(1000)
    cfg['bindings'] = []
    assert 'readiness_evidence' not in projected(svc, snap)


def test_legacy_runner_valid_without_evidence(tmp_path):
    _, svc, snap = build(tmp_path, include=False)
    row = deepcopy(snap['runners'][0]); row.pop('readiness_evidence', None)
    validate('Runner', row)
    e = projected(svc, snap)['readiness_evidence']
    assert e['failed'] == e['hold_explained_failures'] == []
    assert set(e['passed']) == set(e['required'])


def test_provider_fields_dropped_before_persistence(tmp_path):
    from plugins.runners.collector import Collector
    from plugins.runners.providers.base import ProviderResult
    from plugins.runners.store import Store
    from test_host_health import setup
    from test_collector import F
    cfg, path, adapter = setup(tmp_path, None)
    malicious = dict(F['Runner'], readiness_evidence={'spoof': True}, operational_evidence=[{'spoof': True}])
    adapter.result = ProviderResult((malicious,), True, None, 1000, 1)
    with Store(path) as store:
        snap = Collector(store, cfg, adapter, clock=lambda:1000).tick()
        saved = store.list('Runner')[0]
        assert 'readiness_evidence' not in saved and 'operational_evidence' not in saved
    from plugins.runners.service import Service
    e = projected(Service(path, cfg, clock=lambda:1001), snap)['readiness_evidence']
    assert e['freshness'] == 'unknown' and e['observed_at'] is None
    assert e['hold_explained_failures'] == []


def test_warning_and_unknown_tool_are_not_explained(tmp_path):
    cfg, svc, snap = build(tmp_path, disk=10)
    facts = snap['host_results'][cfg['hosts'][0]['id']]['facts']
    facts.update(tools={'git': True}, tools_observed_at=0)
    e = projected(svc, snap)['readiness_evidence']
    assert e['warned'] == ['disk']
    assert 'tool:git' in e['required'] and 'tool:git' not in e['passed']
    assert 'disk' not in e['hold_explained_failures']


def test_new_snapshot_cannot_rejuvenate_host_checks(tmp_path):
    cfg, svc, snap = build(tmp_path)
    snap['at'] = 1200
    svc.clock = lambda:1200
    e = projected(svc, snap)['readiness_evidence']
    assert e['observed_at'] == iso(1000) and e['expires_at'] == iso(1180)
    assert e['freshness'] == 'stale' and e['hold_explained_failures'] == []
