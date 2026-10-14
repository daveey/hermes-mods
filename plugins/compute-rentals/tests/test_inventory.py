from test_ownership import load
import sqlite3
import json
import threading
from concurrent.futures import ThreadPoolExecutor


def test_locked_cache_does_not_block_readers_or_discard_inventory(tmp_path):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    entered = threading.Event()
    service.register_adapter('vast', lambda: (entered.set(), result([machine()]))[1])
    blocker = sqlite3.connect(service.ownership.path)
    blocker.execute('BEGIN IMMEDIATE')
    with ThreadPoolExecutor(max_workers=2) as pool:
        refresh = pool.submit(service.inventory, refresh=True)
        assert entered.wait(1)
        reader = pool.submit(service.inventory)
        try:
            # Both paths must return even while another connection holds the writer lock.
            data = reader.result(timeout=1)
            fresh = refresh.result(timeout=1)
            assert fresh['machines'][0]['provider_id'] == '42'
            assert fresh['providers'][0]['persistence_error'] == 'Inventory cache write failed'
        finally:
            blocker.rollback()
            blocker.close()
    assert service.inventory()['machines'][0]['stale'] is False
    service.register_adapter('vast', lambda: result([machine()]))
    assert service.inventory(refresh=True)['providers'][0]['persistence_error'] is None

import pytest


def test_readers_can_read_while_cache_persistence_is_paused(tmp_path, monkeypatch):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    entered, release = threading.Event(), threading.Event()
    connect = service.ownership.connect
    def paused_connect(*args, **kwargs):
        if 'timeout' in kwargs:
            entered.set()
            assert release.wait(2)
        return connect(*args, **kwargs)
    monkeypatch.setattr(service.ownership, 'connect', paused_connect)
    service.register_adapter('vast', lambda: result([machine()]))
    with ThreadPoolExecutor(max_workers=2) as pool:
        refresh = pool.submit(service.inventory)
        assert entered.wait(1)
        reader = pool.submit(service.inventory)
        try:
            data = reader.result(timeout=1)
            assert data['machines'][0]['provider_id'] == '42'
            assert data['refreshing'] is True
        finally:
            release.set()
        refresh.result(timeout=1)


def test_decimal_totals_preserve_beyond_default_context_precision(tmp_path):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: result([
        machine(rate='0.12345678901234567890123456789'),
        machine(provider_id='43', rate='0.00000000000000000000000000002')]))
    data = service.inventory()
    assert data['summary']['rates']['USD'] == '0.12345678901234567890123456791'
    assert data['summary']['complete'] is True


@pytest.mark.parametrize('rate', ['1e4097', '1e-4097', '1' * 4097], ids=['large-exponent', 'small-exponent', 'long-coefficient'])
def test_unbounded_rates_become_unknown_without_expansion(tmp_path, rate):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: result([machine(rate=rate)]))
    data = service.inventory()
    assert data['machines'][0]['hourly_rate'] is None
    assert data['summary']['complete'] is False


@pytest.mark.parametrize('changes', [dict(hardware=['GPU']), dict(started_at='not a date'), dict(expires_at='tomorrow'), dict(observed_at=None), dict(ssh={'token':'SECRET'}), dict(console_url=['SECRET']), dict(rate_note={'token':'SECRET'}), dict(state_detail={'token':'SECRET'}), dict(started_at='2026-10-09'), dict(observed_at='2026-10-09T00:00:00')])
def test_invalid_normalized_fields_reject_collection(tmp_path, changes):
    service = load('inventory').InventoryService(tmp_path,providers=('vast',))
    service.register_adapter('vast',lambda:result([dict(machine(),**changes)]))
    data = service.inventory(refresh=True)
    assert data['providers'][0]['status'] == 'error'
    assert data['machines'] == []



@pytest.mark.parametrize('extra', [False, True])
def test_cached_inventory_is_revalidated_before_exposure(tmp_path, extra):
    module = load('inventory')
    service = module.InventoryService(tmp_path, providers=('vast',))
    provider = 'extra' if extra else 'vast'
    row = module.normalize_machine(machine(provider=provider), provider)
    row['ssh'] = {'token': 'SECRET'}
    with service.ownership.connect() as db:
        db.execute('INSERT INTO inventory_cache VALUES (?, ?)', (provider, json.dumps(dict(machines=[row], state=dict(provider=provider, status='ok', scope={'token':'SECRET'}, last_success_at={'token':'SECRET'})))))
    restarted = module.InventoryService(tmp_path, providers=('vast',))
    if extra:
        restarted.register_adapter(provider, lambda: dict(provider=provider, status='error', machines=[]))
    data = restarted.inventory()
    assert 'SECRET' not in str(data)
    assert data['machines'] == []


def test_last_success_survives_failure_partial_and_restart(tmp_path, monkeypatch):
    module = load('inventory')
    service = module.InventoryService(tmp_path, providers=('vast',))
    assert service.inventory()['providers'][0]['last_success_at'] is None
    first = '2026-10-09T01:00:00+00:00'
    second = '2026-10-09T02:00:00+00:00'
    monkeypatch.setattr(module, 'now', lambda: first)
    service.register_adapter('vast', lambda: result([machine()]))
    success = service.inventory()['providers'][0]
    assert success['last_success_at'] == first
    assert success['observed_at'] != first  # Provider observation is independent.
    monkeypatch.setattr(module, 'now', lambda: second)
    for status in ('error', 'partial', 'unconfigured'):
        service.register_adapter('vast', lambda status=status: result([], status=status))
        assert service.inventory()['providers'][0]['last_success_at'] == first
    def fail():
        raise RuntimeError('SECRET')
    service.register_adapter('vast', fail)
    failed = service.inventory()['providers'][0]
    assert failed['last_success_at'] == first
    assert failed['observed_at'] == second
    restarted = module.InventoryService(tmp_path, providers=('vast',))
    assert restarted.inventory()['providers'][0]['last_success_at'] == first
    restarted.register_adapter('vast', lambda: result([]))
    assert restarted.inventory()['providers'][0]['last_success_at'] == second


def test_string_metadata_is_preserved(tmp_path):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: result([dict(machine(), ssh='ssh host', console_url='https://example.com', rate_note='Estimated', state_detail='running')]))
    row = service.inventory()['machines'][0]
    assert row['rate_note'] == 'Estimated'
    assert row['state_detail'] == 'running'
    assert row['ssh'] == 'ssh host'


@pytest.mark.parametrize('changes', [dict(scope={'token':'SECRET'}), dict(error=['SECRET']), dict(observed_at={'token':'SECRET'}), dict(observed_at='yesterday')])
def test_provider_metadata_rejects_raw_objects(tmp_path, changes):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: dict(result([machine()]), **changes))
    data = service.inventory()
    assert data['providers'][0]['status'] == 'error'
    assert 'SECRET' not in str(data)
    assert 'SECRET' not in service.ownership.path.read_bytes().decode(errors='ignore')


def test_stale_cache_survives_errors_partial_results_and_restart(tmp_path):
    m = load('inventory')
    service = m.InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: result([machine()]))
    service.inventory(refresh=True)
    def fail():
        raise RuntimeError('do not expose credentials from exception')
    service.register_adapter('vast', fail)
    stale = service.inventory(refresh=True)
    assert stale['machines'][0]['stale'] is True
    assert stale['summary']['complete'] is False
    assert stale['providers'][0]['status'] == 'error'
    assert 'credentials' not in stale['providers'][0]['error']
    restarted = m.InventoryService(tmp_path, providers=('vast',))
    assert restarted.inventory()['machines'][0]['stale'] is True
    restarted.register_adapter('vast', lambda: result([machine(provider_id='43')], status='partial'))
    partial = restarted.inventory(refresh=True)
    assert len(partial['machines']) == 2
    assert partial['summary']['complete'] is False
    restarted.register_adapter('vast', lambda: result([]))
    assert restarted.inventory(refresh=True)['machines'] == []


def machine(provider='vast', provider_id='42', rate='0.10', currency='USD'):
    return dict(provider=provider, account='acct', region='zone', provider_id=provider_id,
                display_name='Test fixture', status='running', hardware='GPU', hourly_rate=rate,
                currency=currency, rate_basis='reported', observed_at='2026-10-09T00:00:00+00:00')


def result(machines, status='ok'):
    return dict(provider='vast', machines=machines, status=status, error=None, scope='test account', observed_at='2026-10-09T00:00:00+00:00')


def test_inventory_deduplicates_sums_exact_rates_and_selects_session(tmp_path):
    m = load('inventory')
    service = m.InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: result([machine(), machine(), machine(provider_id='43', rate='0.20'), machine(provider_id='44', rate=None), machine(provider_id='45', rate='2.30', currency='EUR')]))
    data = service.inventory(refresh=True)
    assert len(data['machines']) == 4
    assert data['summary'] == dict(count=4, rates={'USD':'0.30','EUR':'2.30'}, unknown_rates=1, complete=False, by_resource_kind=dict(instance=4, physical_node=0, pod=0, serverless_worker=0, sandbox=0), internal_count=0, rental_count=4)
    assert data['session_summary']['count'] == 0
    first = data['machines'][0]
    assert first['key'] == '["vast","acct","zone","42"]'
    assert first['started_at'] is None and first['expires_at'] is None and first['ssh'] is None
    identity = dict(gateway_id='g', profile='p', session_id='s')
    service.ownership.attach(first['key'], **identity)
    assert service.inventory(**identity)['session_summary']['count'] == 1
    for other in (dict(identity, gateway_id='other'), dict(identity, profile='other'), dict(identity, session_id='')):
        assert service.inventory(**other)['session_summary']['count'] == 0
