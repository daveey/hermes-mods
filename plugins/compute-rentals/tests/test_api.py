import importlib.util
import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_ownership import ROOT, load
from test_inventory import machine, result


def api_module():
    path = ROOT / 'dashboard/plugin_api.py'
    assert path.exists(), 'API implementation missing'
    spec = importlib.util.spec_from_file_location('compute_api_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rest_inventory_and_registry_only_bindings(tmp_path):
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: result([machine()]))
    api = api_module()
    app = FastAPI()
    app.include_router(api.create_router(lambda:service), prefix='/api/plugins/compute-rentals')
    client = TestClient(app)
    base = '/api/plugins/compute-rentals'
    service.inventory(refresh=True)
    data = client.get(base+'/inventory?refresh=true').json()
    body = dict(machine_key=data['machines'][0]['key'], gateway_id=service.gateway_id, profile='p', session_id='s')
    assert client.post(base+'/bindings', json=body).status_code == 200
    assert client.post(base+'/bindings', json=body).json()['ownership'] == [dict(gateway_id=service.gateway_id, profile='p', session_id='s')]
    assert client.get(base+'/inventory', params=body).json()['session_summary']['count'] == 1
    assert client.request('DELETE',base+'/bindings',json=body).json()['ownership'] == []
    assert client.post(base+'/bindings',json=dict(body,session_id='')).status_code == 422
    assert client.post(base+'/bindings',json=dict(body,machine_key='oops')).status_code == 422
    assert client.post(base+'/bindings',json=dict(body,machine_key='["vast","acct","zone","missing"]')).status_code == 404


def test_factory_discovery_and_home_resolution(tmp_path, monkeypatch):
    plugin = load('__init__')
    discover = plugin.discover_adapters
    monkeypatch.setattr(plugin, 'discover_adapters', lambda service, root=None: discover(service, root or tmp_path/'isolated-plugin'))
    monkeypatch.setenv('HERMES_HOME',str(tmp_path / 'home'))
    import sys
    import types
    monkeypatch.setitem(sys.modules,'hermes_constants',types.SimpleNamespace(get_hermes_home=lambda:tmp_path/'home'))
    service = plugin.get_service()
    assert service.state_dir == tmp_path/'home'/'compute-rentals'
    assert all(p['status'] == 'unconfigured' for p in service.inventory()['providers'])
    fixture = tmp_path/'fixture'
    (fixture/'providers').mkdir(parents=True)
    (fixture/'providers/__init__.py').write_text('def create_adapters():\n    return {"vast": lambda: {"provider":"vast", "status":"ok", "machines":[], "scope":"fixture", "error":None, "observed_at":None}}\n')
    plugin.discover_adapters(service, fixture)
    assert service.inventory(refresh=True)['providers'][0]['status'] == 'ok'


def test_plugin_tools_use_trusted_session_and_explicit_other_identity(tmp_path, monkeypatch):
    plugin = load('__init__')
    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', lambda: result([machine()]))
    monkeypatch.setattr(plugin, 'get_service', lambda:service)
    class Context:
        def __init__(self): self.tools = {}
        def register_tool(self, **kw): self.tools[kw['name']] = kw
    ctx = Context()
    plugin.register(ctx)
    data = json.loads(ctx.tools['compute_rentals_list']['handler']({'refresh':True}))
    args = dict(machine_key=data['machines'][0]['key'],gateway_id=service.gateway_id,profile='p',session_id='spoof')
    attached = json.loads(ctx.tools['compute_rentals_attach']['handler'](args,session_id='trusted'))
    assert attached['ownership'] == [dict(gateway_id=service.gateway_id,profile='p',session_id='trusted')]
    assert json.loads(ctx.tools['compute_rentals_detach']['handler'](args,session_id='trusted'))['ownership'] == []
    assert 'error' in json.loads(ctx.tools['compute_rentals_attach']['handler'](dict(args,profile='')))


def test_inventory_get_does_not_wait_for_collect(tmp_path):
    import threading
    started = threading.Event()
    release = threading.Event()

    def collect():
        started.set()
        release.wait(10)
        return result([machine()])

    service = load('inventory').InventoryService(tmp_path, providers=('vast',))
    service.register_adapter('vast', collect)
    app = FastAPI()
    app.include_router(api_module().create_router(lambda: service))
    client = TestClient(app)
    data = client.get('/inventory?refresh=true').json()
    assert started.wait(2)
    assert not release.is_set()
    assert data['providers'][0]['status'] == 'loading'
    assert data['machines'] == []
    release.set()
    service._refresh_thread.join(5)

