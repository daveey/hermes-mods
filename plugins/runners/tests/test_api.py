"""Offline API boundary tests; core auth functions loaded without server startup."""
import ast
import hmac
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from plugins.runners.config import normalize
from plugins.runners.service import Service
from plugins.runners.store import Store

ROOT = Path(__file__).parents[1]
F = json.loads((ROOT / 'contracts/fixtures.json').read_text())
PREFIX = '/api/plugins/runners/v1'


@pytest.fixture
def api(monkeypatch, tmp_path):
    # Execute the exact core auth implementation, not a permissive mock.
    core = Path.home() / '.hermes/hermes-agent/hermes_cli/web_server.py'
    tree = ast.parse(core.read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in
             ('_require_token', '_has_valid_session_token')]
    module = types.ModuleType('hermes_cli.web_server')
    module.__dict__.update(hmac=hmac, Request=Request, HTTPException=HTTPException,
                           _SESSION_TOKEN='fictional-test-token', _SESSION_HEADER_NAME='x-hermes-session-token')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(core), 'exec'), module.__dict__)
    monkeypatch.setitem(sys.modules, 'hermes_cli.web_server', module)
    spec = importlib.util.spec_from_file_location('runners_test_api', ROOT / 'dashboard/plugin_api.py')
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    cfg = normalize(dict(schema_version=1, owner_gateway_id=F['Owner']['gateway_id'], owner_profile='example'))
    path = tmp_path / 'state.sqlite'
    from plugins.runners.collector import initialize
    with Store(path) as store:
        initialize(store)
    service = Service(path, cfg)
    api._real_service = api.service
    monkeypatch.setattr(api, 'service', lambda: service)
    monkeypatch.setattr(api, 'active_profile', lambda: 'example', raising=False)
    app = FastAPI()
    @app.middleware('http')
    async def verified_state(request, call_next):
        if request.headers.get('test-session') == 'verified':
            request.state.session = object()
            request.app.state.auth_required = True
        else:
            request.app.state.auth_required = False
        return await call_next(request)
    app.include_router(api.router, prefix='/api/plugins/runners')
    return api, TestClient(app), service


TOKEN = {'x-hermes-session-token': 'fictional-test-token'}


def test_auth_and_cached_inventory(api):
    _, client, _ = api
    assert client.get(PREFIX + '/inventory').status_code == 401
    assert client.get(PREFIX + '/inventory?token=fictional-test-token', headers={'connection-id': 'local'}).status_code == 401
    r = client.get(PREFIX + '/inventory', headers=TOKEN)
    assert r.status_code == 200
    assert r.json()['data']['state'] == 'not_configured'


@pytest.mark.parametrize('origin', ['https://testserver', 'http://evil.example', 'null', 'http://testserver?x=y', 'http://testserver#x', 'http://['])
def test_bad_origin(api, origin):
    _, client, _ = api
    assert client.get(PREFIX + '/inventory', headers={**TOKEN, 'origin': origin}).status_code == 403


def test_cookie_csrf_cannot_be_bypassed_by_fake_header(api):
    _, client, _ = api
    for extra in ({}, {'authorization': 'garbage'}, {'x-hermes-session-token': 'wrong'}):
        r = client.post(PREFIX + '/refresh', json=F['RefreshRequest'], headers={'test-session': 'verified', **extra})
        assert r.status_code == 403


def test_profile_owner_fenced(api, monkeypatch):
    module, client, service = api
    monkeypatch.setattr(module, 'active_profile', lambda: 'other')
    for method, route in [('get', '/inventory'), ('get', '/runners/' + F['Runner']['id']), ('post', '/refresh')]:
        kwargs = {'json': F['RefreshRequest']} if method == 'post' else {}
        assert getattr(client, method)(PREFIX + route, headers=TOKEN, **kwargs).status_code == 403


def test_validation_and_safe_errors(api):
    _, client, _ = api
    assert client.get(PREFIX + '/runners/not-an-id', headers=TOKEN).status_code == 400
    assert client.get(PREFIX + '/runners/' + F['Runner']['id'], headers=TOKEN).status_code == 404
    assert client.post(PREFIX + '/refresh', headers=TOKEN, json={'target_ids': [], 'idempotency_key': 'bad'}).status_code == 400


def test_missing_store_is_unavailable(api):
    _, client, service = api
    service.path = Path(service.path).parent / 'absent.sqlite'
    assert client.get(PREFIX + '/inventory', headers=TOKEN).status_code == 503
    assert not service.path.exists()


def test_cached_inspect_and_refresh_round_trip(api):
    from plugins.runners.collector import Collector
    from plugins.runners.providers.base import ProviderResult
    from plugins.runners.models import validate
    _, client, service = api
    service.config['scopes'] = [F['Scope']]
    class FixtureAdapter:
        calls = 0
        def enumerate(self, scope):
            self.calls += 1
            return ProviderResult((F['Runner'],), True, None, 1000, 1)
    adapter = FixtureAdapter()
    with Store(service.path) as store:
        Collector(store, service.config, adapter, clock=lambda: 1000).tick()
    service.clock = lambda: 1001
    response = client.get(PREFIX + '/runners/' + F['Runner']['id'], headers=TOKEN)
    assert response.status_code == 200
    validate('InspectResponse', response.json())
    payload = dict(target_ids=[F['Scope']['id']], idempotency_key='request_' + 'a' * 16)
    cookie = {'test-session': 'verified', 'origin': 'http://testserver'}
    first = client.post(PREFIX + '/refresh', headers=cookie, json=payload)
    assert first.status_code == 200
    validate('RefreshResponse', first.json())
    second = client.post(PREFIX + '/refresh', headers=TOKEN, json=payload)
    assert second.status_code == 200
    assert first.json()['data']['id'] == second.json()['data']['id']
    assert second.json()['data']['state'] == 'pending'
    with Store(service.path) as store:
        assert store.connection.execute('SELECT count(*) FROM collector_refresh').fetchone()[0] == 1
    assert adapter.calls == 1  # HTTP never calls the provider or runs the collector.


@pytest.fixture
def disk_api(api, monkeypatch, tmp_path):
    import yaml
    from plugins.runners.config import ConfigFile
    module, client, backend = api
    home = tmp_path / 'home'
    (home / 'runners').mkdir(parents=True)
    backend.path.replace(home / 'runners/state.sqlite')
    path = home / 'runners.yaml'
    path.write_text(yaml.safe_dump(backend.config))
    constants = types.ModuleType('hermes_constants')
    constants.get_hermes_home = lambda: home
    monkeypatch.setitem(sys.modules, 'hermes_constants', constants)
    monkeypatch.setattr(module, 'service', module._real_service)
    monkeypatch.setattr(module, '_backend', lambda: (ConfigFile, Service))
    calls = []
    load = ConfigFile.load
    def counted(self):
        calls.append(self.path)
        return load(self)
    monkeypatch.setattr(ConfigFile, 'load', counted)
    return module, client, path, calls, backend.config, constants


def test_disk_config_is_cached_and_detached(disk_api):
    module, client, path, calls, cfg, _ = disk_api
    for _ in range(3):
        assert client.get(PREFIX + '/inventory', headers=TOKEN).status_code == 200
    assert len(calls) == 1
    module.service().config['features']['observe'] = False
    assert module.service().config['features']['observe'] is True
    assert len(calls) == 1


def test_atomic_replace_same_size_mtime_invalidates(disk_api):
    import os
    module, client, path, calls, cfg, _ = disk_api
    assert module.service().config['config_revision'] == 1
    before = path.stat()
    replacement = path.with_suffix('.next')
    replacement.write_text(path.read_text().replace('config_revision: 1', 'config_revision: 2'))
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    replacement.replace(path)
    assert module.service().config['config_revision'] == 2
    assert len(calls) == 2


@pytest.mark.parametrize('failure', ['malformed', 'missing', 'schema'])
def test_config_failure_never_serves_stale(disk_api, failure):
    module, client, path, calls, cfg, _ = disk_api
    original = path.read_text()
    assert client.get(PREFIX + '/inventory', headers=TOKEN).status_code == 200
    if failure == 'missing':
        path.unlink()
    else:
        path.write_text('[: invalid' if failure == 'malformed' else 'schema_version: 999')
    for _ in range(2):
        assert client.get(PREFIX + '/inventory', headers=TOKEN).status_code in (400, 503)
    path.write_text(original)
    assert client.get(PREFIX + '/inventory', headers=TOKEN).status_code == 200


def test_config_replacement_during_load_is_rejected(disk_api, monkeypatch):
    from plugins.runners.config import ConfigFile
    module, client, path, calls, cfg, _ = disk_api
    load = ConfigFile.load
    def racing(self):
        result = load(self)
        path.write_text(path.read_text().replace('config_revision: 1', 'config_revision: 2'))
        return result
    monkeypatch.setattr(ConfigFile, 'load', racing)
    assert client.get(PREFIX + '/inventory', headers=TOKEN).status_code == 503
    monkeypatch.setattr(ConfigFile, 'load', load)
    assert module.service().config['config_revision'] == 2


def test_cache_profile_and_home_are_fenced(disk_api, monkeypatch):
    module, client, path, calls, cfg, constants = disk_api
    assert client.get(PREFIX + '/inventory', headers=TOKEN).status_code == 200
    monkeypatch.setattr(module, 'active_profile', lambda: 'other')
    assert client.get(PREFIX + '/inventory', headers=TOKEN).status_code == 403
    assert len(calls) == 2
    monkeypatch.setattr(module, 'active_profile', lambda: 'example')
    constants.get_hermes_home = lambda: path.parent / 'absent'
    assert client.get(PREFIX + '/inventory', headers=TOKEN).status_code == 503


@pytest.mark.parametrize('field,value', [('owner_epoch', 2), ('config_revision', 2),
                                        ('owner_gateway_id', '00000000-0000-4000-8000-000000000002')])
def test_cached_config_preserves_snapshot_owner_fence(disk_api, field, value):
    import yaml
    module, client, path, calls, cfg, _ = disk_api
    snapshot = {'owner': {k: cfg[k] for k in
                ('owner_gateway_id', 'owner_profile', 'owner_epoch', 'config_revision')}}
    with Store(path.parent / 'runners/state.sqlite') as store:
        store.connection.execute("INSERT OR REPLACE INTO collector_cache(key,payload) VALUES ('snapshot',?)",
                                 (json.dumps(snapshot),))
        store.connection.commit()
    module.service()  # warm old config without reading the deliberately minimal snapshot
    cfg[field] = value
    path.write_text(yaml.safe_dump(cfg))
    assert client.get(PREFIX + '/inventory', headers=TOKEN).status_code == 503


def test_in_place_same_size_mtime_invalidates(disk_api):
    import os
    module, _, path, calls, _, _ = disk_api
    module.service()
    before = path.stat()
    path.write_text(path.read_text().replace('config_revision: 1', 'config_revision: 2'))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert module.service().config['config_revision'] == 2
    assert len(calls) == 2


def test_concurrent_requests_parse_once(disk_api):
    from concurrent.futures import ThreadPoolExecutor
    module, _, _, calls, _, _ = disk_api
    with ThreadPoolExecutor(max_workers=8) as pool:
        configs = list(pool.map(lambda _: module.service().config, range(16)))
    assert len(calls) == 1
    assert all(c == configs[0] for c in configs)
    assert len({id(c) for c in configs}) == 16


def test_fake100_555_scope_offline_route_benchmark(disk_api, monkeypatch):
    """Opt-in measurement of real FastAPI/auth, NOT live gateway latency."""
    import os
    import math
    import statistics
    import time
    from copy import deepcopy
    import yaml
    from plugins.runners.collector import Collector
    from plugins.runners.config import ConfigFile
    from plugins.runners.providers.base import ProviderResult
    evidence = os.environ.get('RUNNERS_API_BENCHMARK')
    if not evidence:
        pytest.skip('set RUNNERS_API_BENCHMARK to an evidence JSON path')
    module, client, path, calls, cfg, _ = disk_api
    cfg['scopes'] = [dict(F['Scope'], id=f'scope_{i + 1:016x}',
                          name=f'fictional/project-{i}') for i in range(555)]
    cfg = normalize(cfg)
    path.write_text(yaml.safe_dump(cfg))
    rows = [dict(deepcopy(F['Runner']), id=f'runner_{i + 1:016x}',
                 provider_runner_id=str(i + 1), scope_id=cfg['scopes'][0]['id'])
            for i in range(100)]
    class FakeProvider:
        calls = 0
        def enumerate(self, scope):
            self.calls += 1
            return ProviderResult(tuple(rows), True, None, time.time(), 1)
    provider = FakeProvider()
    seed = deepcopy(cfg)
    seed['scopes'] = seed['scopes'][:1]
    with Store(path.parent / 'runners/state.sqlite') as store:
        Collector(store, seed, provider).tick()
    def forbidden(*args, **kwargs):
        raise AssertionError('offline cached route must not collect or access network')
    monkeypatch.setattr(provider, 'enumerate', forbidden)
    import socket
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    assert client.get(PREFIX + '/inventory').status_code == 401
    cached_service = module.service
    def uncached_service():
        return Service(path.parent / 'runners/state.sqlite', ConfigFile(path).load())
    report = dict(environment='offline FastAPI TestClient with core auth harness; NOT live gateway',
                  scopes=555, runners=100, provider_calls_during_gets=0)
    for label, factory, count in [('uncached', uncached_service, 20),
                                   ('cached', cached_service, 100)]:
        monkeypatch.setattr(module, 'service', factory)
        for _ in range(3):
            assert client.get(PREFIX + '/inventory', headers=TOKEN).status_code == 200
        loads_before = len(calls)
        durations = []
        for _ in range(count):
            start = time.perf_counter()
            response = client.get(PREFIX + '/inventory', headers=TOKEN)
            durations.append((time.perf_counter() - start) * 1000)
            assert response.status_code == 200
            data = response.json()['data']
            assert len(data['runners']) == 100 and len(data['scopes']) == 555
        report[label] = dict(samples=count, median_ms=statistics.median(durations),
                             p95_ms=sorted(durations)[math.ceil(count * .95) - 1],
                             config_parses=len(calls) - loads_before)
    assert report['cached']['config_parses'] == 0
    assert report['uncached']['config_parses'] == 20
    assert provider.calls == 1
    Path(evidence).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


def test_manifest_and_import_inert(monkeypatch):
    import threading
    import subprocess
    def forbidden(*a, **kw):
        raise AssertionError('import must not start work')
    monkeypatch.setattr(threading.Thread, 'start', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    spec = importlib.util.spec_from_file_location('runners_inert_api', ROOT / 'dashboard/plugin_api.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = json.loads((ROOT / 'dashboard/manifest.json').read_text())
    assert manifest['name'] == 'runners'
    assert manifest['api'] == 'plugin_api.py'
    assert manifest['tab']['hidden'] is True
