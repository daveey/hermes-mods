"""Actual private runtime parser accepts explicit existing-App grants only."""
from dataclasses import asdict
import json
import pytest
from plugins.runners.providers.runtime import BrokerCredential, BrokerVendGitHub
from plugins.runners.scripts.run_collector import load_runtime


def mapping():
    return dict(scope='user/example-app', env_var='UNUSED_APP_TOKEN',
                key='github-app-installation:123', app=dict(
                    app_id_env='EXAMPLE_APP_ID', private_key_env='EXAMPLE_APP_KEY_B64',
                    installation_id='123', repositories=['example/project'], organizations=['example']))


def test_runtime_parser_accepts_app_without_vending(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('configuration load must not vend credentials')
    monkeypatch.setattr(BrokerVendGitHub, '_start', forbidden)
    value = dict(credentials={'app': mapping()}, probes={}, hourly_request_cap=25)
    path = tmp_path / 'runtime.json'
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    loaded = load_runtime(path)
    spec = BrokerCredential.from_mapping(loaded['credentials']['app'])
    assert spec.app.repositories == ('example/project',)
    assert spec.key == spec.app.quota_key
    assert loaded == value  # no mutation of lists to tuples in caller data
    assert loaded['hourly_request_cap'] == 25


@pytest.mark.parametrize('field,value', [('repositories', 'example/project'),
    ('organizations', 'example'), ('installation_id', 123), ('extra', True)])
def test_runtime_parser_rejects_malformed_app(tmp_path, field, value):
    spec = mapping()
    spec['app'][field] = value
    path = tmp_path / 'runtime.json'
    path.write_text(json.dumps(dict(credentials={'app': spec}, probes={})))
    path.chmod(0o600)
    with pytest.raises((ValueError, RuntimeError)):
        load_runtime(path)


@pytest.mark.parametrize('route,ref,valid', [
    ('repository:example/project', 'app', True),
    ('organization:example', 'app', True),
    ('repository:example/other', 'app', False),
    ('organization:other', 'app', False),
    ('repository:Example/project', 'app', False),
    ('enterprise:example', 'app', False),
    ('repository:example/project', 'pat', False),
    ('repository:example/project', 'missing', False),
])
def test_inventory_routes_validate_existing_app_coverage(tmp_path, route, ref, valid):
    value = dict(credentials={'app': mapping(), 'pat': dict(
        scope='user/example', env_var='GH_TOKEN', key='shared-user')}, probes={},
        inventory_credentials={route: ref})
    path = tmp_path / 'runtime.json'
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    if valid:
        assert load_runtime(path) == value
    else:
        with pytest.raises(RuntimeError):
            load_runtime(path)


def test_inventory_route_does_not_change_original_scope_or_metadata_ref():
    from test_github import Harness, scope, response
    from plugins.runners.providers.base import Credential
    s = scope()
    refs = []
    h = Harness([response()], inventory_credentials={s['kind'] + ':' + s['name'].lower(): 'app'})
    def resolver(ref):
        refs.append(ref)
        return Credential(ref, 'opaque')
    h.api.credential_resolver = resolver
    assert h.api.enumerate(s).complete
    assert refs == ['app']
    assert h.api._credential(s).key == s['credential_ref']
    assert refs == ['app', s['credential_ref']]


def test_inventory_app_failure_never_falls_back_to_pat():
    from test_github import Harness, scope
    s = scope()
    refs = []
    h = Harness([], inventory_credentials={s['kind'] + ':' + s['name'].lower(): 'app'})
    def resolver(ref):
        refs.append(ref)
        raise RuntimeError('unavailable')
    h.api.credential_resolver = resolver
    assert h.api.enumerate(s).error == 'credential_unavailable'
    assert refs == ['app'] and not h.calls


def test_mapping_preserves_existing_pat_shape():
    value = dict(scope='user/example', env_var='GH_TOKEN', key='shared-user')
    spec = BrokerCredential.from_mapping(value)
    assert spec.app is None
    assert asdict(spec) == dict(value, app=None)
