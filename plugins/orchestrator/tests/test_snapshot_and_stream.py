"""Producer contract, classification, and real disk-plugin loader integration."""
import asyncio
import copy
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

PATH = Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py'
spec = importlib.util.spec_from_file_location('orchestrator_test_api', PATH)
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


def fixture_adapter():
    prerequisite = {'id': 'build', 'title': 'Shared build', 'status': 'running'}
    tasks = [
        {'id': 'old', 'title': 'Historical human flag', 'block_kind': 'human_request'},
        {'id': 'human', 'title': 'Current request', 'wait': {'wait_state': 'human'}},
        {'id': 'gate', 'title': 'Admission', 'governor': 'lane_gate'},
        {'id': 'one', 'title': 'First waiter', 'reason': 'dep', 'prerequisite': prerequisite},
        {'id': 'two', 'title': 'Second waiter', 'reason': 'dep', 'prerequisite': prerequisite},
    ]
    panel = {'bundles': {'chat': {'bundle': {'session': 'chat', 'tasks': tasks,
             'lineage': ['chat'], 'model': {'sections': {'running': [{}]}}}}}}
    lanes = {'configured': True, 'lanes': {'work': {'running': 3, 'share': 2,
             'strip': {'cap': 2, 'over': 1}, 'target': 0.5, 'actual': 0.75,
             'usage': {'usd_today': 1.25, 'admitted_today': 4},
             'admit_per_day': 8, 'cost_usd': 2.5}}, 'governors': {'available': True}}
    request = {'requestId': 'ask', 'revision': 2, 'state': 'open', 'title': 'Approve work',
               'owner': {'gatewayId': 'connection', 'profile': 'default', 'storedSessionId': 'chat'}}
    return SimpleNamespace(lanes=lambda: lanes, sessions=lambda: panel,
                           requests=lambda panel: {'chat': [request]}, repair=lambda: {'findings': []},
                           sync=lambda: {'connected': True}, auth=lambda: {'logged_in': True})


def run(adapter=None, **kwargs):
    return asyncio.run(api.snapshot(adapter=adapter or fixture_adapter(), **kwargs))


def test_classification_counts_chains_and_lane_accounting():
    result = run()
    assert [r['key'] for r in result['human']] == ['human', 'request:ask']
    assert 'old' not in {r['key'] for r in result['human']}
    gate = next(r for r in result['machinery'] if r['key'] == 'gate')
    assert gate['label'] == 'Waiting for admission to lane'
    assert result['totals'] == {'human': 2, 'machinery': 1, 'sessions': 2, 'running': 2,
                               'filtered': {'human': 2, 'machinery': 1, 'sessions': 2, 'running': 2}}
    assert len(result['chains']) == 1
    assert result['chains'][0]['prerequisite']['key'] == 'build'
    assert len(result['chains'][0]['waiters']) == 2
    lane = result['lanes'][0]
    assert (lane['running'], lane['cap'], lane['over']) == (3, 2, 1)
    assert lane['gate'] == {'admitted': 4, 'budget': 8}
    assert lane['cost'] == {'today': 1.25, 'window': 2.5}
    assert result['human'][1]['route'] == {'gatewayId': 'connection',
                                          'profile': 'default', 'targetProfile': 'default'}


@pytest.mark.parametrize('mutate', [lambda r: r.update(state='resolved'),
    lambda r: r.update(revision=None), lambda r: r.update(revision=True),
    lambda r: r['owner'].update(storedSessionId='foreign'),
    lambda r: r['owner'].pop('profile'), lambda r: r.update(resolution='approved')])
def test_requests_require_current_owner_and_revision(mutate):
    adapter = fixture_adapter()
    requests = adapter.requests(None)
    mutate(requests['chat'][0])
    adapter.requests = lambda _: requests
    assert run(adapter)['totals']['human'] == 1


def test_unknown_then_recovery_changes_cursor_and_does_not_leak_exception():
    adapter = fixture_adapter()
    healthy = run(adapter)
    original = adapter.sessions
    def fail():
        raise RuntimeError('SECRET_SENTINEL /private/location')
    adapter.sessions = fail
    missing = run(adapter)
    assert missing['totals']['human'] is None
    assert missing['totals']['sessions'] is None
    assert missing['cursor'] != healthy['cursor']
    assert 'SECRET_SENTINEL' not in str(missing)
    assert next(s for s in missing['sources'] if s['name'] == 'session-panel')['state'] == 'unknown'
    adapter.sessions = original
    recovered = run(adapter)
    assert recovered['cursor'] == healthy['cursor']
    assert api.delta(missing, recovered)['changes']['sources'] == recovered['sources']


def test_filtered_counts_are_backend_owned():
    result = run(q='Approve')
    assert result['totals']['human'] == 2
    assert result['totals']['filtered']['human'] == 1
    assert len(result['human']) == 1
    assert result['machinery'] == []


def test_delta_excludes_generated_time_and_unchanged_fields():
    first, second = run(), run()
    assert first['generated_at'] != second['generated_at']
    assert api.delta(first, second) is None
    adapter = fixture_adapter()
    adapter.auth = lambda: {'logged_in': False}
    third = run(adapter)
    patch = api.delta(first, third)
    assert patch['base_cursor'] == first['cursor']
    assert set(patch['changes']) == {'telemetry', 'generated_at', 'machinery', 'totals'}


def test_namespaced_rest_and_real_websocket_delta(monkeypatch):
    adapter = fixture_adapter()
    monkeypatch.setattr(api, 'adapters', adapter)
    monkeypatch.setattr(api, 'POLL_SECONDS', 0.01)
    app = FastAPI()
    app.include_router(api.router, prefix='/api/plugins/orchestrator')
    with TestClient(app) as client:
        response = client.get('/api/plugins/orchestrator/v1/snapshot?q=Approve')
        assert response.status_code == 200
        assert response.json()['totals']['filtered']['human'] == 1
        assert client.get('/v1/snapshot').status_code == 404
        with client.websocket_connect('/api/plugins/orchestrator/v1/stream') as ws:
            first = ws.receive_json()
            assert first['type'] == 'snapshot'
            adapter.auth = lambda: {'logged_in': False}
            ws.send_json({'type': 'refresh'})
            update = ws.receive_json()
            assert update['type'] == 'delta'
            assert update['base_cursor'] == first['snapshot']['cursor']
            assert update['changes']['telemetry']['auth'] == {'logged_in': False}


def test_default_adapter_calls_real_canonical_interfaces(monkeypatch):
    calls = []
    lane_value = {'configured': True, 'lanes': {'work': {}}}
    panel_value = {'bundles': {'chat': {'bundle': {'session': 'chat', 'lineage': ['chat'], 'tasks': []}}}}
    modules = {
        'lanes': SimpleNamespace(rest_view=lambda *args: calls.append(('lanes', args)) or lane_value),
        'session-panel': SimpleNamespace(load_snapshot=lambda: panel_value,
            _load_feed=lambda: SimpleNamespace(_model=lambda: None)),
        'user-steps': SimpleNamespace(panel_requests_many=lambda home, lineages: calls.append(('requests', lineages)) or {}),
        'session-repair': SimpleNamespace(_last=lambda: {'scanned_at': 1, 'findings': []}),
    }
    monkeypatch.setattr(api, 'producer', lambda name, **kw: modules[name])
    monkeypatch.setitem(sys.modules, '_session_panel_feed_core', SimpleNamespace(PROCESS_FEED=None))
    assert api.read_lanes() == lane_value
    snap = api.read_sessions()
    assert api.read_requests(snap) == {}
    assert calls == [('lanes', (6.0, '6h', True)), ('requests', {'chat': ['chat']})]
    assert api.read_repair()['scanned_at'] == 1


def test_sync_response_adapter_and_owner_failure(monkeypatch):
    from starlette.responses import JSONResponse
    async def status():
        return JSONResponse({'connected': True})
    modules = {
        'hub-sync': SimpleNamespace(get_status=status),
        'user-steps': SimpleNamespace(panel_requests_many=lambda *args: {'chat': RuntimeError('missing owner')}),
    }
    monkeypatch.setattr(api, 'producer', lambda name, **kw: modules[name])
    assert asyncio.run(api.read_sync()) == {'connected': True}
    with pytest.raises(RuntimeError, match='ownership lookup unavailable'):
        api.read_requests({'bundles': {}})


def test_default_cached_producers_import_and_report_missing_state(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delitem(sys.modules, '_session_panel_feed_core', raising=False)
    # These use actual sibling modules (not mocked producer implementations).
    with pytest.raises(RuntimeError, match='cached snapshot unavailable'):
        api.read_sessions()
    with pytest.raises(RuntimeError, match='scan unavailable'):
        api.read_repair()
    assert api.read_requests({'bundles': {}}) == {}


def test_real_core_loader_mounts_profile_dependency(tmp_path, monkeypatch):
    # CI without core skips; a locally available but broken loader must FAIL.
    if importlib.util.find_spec('hermes_cli') is None:
        pytest.skip('Hermes core unavailable in standalone plugin CI')
    from hermes_cli import web_server_dashboard as dashboard
    from hermes_cli import plugin_isolation
    monkeypatch.setattr(plugin_isolation, 'isolation_mode', lambda: 'in_process')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    app = FastAPI()
    plugin = {'name': 'orchestrator', 'source': 'user', '_dir': str(PATH.parent), '_api_file': PATH.name}
    assert dashboard._mount_one_plugin_api(app, plugin, {'orchestrator'}, set())
    from fastapi import routing
    contexts = getattr(routing, 'iter_route_contexts', None)
    effective = list(contexts(app.routes)) if contexts else app.routes
    routes = {r.path: r for r in effective if (getattr(r, 'path', '') or '').startswith('/api/plugins/orchestrator')}
    assert set(routes) == {'/api/plugins/orchestrator/v1/snapshot', '/api/plugins/orchestrator/v1/stream'}
    for route in routes.values():
        assert any(d.call is dashboard._plugin_route_secret_scope for d in route.dependant.dependencies)
    loaded = sys.modules['hermes_dashboard_plugin_orchestrator']
    # A canonical sibling mounted by the real loader must be reused, not
    # imported again under an orchestrator-private name (especially panel).
    sibling = {'name': 'session-panel', 'source': 'user',
               '_dir': str(api.ROOT / 'session-panel' / 'dashboard'), '_api_file': 'plugin_api.py'}
    assert dashboard._mount_one_plugin_api(app, sibling, {'session-panel'}, set())
    assert loaded.producer('session-panel') is sys.modules['hermes_dashboard_plugin_session-panel']
    monkeypatch.setattr(loaded, 'adapters', fixture_adapter())
    # Isolate profile scope from this test process's user config while exercising the mounted dependency.
    async def scope():
        yield
    app.dependency_overrides[dashboard._plugin_route_secret_scope] = scope
    with TestClient(app) as client:
        assert client.get('/api/plugins/orchestrator/v1/snapshot').json()['version'] == 1
        with client.websocket_connect('/api/plugins/orchestrator/v1/stream') as ws:
            assert ws.receive_json()['type'] == 'snapshot'
