"""Synthetic fixture tests only: no live Slurm/Docker or cloud calls."""
import json
import pytest
from test_ownership import load
from test_inventory import machine, result

PARENT = '["vast","acct","zone","42"]'
STAMP = '2026-10-09T00:00:00+00:00'


def workload(**changes):
    return dict(dict(provider='vast', account='acct', provider_id='job-1',
                     resource_kind='allocation', display_name='Synthetic job',
                     status='running', parent_machine_keys=[PARENT], hardware=None,
                     started_at=None, expires_at=None, observed_at=STAMP, state_detail=None), **changes)


def collect(workloads, machines=None, status='ok'):
    return dict(result([machine()] if machines is None else machines, status), workloads=workloads)


def test_workloads_are_typed_deduplicated_and_never_counted_as_machines(tmp_path):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: collect([workload(), workload()]))
    data = service.inventory()
    assert data['summary']['count'] == 1
    assert data['summary']['rates'] == {'USD': '0.10'}
    assert len(data['machines']) == 1
    assert len(data['workloads']) == 1
    assert data['workloads'][0] == dict(workload(), key='["vast","acct","internal","job-1"]', stale=False)
    assert data['workload_summary'] == dict(count=1, by_resource_kind=dict(allocation=1, queued_job=0, container=0), active_by_resource_kind=dict(allocation=1, queued_job=0, container=0), unknown_status_count=0, complete=True)
    assert data['session_workloads'] == []


def test_workload_cache_retains_stale_partial_failure_restart_and_clears_on_success(tmp_path):
    module = load('inventory')
    service = module.InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: collect([workload()], status='ok'))
    success = service.inventory()['providers'][0]['last_success_at']
    service.register_adapter('vast', lambda: collect([workload(provider_id='job-2')], status='partial'))
    partial = service.inventory()
    assert [w['stale'] for w in partial['workloads']] == [True, False]
    assert partial['workload_summary']['complete'] is False
    restarted = module.InventoryService(tmp_path, providers=('vast',))
    cached = restarted.inventory()
    assert len(cached['workloads']) == 2
    assert all(w['stale'] for w in cached['workloads'])
    assert cached['providers'][0]['last_success_at'] == success
    restarted.register_adapter('vast', lambda: collect([], status='error'))
    assert len(restarted.inventory()['workloads']) == 2
    restarted.register_adapter('vast', lambda: collect([]))
    assert restarted.inventory()['workloads'] == []


@pytest.mark.parametrize('changes', [
    dict(parent_machine_keys={}), dict(parent_machine_keys=[PARENT] * 1025),
    dict(display_name=None), dict(status=None), dict(hardware='x' * 4097),
    dict(provider_id='x' * 1025), dict(resource_kind='physical_node'),
    dict(hardware={'token': 'SECRET'}), dict(state_detail=['SECRET']),
    dict(parent_machine_keys=['["a", "b", "c", "d"]']),
    dict(observed_at=None), dict(started_at='yesterday'), dict(key='SECRET'),
    dict(provider='other'),
])
def test_invalid_workloads_fail_closed_keep_snapshot_and_do_not_store_raw_values(tmp_path, changes):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: collect([workload()]))
    service.inventory()
    service.register_adapter('vast', lambda: collect([workload(**changes)]))
    data = service.inventory()
    assert data['providers'][0]['status'] == 'error'
    assert data['providers'][0]['scope'] == 'test account'
    assert data['workloads'][0]['stale'] is True
    assert 'SECRET' not in json.dumps(data)
    assert 'SECRET' not in service.ownership.path.read_bytes().decode(errors='ignore')


@pytest.mark.parametrize('bad', [{}, [workload()] * 10001])
def test_workload_collection_is_bounded_list(tmp_path, bad):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: collect(bad))
    assert service.inventory()['providers'][0]['status'] == 'error'


def test_session_workloads_require_explicit_parent_binding_and_label_nonexclusive_association(tmp_path):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: collect([
        workload(), workload(provider_id='queued', resource_kind='queued_job', status='queued', parent_machine_keys=[]),
        workload(provider_id='docker', resource_kind='container', parent_machine_keys=[PARENT, PARENT])]))
    a = dict(gateway_id=service.gateway_id, profile='default', session_id='a')
    b = dict(a, session_id='b')
    assert service.inventory(**a)['session_workloads'] == []
    for identity in (a, b):
        service.ownership.attach(PARENT, **identity)
        data = service.inventory(**identity)
        assert len(data['session_workloads']) == 2
        for w in data['session_workloads']:
            assert w['association'] == dict(basis='explicit_parent_machine_binding', exclusive=False, parent_machine_keys=[PARENT], label='Associated through a bound machine; not exclusive workload ownership')
            assert 'ownership' not in w
        assert data['session_workload_summary']['count'] == 2
        assert data['workload_summary']['count'] == 3
        assert data['summary']['count'] == 1
    for identity in (dict(a, profile='other'), dict(a, gateway_id='other'), dict(a, session_id='')):
        assert service.inventory(**identity)['session_workloads'] == []


def test_resource_kinds_and_explicit_prepaid_capacity_have_separate_counts_and_no_fake_rate(tmp_path):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    node = dict(machine(rate=None), resource_kind='physical_node', billing_class='internal_prepaid', rate_note='Already paid internal capacity')
    pod = dict(machine(provider_id='pod'), resource_kind='pod', billing_class='rental')
    worker = dict(machine(provider_id='worker'), resource_kind='serverless_worker')
    sandbox = dict(machine(provider_id='sandbox'), resource_kind='sandbox')
    service.register_adapter('vast', lambda: collect([], [node, pod, worker, sandbox]))
    data = service.inventory()
    summary = data['summary']
    assert summary['internal_count'] == 1
    assert summary['rental_count'] == 3
    assert summary['by_resource_kind'] == dict(instance=0, physical_node=1, pod=1, serverless_worker=1, sandbox=1)
    assert summary['rates'] == {'USD': '0.30'}
    assert summary['unknown_rates'] == 0
    assert summary['complete'] is True
    assert data['machines'][0]['hourly_rate'] is None
    assert data['machines'][0]['billing_class'] == 'internal_prepaid'
    # Neither generic nodes nor notes alone establish prepaid billing.
    service.register_adapter('vast', lambda: collect([], [dict(node, billing_class='unknown')]))
    assert service.inventory()['summary']['unknown_rates'] == 1
    service.register_adapter('vast', lambda: collect([], [dict(node, billing_class='rental')]))
    assert service.inventory()['summary']['internal_count'] == 0


@pytest.mark.parametrize('changes', [dict(resource_kind='allocation'), dict(resource_kind='container'), dict(resource_kind={}), dict(billing_class='free'), dict(resource_kind='pod', billing_class='internal_prepaid'), dict(hourly_rate='0', billing_class='internal_prepaid')])
def test_machine_type_and_prepaid_billing_are_validated(tmp_path, changes):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: collect([], [dict(machine(), **changes)]))
    assert service.inventory()['providers'][0]['status'] == 'error'


def test_tool_profile_uses_verified_host_accessor_not_model_input(tmp_path, monkeypatch):
    plugin = load('__init__')
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: collect([]))
    monkeypatch.setattr(plugin, 'get_service', lambda: service)
    class Context:
        profile_name = 'host-profile'
        def __init__(self): self.tools = {}
        def register_tool(self, **kwargs): self.tools[kwargs['name']] = kwargs
    ctx = Context()
    plugin.register(ctx)
    handler = ctx.tools['compute_rentals_attach']['handler']
    out = json.loads(handler(dict(machine_key=PARENT, gateway_id=service.gateway_id, profile='model-profile', session_id='model-session'), session_id='host-session'))
    assert out['ownership'] == [dict(gateway_id=service.gateway_id, profile='host-profile', session_id='host-session')]
    ctx.profile_name = 'second-host-profile'
    out = json.loads(handler(dict(machine_key=PARENT, gateway_id=service.gateway_id), session_id='second-session'))
    assert dict(gateway_id=service.gateway_id, profile='second-host-profile', session_id='second-session') in out['ownership']


def test_workload_snapshot_parent_lists_cannot_mutate_cache(tmp_path):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: collect([workload()]))
    first = service.inventory()
    first['workloads'][0]['parent_machine_keys'].clear()
    assert service.inventory()['workloads'][0]['parent_machine_keys'] == [PARENT]


@pytest.mark.parametrize('extra_provider', [False, True])
def test_cached_workloads_are_revalidated_for_default_and_discovered_providers(tmp_path, extra_provider):
    module = load('inventory')
    service = module.InventoryService(tmp_path, providers=('vast',))
    provider = 'extra' if extra_provider else 'vast'
    with service.ownership.connect() as db:
        db.execute('INSERT INTO inventory_cache VALUES (?, ?)', (provider, json.dumps(dict(machines=[], workloads=[workload(provider=provider, state_detail={'token':'SECRET'})], state=dict(provider=provider, status='ok')))))
    restarted = module.InventoryService(tmp_path, providers=('vast',))
    if extra_provider:
        restarted.register_adapter(provider, lambda: dict(provider=provider, status='error'))
    data = restarted.inventory()
    assert data['workloads'] == []
    assert 'SECRET' not in json.dumps(data)


def test_workload_counts_distinguish_active_terminal_unknown_and_coverage(tmp_path):
    service = load('inventory').InventoryService(tmp_path, providers=('vast', 'missing'))
    service.register_adapter('vast', lambda: collect([
        workload(), workload(provider_id='2', resource_kind='queued_job', status='queued', parent_machine_keys=[]),
        workload(provider_id='3', resource_kind='container', status='exited'),
        workload(provider_id='4', resource_kind='container', status='unknown')]))
    summary = service.inventory()['workload_summary']
    assert summary['by_resource_kind'] == dict(allocation=1, queued_job=1, container=2)
    assert summary['active_by_resource_kind'] == dict(allocation=1, queued_job=1, container=0)
    assert summary['unknown_status_count'] == 1
    assert summary['complete'] is False


def test_authoritative_adapter_workload_keys_are_accepted(tmp_path):
    service = load('inventory').InventoryService(tmp_path, providers=('slurm', 'dockerhosts'))
    parent = '["slurm","cluster-a","internal","node1"]'
    node = dict(machine(provider='slurm', provider_id='node1', rate=None), account='cluster-a', region='internal', resource_kind='physical_node', billing_class='internal_prepaid')
    for provider, kind, account, ident in [('slurm', 'allocation', 'cluster-a', '123'), ('dockerhosts', 'container', 'node1', 'a' * 64)]:
        row = workload(provider=provider, account=account, provider_id=ident, resource_kind=kind,
                       key=json.dumps([provider, account, 'internal', ident], separators=(',', ':')),
                       parent_machine_keys=[parent])
        service.register_adapter(provider, lambda provider=provider, row=row: dict(provider=provider, status='ok', machines=[node] if provider == 'slurm' else [], workloads=[row]))
    identity = dict(gateway_id=service.gateway_id, profile='default', session_id='s')
    service.ownership.attach(parent, **identity)
    data = service.inventory(**identity)
    assert [p['status'] for p in data['providers']] == ['ok', 'ok']
    assert len(data['workloads']) == len(data['session_workloads']) == 2
    assert data['summary']['internal_count'] == 1
    assert data['summary']['unknown_rates'] == 0


def test_unknown_fields_never_escape_allowlist(tmp_path):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: collect([workload(raw={'env': 'SECRET'}, token='SECRET')]))
    data = service.inventory()
    assert data['providers'][0]['status'] == 'ok'
    assert 'SECRET' not in json.dumps(data)
    assert 'SECRET' not in service.ownership.path.read_bytes().decode(errors='ignore')
