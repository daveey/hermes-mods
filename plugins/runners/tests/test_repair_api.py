"""Repair boundary uses authenticated owner principal, never request identity."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException

spec = importlib.util.spec_from_file_location('repair_api', Path(__file__).parents[1] / 'dashboard/plugin_api.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def test_principal_matches_core_transport_identity():
    request = SimpleNamespace(state=SimpleNamespace(session=SimpleNamespace(provider='oidc', user_id='example')))
    assert api.repair_principal(request) == 'oidc:example'


def test_legacy_token_has_no_invented_principal():
    request = SimpleNamespace(state=SimpleNamespace(token_authenticated=True))
    with pytest.raises(HTTPException) as error:
        api.repair_principal(request)
    assert error.value.status_code == 403


def test_repair_routes_exist_and_status_is_rest_not_unshipped_rpc():
    routes = {(r.path, m) for r in api.router.routes for m in r.methods}
    assert {('/v1/repairs', 'POST'), ('/v1/repairs/{repair_id}', 'GET'),
            ('/v1/repairs/{repair_id}/verify', 'POST'), ('/v1/maintenance', 'POST')} <= routes


from test_repairs import core_types, FakeCore


def test_shared_disk_and_tool_api_domain_registry(tmp_path):
    from test_repair_projection import setup
    cfg, host, now, store, collector, backend = setup(tmp_path)
    second_spec = importlib.util.spec_from_file_location('another_repair_api', Path(__file__).parents[1] / 'dashboard/plugin_api.py')
    second = importlib.util.module_from_spec(second_spec)
    second_spec.loader.exec_module(second)
    first = api.repair_domain(backend)
    try:
        assert second.repair_domain(backend) is first
        core = FakeCore()
        first.core_factory = lambda profile:core
        assert first.register('oidc:example') == second.repair_domain(backend).register('oidc:example')
        assert len(core.resolvers) == 1
    finally:
        first.close()
        store.close()


def test_authenticated_route_domain_roundtrip_and_cas(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_repair_projection import setup
    from plugins.runners.repairs import RepairService
    from plugins.runners.collector import iso
    cfg, host, now, store, collector, backend = setup(tmp_path)
    domain = RepairService(backend, core_factory=lambda profile: FakeCore())
    monkeypatch.setattr(api, 'service', lambda:backend)
    monkeypatch.setattr(api, 'active_profile', lambda:cfg['owner_profile'])
    monkeypatch.setattr(api, 'repair_domain', lambda backend:domain)
    app = FastAPI()
    # Auth itself is covered by test_api; this fixture supplies verified state,
    # never payload identity. Missing/other principals still exercise policy.
    @app.middleware('http')
    async def authenticated(request, call_next):
        user = request.headers.get('x-fixture-user')
        if user:
            request.state.session = SimpleNamespace(provider='oidc', user_id=user)
        return await call_next(request)
    app.dependency_overrides[api.authorize] = lambda:None
    app.include_router(api.router)
    try:
        with TestClient(app) as client:
            assert client.get('/v1/enrollment').json()['features']['repair_sessions'] is False
            headers = {'x-fixture-user':'example'}
            assert client.get('/v1/enrollment', headers=headers).json()['features']['repair_sessions'] is True
            rid = backend.inventory()['data']['runners'][0]['id']
            detail = client.get('/v1/runners/'+rid, headers=headers).json()
            payload = dict(entity_id=rid, incident_id=detail['data']['incidents'][0]['id'],
                idempotency_key='request_0000000000000001', mode='diagnose', expected_owner=domain._owner())
            assert client.post('/v1/repairs', json=payload).status_code == 403
            response = client.post('/v1/repairs', json=payload, headers=headers)
            assert response.status_code == 202, response.text
            repair = response.json()['data']['repair']
            assert client.post('/v1/repairs', json=payload, headers=headers).json()['data']['repair']['id'] == repair['id']
            path = '/v1/repairs/'+repair['id']
            assert client.get(path, headers={'x-fixture-user':'other'}).status_code == 403
            assert client.get('/v1/runners/'+rid).json()['data']['repairs'] == []
            assert client.get('/v1/runners/'+rid, headers=headers).json()['data']['repairs'][0]['id'] == repair['id']
            domain.start(repair['id'], principal='oidc:example')
            assert client.get(path, headers=headers).json()['data']['repair']['session']['stored_session_id'] == 'chat_first'
            assert client.post(path+'/verify', headers=headers, json=dict(expected_owner=domain._owner(), idempotency_key='verify_0000000000000001')).status_code == 202
            for state in ('active','inactive'):
                revision = client.get('/v1/maintenance', headers=headers).json()['data']['revision']
                maintenance = dict(entity_id=host['resource_id'], state=state, reason='Inspection',
                    expires_at=iso(1100) if state=='active' else None, expected_config_revision=revision,
                    expected_owner=domain._owner())
                assert client.post('/v1/maintenance', headers=headers, json=maintenance).status_code == 200
                assert client.post('/v1/maintenance', headers=headers, json=maintenance).status_code == 409
                assert client.get('/v1/inventory').json()['data']['hosts'][0]['maintenance']['state'] == state
    finally:
        domain.close()
        store.close()

