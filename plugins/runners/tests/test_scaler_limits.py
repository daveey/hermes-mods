"""Authenticated HTTP -> actual argv contract; no AWS or production writes."""
import json
import subprocess
from types import SimpleNamespace

import pytest
from test_api import api, PREFIX, TOKEN  # noqa: F401 -- shared real-core auth fixture


@pytest.fixture
def cli(api, monkeypatch):
    module, client, _ = api
    calls = []
    answer = SimpleNamespace(returncode=0, stdout=json.dumps(
        dict(ok=True, min=1, max=5, active=2, asg_max=5)))
    monkeypatch.setattr('shutil.which', lambda name: '/example/bin/hermes-workers')
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return answer
    monkeypatch.setattr(subprocess, 'run', run)
    return client, calls, answer


def test_accept_returns_cli_readback(cli):
    client, calls, _ = cli
    response = client.post(PREFIX + '/scaler/limits', headers=TOKEN, json={'min': 1, 'max': 5})
    assert response.status_code == 200
    assert response.json() == dict(contract_version=1, ok=True, min=1, max=5, active=2, asg_max=5)
    assert calls == [(['/example/bin/hermes-workers', 'scale', '--min', '1', '--max', '5', '--json'],
                      dict(capture_output=True, text=True, timeout=45, check=False))]


def test_get_normalizes_status_without_writing(cli):
    client, calls, answer = cli
    answer.stdout = json.dumps(dict(min=0, max=0, active=2, asg={'desired': 2, 'max': 0}))
    response = client.get(PREFIX + '/scaler/limits', headers=TOKEN)
    assert response.status_code == 200
    assert response.json() == dict(contract_version=1, ok=True, min=0, max=0, active=2, asg_max=0)
    assert calls[0][0] == ['/example/bin/hermes-workers', 'status', '--json']


@pytest.mark.parametrize('payload', [
    {'min': 3, 'max': 2}, {'min': -1, 'max': 5}, {'min': 0, 'max': 6},
    {'min': 6, 'max': 6}, {'min': 0.0, 'max': 5}, {'min': 0, 'max': 5.0},
    {'min': True, 'max': 5}, {'min': 0, 'max': False}, {'min': '1', 'max': 5},
    {'min': None, 'max': 5}, {'min': 0}, {'max': 5}, {},
    {'min': 0, 'max': 5, 'host': 'example'},
])
def test_invalid_limits_never_invoke_cli(cli, payload):
    client, calls, _ = cli
    response = client.post(PREFIX + '/scaler/limits', headers=TOKEN, json=payload)
    assert response.status_code == 400
    assert calls == []


@pytest.mark.parametrize('method', ['get', 'post'])
def test_non_scaler_host_is_unsupported(cli, method):
    client, _, answer = cli
    answer.returncode, answer.stdout = 3, 'not the scaler host'
    response = getattr(client, method)(PREFIX + '/scaler/limits', headers=TOKEN,
                                     **({'json': {'min': 0, 'max': 5}} if method == 'post' else {}))
    assert response.status_code == 404
    assert response.json()['detail'] == 'scaler_unsupported'


def test_absent_cli_is_unsupported(cli, monkeypatch, tmp_path):
    client, calls, _ = cli
    monkeypatch.setattr('shutil.which', lambda name: None)
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    assert client.get(PREFIX + '/scaler/limits', headers=TOKEN).status_code == 404
    assert calls == []


def test_cli_error_maps_to_400_with_error(cli):
    client, _, answer = cli
    answer.returncode, answer.stdout = 2, json.dumps(dict(ok=False, error='capacity below current desired'))
    response = client.post(PREFIX + '/scaler/limits', headers=TOKEN, json={'min': 0, 'max': 0})
    assert response.status_code == 400
    assert response.json() == {'detail': 'capacity below current desired'}


@pytest.mark.parametrize('stdout,code', [('not json', 0), ('[]', 0), ('{}', 0),
    ('{"ok":false}', 0), ('{"ok":false,"error":"oops"}', 1),
    ('{"min":1,"max":5,"active":1,"asg":null}', 0)])
def test_broken_cli_fails_closed(cli, stdout, code):
    client, _, answer = cli
    answer.returncode, answer.stdout = code, stdout
    assert client.get(PREFIX + '/scaler/limits', headers=TOKEN).status_code == 503


def test_timeout_maps_to_503(cli, monkeypatch):
    client, _, _ = cli
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired('hermes-workers', 45)
    monkeypatch.setattr(subprocess, 'run', timeout)
    assert client.get(PREFIX + '/scaler/limits', headers=TOKEN).status_code == 503


def test_auth_and_origin_protect_write(cli):
    client, calls, _ = cli
    path = PREFIX + '/scaler/limits'
    assert client.get(path).status_code == 401
    assert client.post(path, json={'min': 1, 'max': 5}).status_code == 401
    assert client.post(path, headers={**TOKEN, 'origin': 'https://foreign.example.com'},
                       json={'min': 1, 'max': 5}).status_code == 403
    assert calls == []
