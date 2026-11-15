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


def test_mapping_preserves_existing_pat_shape():
    value = dict(scope='user/example', env_var='GH_TOKEN', key='shared-user')
    spec = BrokerCredential.from_mapping(value)
    assert spec.app is None
    assert asdict(spec) == dict(value, app=None)
