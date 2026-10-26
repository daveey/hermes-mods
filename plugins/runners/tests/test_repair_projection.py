"""Repair overlays are cached, scoped and do not change provider evidence."""
from copy import deepcopy
import pytest
from plugins.runners.collector import Collector, iso
from plugins.runners.store import Store
from plugins.runners.service import Service
from plugins.runners.repairs import RepairService
from plugins.runners.models import validate
from test_collector import Adapter, config, F


def setup(tmp_path):
    cfg = config()
    cfg['features']['repair_sessions'] = True
    host = deepcopy(F['Host'])
    cfg['hosts'] = [host]
    adapter = Adapter()
    now = [1000]
    path = tmp_path / 'state.sqlite'
    store = Store(path)
    collector = Collector(store, cfg, adapter, clock=lambda:now[0])
    collector.tick()
    service = Service(path, cfg, clock=lambda:now[0])
    return cfg, host, now, store, collector, service


def test_persisted_investigation_identity_is_not_a_failure(tmp_path):
    cfg, host, now, store, collector, service = setup(tmp_path)
    try:
        detail = service.inspect(F['Runner']['id'])
        validate('InspectResponse', detail)
        context = detail['data']['incidents'][0]
        assert context['dimension'] == 'investigation'
        assert context['transitions'] == []
        now[0] += 31
        collector.tick()
        again = service.inspect(F['Runner']['id'])['data']['incidents'][0]
        assert again['id'] == context['id']
        assert again['opened_at'] == context['opened_at']
        assert again['resolved_at'] is None
    finally:
        store.close()


def test_maintenance_overlay_expiry_and_no_snapshot_mutation(tmp_path):
    cfg, host, now, store, collector, service = setup(tmp_path)
    try:
        domain = RepairService(service)
        original = deepcopy(cfg)
        domain.maintenance(dict(entity_id=host['resource_id'], state='active', reason='Inspection',
            expires_at=iso(1100), expected_config_revision=1, expected_owner=domain._owner()))
        inv = service.inventory()
        validate('InventoryResponse', inv)
        assert inv['data']['hosts'][0]['maintenance']['state'] == 'active'
        assert cfg == original
        assert inv['meta']['owner']['config_revision'] == 1
        now[0] = 1101
        assert service.inventory()['data']['hosts'][0]['maintenance']['state'] == 'inactive'
    finally:
        store.close()


def test_inspect_related_repairs_requires_principal(tmp_path):
    cfg, host, now, store, collector, service = setup(tmp_path)
    try:
        domain = RepairService(service)
        rid = F['Runner']['id']
        incident = service.inspect(rid)['data']['incidents'][0]['id']
        request = dict(entity_id=rid, incident_id=incident, idempotency_key='request_0000000000000001',
                       mode='diagnose', expected_owner=domain._owner())
        repair = domain.reserve(request, F['Requester'], principal='oidc:example')
        assert service.inspect(rid)['data']['repairs'] == []
        assert service.inspect(rid, principal='oidc:another')['data']['repairs'] == []
        assert service.inspect(rid, principal='oidc:example')['data']['repairs'] == [repair]
        with pytest.raises(ValueError, match='incident_conflict'):
            domain.reserve({**request, 'incident_id':'incident_0000000000000000',
                            'idempotency_key':'request_0000000000000002'}, F['Requester'], principal='oidc:example')
    finally:
        store.close()
