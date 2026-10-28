from test_ownership import load
from test_inventory import machine, result
from test_api import api_module
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_new_factory_provider_cache_is_retained_after_restart(tmp_path):
    module = load('inventory')
    service = module.InventoryService(tmp_path)
    service.register_adapter('extra', lambda: dict(provider='extra', status='ok', machines=[machine(provider='extra')]))
    service.inventory()
    restarted = module.InventoryService(tmp_path)
    restarted.register_adapter('extra', lambda: dict(provider='extra', status='error', machines=[]))
    data = restarted.inventory()
    assert len(data['machines']) == 1
    assert data['machines'][0]['stale'] is True
    assert data['summary']['complete'] is False


def test_rate_and_state_notes_are_allowlisted(tmp_path):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: result([dict(machine(), rate_note='Compute only; storage excluded', state_detail='Provisioning', token='secret')]))
    item = service.inventory()['machines'][0]
    assert item['rate_note'] == 'Compute only; storage excluded'
    assert item['state_detail'] == 'Provisioning'
    assert 'token' not in item


def test_build_collectors_discovered_preferentially_with_relative_imports(tmp_path):
    plugin = load('__init__')
    root = tmp_path / 'hyphenated-plugin'
    (root / 'providers').mkdir(parents=True)
    (root / 'providers/data.py').write_text('def collect():\n    return {"provider":"extra", "status":"ok", "machines":[]}\n')
    (root / 'providers/__init__.py').write_text('from .data import collect\ndef build_collectors():\n    return {"extra":collect}\ndef create_adapters():\n    raise AssertionError("legacy must not win")\n')
    service = load('inventory').InventoryService(tmp_path / 'state', providers=())
    plugin.discover_adapters(service, root)
    assert list(service.adapters) == ['extra']
    assert service.inventory()['providers'][0]['status'] == 'ok'


def test_startup_ttl_and_manual_refresh_throttle(tmp_path):
    clock = [0.0]
    calls = []
    service = load('inventory').InventoryService(tmp_path, providers=('vast',), clock=lambda: clock[0])
    service.register_adapter('vast', lambda: (calls.append(1) or result([machine()])))
    assert service.inventory()['summary']['count'] == 1
    service.inventory()
    service.inventory(refresh=True)
    assert len(calls) == 1
    clock[0] = 3
    service.inventory(refresh=True)
    assert len(calls) == 2
    clock[0] = 32
    service.inventory()
    assert len(calls) == 2
    clock[0] = 33
    service.inventory()
    assert len(calls) == 3


def test_slow_collection_coalesces_without_blocking_other_reads(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    entered, release = threading.Event(), threading.Event()
    calls = []
    def collect():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return result([machine()])
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', collect)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(service.inventory)
        assert entered.wait(2)
        try:
            pending = pool.submit(service.inventory, refresh=True).result(timeout=2)
            assert pending['refreshing'] is True
            assert pending['summary']['complete'] is False
            assert pending['providers'][0]['status'] == 'loading'
            assert len(calls) == 1
        finally:
            release.set()
        assert first.result(timeout=2)['summary']['count'] == 1


def test_empty_gateway_resolves_persistent_identity_and_rejects_foreign_binding(tmp_path):
    module = load('inventory')
    service = module.InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: result([machine()]))
    app = FastAPI()
    app.include_router(api_module().create_router(lambda: service))
    client = TestClient(app)
    service.inventory(refresh=True)
    data = client.get('/inventory?refresh=true').json()
    gateway = data['gateway_id']
    assert gateway
    assert module.InventoryService(tmp_path).gateway_id == gateway
    assert module.InventoryService(tmp_path / 'other').gateway_id != gateway
    body = dict(machine_key=data['machines'][0]['key'], gateway_id=gateway, profile='p', session_id='s')
    assert client.post('/bindings', json=body).status_code == 200
    assert client.get('/inventory', params=dict(profile='p', session_id='s', gateway_id='')).json()['session_summary']['count'] == 1
    assert client.post('/bindings', json=dict(body, gateway_id='foreign')).status_code == 422
    assert client.request('DELETE', '/bindings', json=dict(body, gateway_id='foreign')).status_code == 422
    assert client.get('/inventory', params=dict(profile='p', session_id='other')).json()['session_summary']['count'] == 0
