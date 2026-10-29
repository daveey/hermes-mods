"""Offline API boundary tests; core auth functions loaded without server startup."""
import ast
import hmac
import importlib.util
import json
import os
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


def resolve_core_auth_source() -> Path:
    """Exact core auth source, never a mock.

    CI sets RUNNERS_CORE_WEB_SERVER to a pinned NousResearch/hermes-agent
    web_server.py. A developer machine falls back to the installed checkout.
    A missing file fails closed so the suite cannot pass by skipping auth.
    """
    override = os.environ.get('RUNNERS_CORE_WEB_SERVER')
    if override:
        path = Path(override)
    else:
        root = os.environ.get('RUNNERS_CORE_ROOT')
        path = (Path(root) / 'hermes_cli/web_server.py') if root else (
            Path.home() / '.hermes/hermes-agent/hermes_cli/web_server.py')
    if not path.is_file():
        raise FileNotFoundError(
            f'core auth source missing at {path}; set RUNNERS_CORE_WEB_SERVER '
            'to hermes_cli/web_server.py (CI pins NousResearch/hermes-agent)')
    return path


def test_missing_core_auth_source_names_the_provision_contract(tmp_path, monkeypatch):
    missing = tmp_path / 'absent.py'
    monkeypatch.setenv('RUNNERS_CORE_WEB_SERVER', str(missing))
    with pytest.raises(FileNotFoundError, match='RUNNERS_CORE_WEB_SERVER'):
        resolve_core_auth_source()


def test_core_auth_source_prefers_the_explicit_override(tmp_path, monkeypatch):
    source = tmp_path / 'web_server.py'
    source.write_text('def _require_token(request):\n    return None\n')
    monkeypatch.setenv('RUNNERS_CORE_WEB_SERVER', str(source))
    assert resolve_core_auth_source() == source


@pytest.fixture
def api(monkeypatch, tmp_path):
    # Execute the exact core auth implementation, not a permissive mock.
    core = resolve_core_auth_source()
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


def test_enrollment_discovery_missing_config_is_not_an_outage(disk_api):
    _, client, path, _, _, _ = disk_api
    path.unlink()
    assert client.get(PREFIX + '/enrollment').status_code == 401
    response = client.get(PREFIX + '/enrollment', headers=TOKEN)
    assert response.status_code == 200
    assert response.json() == {'contract_version': 1, 'profile': 'example', 'enrolled': False, 'capabilities': []}


def test_enrollment_discovery_reports_owner_without_store(disk_api):
    _, client, path, _, config, _ = disk_api
    (path.parent / 'runners/state.sqlite').unlink()
    response = client.get(PREFIX + '/enrollment', headers=TOKEN)
    assert response.status_code == 200
    assert response.json() == {'contract_version': 1, 'profile': 'example', 'enrolled': True,
                               'capabilities': ['inventory', 'inspect', 'refresh'], 'features': {'repair_sessions': False}}
    assert not (path.parent / 'runners/state.sqlite').exists()


def test_enrollment_discovery_does_not_hide_invalid_config(disk_api):
    _, client, path, _, _, _ = disk_api
    path.write_text('schema_version: invalid\n')
    assert client.get(PREFIX + '/enrollment', headers=TOKEN).status_code == 503


def test_enrollment_discovery_fences_wrong_profile(disk_api, monkeypatch):
    module, client, _, _, _, _ = disk_api
    monkeypatch.setattr(module, 'active_profile', lambda: 'other')
    assert client.get(PREFIX + '/enrollment', headers=TOKEN).status_code == 403


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


def test_other_profile_config_failure_does_not_evict_the_owner_config(disk_api, tmp_path):
    """Every profile's fleet poll calls service(); profiles without runners.yaml raise. That used to
    clear the one shared cache entry, so the owner re-parsed its config on each poll."""
    module, client, path, calls, cfg, constants = disk_api
    owner_home = constants.get_hermes_home()
    other = tmp_path / 'other-home'
    other.mkdir()
    for _ in range(3):
        constants.get_hermes_home = lambda: other
        with pytest.raises(OSError):
            module.service()
        constants.get_hermes_home = lambda: owner_home
        module.service()
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


def test_fleet_route_reports_each_section_and_isolates_failures(disk_api, monkeypatch):
    module, client, _, _, _, _ = disk_api
    assert client.get(PREFIX + '/fleet').status_code == 401
    module.FLEET_CACHE_S = 0
    monkeypatch.setattr(module, '_gateway_section', lambda: dict(state='ready', machines=[dict(name='hub', slots=16, running=[])]))
    body = client.get(PREFIX + '/fleet', headers=TOKEN).json()
    assert body['contract_version'] == 1 and body['profile'] == 'example'
    assert body['gateways']['machines'][0]['name'] == 'hub'
    assert body['ci']['state'] in ('ready', 'stale') and body['ci']['machines'] == []
    def down():
        raise ImportError('no core')
    monkeypatch.setattr(module, '_gateway_section', down)
    body = client.get(PREFIX + '/fleet', headers=TOKEN).json()
    assert body['gateways'] == {'state': 'unavailable', 'reason': 'ImportError', 'machines': []}
    assert 'machines' in body['ci']


def test_fleet_route_without_runner_config_still_serves_gateways(disk_api, monkeypatch):
    module, client, path, _, _, _ = disk_api
    path.unlink()
    module.FLEET_CACHE_S = 0
    monkeypatch.setattr(module, '_gateway_section', lambda: dict(state='ready', machines=[]))
    body = client.get(PREFIX + '/fleet', headers=TOKEN).json()
    assert body['ci'] == {'state': 'not_configured', 'machines': []}
    assert body['gateways']['state'] == 'ready'


def test_fleet_route_builds_once_per_cache_window(disk_api, monkeypatch):
    module, client, _, _, _, _ = disk_api
    calls = []
    monkeypatch.setattr(module, '_gateway_section', lambda: calls.append(1) or dict(state='ready', machines=[]))
    module._fleet_cache.clear()
    for _ in range(5):
        assert client.get(PREFIX + '/fleet', headers=TOKEN).status_code == 200
    assert len(calls) == 1
    module._fleet_cache[module.active_profile()]['at'] -= module.FLEET_CACHE_S + 1
    client.get(PREFIX + '/fleet', headers=TOKEN)
    assert len(calls) == 2


PROFILES = 'default alpha alpha-askeve alpha-follow askeve-builder bb-tuner ci-sweeper coach goal-disposition-check mac-ops'.split()


def test_fleet_profiles_do_not_evict_each_other_or_serialize(api, monkeypatch):
    """The Desktop polls every profile at once. A scalar cache keyed by the last profile rebuilt on
    every request, and a global lock held through the build made ten polls wait for ten builds."""
    import threading, time
    module, client, _ = api
    module._fleet_cache.clear()
    current = threading.local()
    monkeypatch.setattr(module, 'active_profile', lambda: current.name)
    builds, running, peak, guard = [], [0], [0], threading.Lock()
    def slow_build(profile):
        with guard:
            builds.append(profile)
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.3)
        with guard:
            running[0] -= 1
        return dict(contract_version=1, profile=profile, gateways={}, ci={})
    monkeypatch.setattr(module, '_build_fleet', slow_build)
    results = {}
    def poll(name):
        current.name = name
        results[name] = module.fleet_view()['profile']  # the route's sync body; threads stand in for the threadpool
    def burst():
        threads = [threading.Thread(target=poll, args=(n,)) for n in PROFILES]
        start = time.monotonic()
        [t.start() for t in threads]
        [t.join() for t in threads]
        return time.monotonic() - start
    first = burst()
    assert results == {n: n for n in PROFILES}
    assert sorted(builds) == sorted(PROFILES)
    assert first < 0.3 * len(PROFILES) / 2, first  # parallel, not ten builds back to back
    assert peak[0] > 1
    assert burst() < 0.3  # every profile served from its own entry; none was evicted by the others
    assert len(builds) == len(PROFILES)


def test_fleet_same_profile_polls_share_one_build(api, monkeypatch):
    import threading, time
    module, client, _ = api
    module._fleet_cache.clear()
    monkeypatch.setattr(module, 'active_profile', lambda: 'default')
    builds = []
    def slow_build(profile):
        builds.append(profile)
        time.sleep(0.2)
        return dict(contract_version=1, profile=profile, gateways={}, ci={})
    monkeypatch.setattr(module, '_build_fleet', slow_build)
    threads = [threading.Thread(target=module.fleet_view) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(builds) == 1


def test_local_cap_parses_config_once_until_it_changes(api, monkeypatch, tmp_path):
    module, _, _ = api
    kdb = types.ModuleType('hermes_cli.kanban_db')
    kdb.kanban_home = lambda: tmp_path
    dispatch = types.ModuleType('hermes_cli.kanban_db_dispatch')
    dispatch.resolve_max_in_progress = lambda configured: configured if configured is not None else 8
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_db', kdb)
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_db_dispatch', dispatch)
    import yaml
    parsed = []
    real = yaml.safe_load
    monkeypatch.setattr(yaml, 'safe_load', lambda text: parsed.append(1) or real(text))
    module._cap_cache = None
    (tmp_path / 'config.yaml').write_text('kanban:\n  max_in_progress: 16\n')
    assert [module._local_cap() for _ in range(10)] == [16] * 10
    assert len(parsed) == 1
    (tmp_path / 'config.yaml').write_text('kanban:\n  max_in_progress: 24\n')
    assert module._local_cap() == 24
    assert len(parsed) == 2


def test_local_cap_reads_the_kanban_home_config_for_every_profile(api, monkeypatch, tmp_path):
    module, _, _ = api
    kdb = types.ModuleType('hermes_cli.kanban_db')
    kdb.kanban_home = lambda: tmp_path
    dispatch = types.ModuleType('hermes_cli.kanban_db_dispatch')
    dispatch.resolve_max_in_progress = lambda configured: configured if configured is not None else 8
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_db', kdb)
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_db_dispatch', dispatch)
    (tmp_path / 'config.yaml').write_text('kanban:\n  max_in_progress: 16\n')
    # A profile's own config (no kanban section) must not matter.
    (tmp_path / 'profiles' / 'other').mkdir(parents=True)
    (tmp_path / 'profiles' / 'other' / 'config.yaml').write_text('model: x\n')
    assert module._local_cap() == 16
    (tmp_path / 'config.yaml').write_text('kanban:\n  max_in_progress: lots\n')
    assert module._local_cap() == 8
    (tmp_path / 'config.yaml').unlink()
    assert module._local_cap() == 8


def _investigate_env(module, monkeypatch, tmp_path, machines=('mini', 'hub')):
    node = types.ModuleType('hermes_cli.kanban_worker_node')
    node.registry = lambda: {'me': 'macbook', 'machines': {m: {'state': 'active'} for m in machines}}
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_worker_node', node)
    pkg = sys.modules.get('hermes_cli') or types.ModuleType('hermes_cli')
    monkeypatch.setitem(sys.modules, 'hermes_cli', pkg)
    monkeypatch.setattr(pkg, 'kanban_worker_node', node, raising=False)
    monkeypatch.setattr(module, '_board_dirs', lambda: [tmp_path])
    module._pending.clear()
    created = []
    def create(args):
        created.append(args)
        return {'ok': True, 'task_id': f't_00000000000{len(created)}', 'status': 'created', 'runs_where': args['placement']}
    monkeypatch.setattr(module, '_create_card', create)
    return created


def _shadow_card(tmp_path, tid, machine, status, created_at, result=None):
    import sqlite3
    db = sqlite3.connect(tmp_path / 'default.db')
    db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT, status TEXT, result TEXT, idempotency_key TEXT, title TEXT, '
               'created_at INTEGER, completed_at INTEGER, human_request TEXT)')
    # Cross-gateway creates arrive without the idempotency key: the title alone identifies the machine.
    key = f'investigate-machine:{machine}:{created_at}' if created_at % 2 == 0 else None
    db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)',
               (tid, status, result, key, f'Investigate machine load: {machine}', created_at, None, None))
    db.commit()
    db.close()


def test_investigation_route_creates_a_card_placed_on_the_machine(api, monkeypatch, tmp_path):
    module, client, _ = api
    created = _investigate_env(module, monkeypatch, tmp_path)
    body = {'machine': 'mini', 'load': {'cpu_load1': 13.8, 'cpus': 8, 'disk_free_gb': 20}}
    assert client.post(PREFIX + '/investigations', json=body).status_code == 401
    res = client.post(PREFIX + '/investigations', json=body, headers=TOKEN)
    assert res.status_code == 202
    assert res.json()['created'] is True and res.json()['investigation']['task'] == 't_000000000001'
    (args,) = created
    assert args['placement'] == 'mini' and args['skills'] == ['machine-investigate', 'five-whys']
    assert args['requires'] == ['terminal', 'file', 'kanban']
    assert args['idempotency_key'].startswith('investigate-machine:mini:')
    assert 'CPU load1 13.8 on 8 cores (172%)' in args['body'] and 'disk 20 GB free' in args['body']
    # A second click before the card reaches the replicas returns the queued one, not a duplicate.
    again = client.post(PREFIX + '/investigations', json=body, headers=TOKEN).json()
    assert again['created'] is False and again['investigation']['task'] == 't_000000000001'
    assert len(created) == 1


def test_investigation_route_reuses_an_open_or_blocked_card_and_restarts_after_done(api, monkeypatch, tmp_path):
    module, client, _ = api
    created = _investigate_env(module, monkeypatch, tmp_path)
    _shadow_card(tmp_path, 't_aaaa00000001', 'hub', 'running', 100)
    res = client.post(PREFIX + '/investigations', json={'machine': 'hub'}, headers=TOKEN).json()
    assert res['created'] is False and res['investigation']['task'] == 't_aaaa00000001' and not created
    _shadow_card(tmp_path, 't_aaaa00000002', 'mini', 'blocked', 101)
    res = client.post(PREFIX + '/investigations', json={'machine': 'mini'}, headers=TOKEN).json()
    assert res['created'] is False and res['investigation']['status'] == 'blocked' and not created
    _shadow_card(tmp_path, 't_aaaa00000003', 'mini', 'done', 200, 'mini: fine now')
    res = client.post(PREFIX + '/investigations', json={'machine': 'mini'}, headers=TOKEN).json()
    assert res['created'] is True and len(created) == 1


def test_investigation_route_refuses_unknown_or_malformed_machines(api, monkeypatch, tmp_path):
    module, client, _ = api
    created = _investigate_env(module, monkeypatch, tmp_path)
    assert client.post(PREFIX + '/investigations', json={'machine': 'nope'}, headers=TOKEN).status_code == 404
    assert client.post(PREFIX + '/investigations', json={'machine': '../x'}, headers=TOKEN).status_code == 400
    assert not created


def test_fleet_rows_carry_their_latest_investigation(api, monkeypatch, tmp_path):
    module, _, _ = api
    _investigate_env(module, monkeypatch, tmp_path)
    _shadow_card(tmp_path, 't_aaaa00000004', 'mini', 'done', 300, 'mini: two BabyBear rpc loops\nmore')
    inv = module._investigate()
    rows = [dict(name='mini'), dict(name='hub')]
    inv.attach(rows, inv.latest([tmp_path], {'mini', 'hub'}), {'hub': {'at': 1000.0, 'task': None}}, 1001.0)
    assert rows[0]['investigation']['verdict'] == 'mini: two BabyBear rpc loops'
    assert rows[1]['investigation']['status'] == 'queued'
