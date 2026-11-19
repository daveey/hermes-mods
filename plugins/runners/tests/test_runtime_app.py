"""Offline App grants: real isolated child/signing, fictional HTTP/broker only."""
import base64
import builtins
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
import time

import pytest

from plugins.runners.providers import github_app as app
from plugins.runners.providers import runtime


SPEC = app.GitHubAppInstallation('EXAMPLE_APP_ID', 'EXAMPLE_APP_KEY_B64', '123',
                                 ('example/project',), ('example',))


def credential(spec=SPEC):
    return runtime.BrokerCredential('user/example-app', 'UNUSED_APP_TOKEN', spec.quota_key, app=spec)


@pytest.mark.parametrize('url,allowed', [
    ('/repos/example/project/actions/runners?per_page=100&page=1', True),
    ('/repos/EXAMPLE/PROJECT/actions/runs?status=queued&per_page=100&page=1', True),
    ('/repos/example/project/actions/runs/123/jobs?per_page=100&page=2', True),
    ('/orgs/example/actions/runners?per_page=100&page=1', True),
    ('/repos/example/other/actions/runners', False),
    ('/repos/example/project/contents/secrets', False),
    ('/repos/example/project/actions/workflows/ci.yml/dispatches', False),
    ('/orgs/other/actions/runners', False),
    ('/repositories/123/actions/runners', False),
    ('/repos/example/project%2fother/actions/runners', False),
    ('/repos/example/project/../other/actions/runners', False),
])
def test_app_exact_route_coverage(url, allowed):
    assert SPEC.permits('https://api.github.com' + url) is allowed


@pytest.mark.parametrize('changes', [
    {'repositories': ('example/project', 'other/project')},
    {'repositories': ('example/project', 'EXAMPLE/PROJECT')},
    {'repositories': ('example/..',)},
    {'repositories': ['example/project']},
    {'repositories': (), 'organizations': ()},
    {'installation_id': '0123'},
    {'app_id_env': 'bad-name'},
    {'private_key_env': 'EXAMPLE_APP_ID'},
])
def test_invalid_app_config(changes):
    with pytest.raises(ValueError):
        runtime.BrokerVendGitHub({'app': credential(replace(SPEC, **changes))})


def test_keys_are_installation_stable_and_cannot_alias_pat():
    refs = {'app': credential(), 'pat': runtime.BrokerCredential('user/example', 'GH_TOKEN', 'global-pat')}
    client = runtime.BrokerVendGitHub(refs)
    assert len(client._handles) == 2
    assert SPEC.quota_key == replace(SPEC, repositories=('example/second',)).quota_key
    with pytest.raises(ValueError):
        runtime.BrokerVendGitHub({'bad': replace(credential(), key='global-pat')})
    with pytest.raises(ValueError):
        runtime.BrokerVendGitHub({'bad': runtime.BrokerCredential('user/example', 'GH_TOKEN', SPEC.quota_key)})


@pytest.fixture
def pem():
    result = subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True)
    return result.stdout


def test_stdlib_memfd_signer_verifies_real_jwt(pem, monkeypatch):
    original = builtins.__import__
    def no_crypto(name, *args, **kwargs):
        if name.startswith('cryptography'):
            raise ImportError()
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_crypto)
    jwt = app._jwt('42', pem, 1000)
    head, payload, signature = jwt.split('.')
    decode = lambda value: base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    assert json.loads(decode(payload)) == {'iat': 940, 'exp': 1300, 'iss': '42'}
    public = subprocess.run(['openssl', 'pkey', '-pubout'], input=pem,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True).stdout
    fds = [os.memfd_create('app-test-public'), os.memfd_create('app-test-signature')]
    try:
        os.write(fds[0], public)
        os.write(fds[1], decode(signature))
        result = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', '/proc/self/fd/' + str(fds[0]),
                                 '-signature', '/proc/self/fd/' + str(fds[1])],
                                input=(head + '.' + payload).encode(), pass_fds=tuple(fds),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert result.returncode == 0
    finally:
        for fd in fds:
            os.close(fd)


def grant(now=1000):
    return {'token': 'fictional-installation-token', 'permissions': SPEC.permissions,
            'repositories': [{'full_name': 'example/project'}],
            'expires_at': datetime.fromtimestamp(now + 3600, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}


@pytest.mark.parametrize('change', [None, 'write', 'extra-repo', 'missing-repo', 'expired', 'far-expiry', 'no-zone', 'bad-token'])
def test_mint_validates_grant_and_consumes_secrets(monkeypatch, pem, change):
    monkeypatch.setenv(SPEC.app_id_env, '42')
    monkeypatch.setenv(SPEC.private_key_env, base64.b64encode(pem).decode())
    response = grant()
    if change == 'write': response['permissions'] = dict(SPEC.permissions, actions='write')
    if change == 'extra-repo': response['repositories'].append({'full_name': 'example/other'})
    if change == 'missing-repo': response['repositories'] = []
    if change == 'expired': response['expires_at'] = '1970-01-01T00:00:01Z'
    if change == 'far-expiry': response['expires_at'] = '2099-01-01T00:00:00Z'
    if change == 'no-zone': response['expires_at'] = '1970-01-01T01:16:40'
    if change == 'bad-token': response['token'] = 'secret\nunsafe'
    exchange = lambda jwt, spec, timeout: response
    if change is None:
        assert app.mint_installation_token(SPEC, clock=lambda: 1000, exchange=exchange) == ('fictional-installation-token', 4600)
    else:
        with pytest.raises(ValueError):
            app.mint_installation_token(SPEC, clock=lambda: 1000, exchange=exchange)
    assert SPEC.app_id_env not in os.environ and SPEC.private_key_env not in os.environ


SITE = '''
import base64, datetime, http.client, json, os
class Response:
    def __init__(self, status, body):
        self.status, self.body = status, json.dumps(body).encode()
    def getheader(self, name, default=None): return default
    def getheaders(self): return [('ETag', '"app-etag"'), ('X-RateLimit-Remaining', '4321')]
    def read(self, size): return self.read1(size)
    def read1(self, size):
        out, self.body = self.body[:size], self.body[size:]
        return out
class Connection:
    sock = None
    def __init__(self, *args, **kwargs): pass
    def request(self, method, path, body=None, headers=None):
        if method == 'POST':
            data = json.loads(body)
            assert path == '/app/installations/123/access_tokens'
            assert data == {'repositories': ['project'], 'permissions': {'metadata': 'read', 'actions': 'read', 'administration': 'read', 'organization_self_hosted_runners': 'read'}}
            assert len(headers['authorization'].split('.')) == 3
            assert 'EXAMPLE_APP_KEY_B64' not in os.environ
            expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
            repos = [] if os.environ.get('APP_BAD_GRANT') else [{'full_name': 'example/project'}]
            self.response = Response(201, {'token': 'fictional-child-only-token', 'permissions': data['permissions'],
                'repositories': repos, 'expires_at': expiry.strftime('%Y-%m-%dT%H:%M:%SZ')})
        else:
            assert headers['authorization'] == 'Bearer fictional-child-only-token'
            assert 'GH_TOKEN' not in os.environ and 'GITHUB_TOKEN' not in os.environ
            self.response = Response(200, {'child': os.getpid(), 'etag': headers.get('if-none-match')})
    def getresponse(self): return self.response
    def close(self): pass
http.client.HTTPSConnection = Connection
'''


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    (tmp_path / 'sitecustomize.py').write_text(SITE)
    broker = tmp_path / 'broker.py'
    broker.write_text('''import base64, os, subprocess, sys
assert 'EXAMPLE_APP_KEY_B64' not in os.environ
assert 'GH_TOKEN' not in os.environ
assert sys.argv[1:4] == ['exec', '--scope', 'user/example-app']
key = subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True).stdout
os.environ['EXAMPLE_APP_ID'] = '42'
os.environ['EXAMPLE_APP_KEY_B64'] = base64.b64encode(key).decode()
command = sys.argv[sys.argv.index('--') + 1:]
os.execvpe(command[0], command, os.environ)
''')
    monkeypatch.setenv('PYTHONPATH', str(tmp_path))
    monkeypatch.setenv('GH_TOKEN', 'fictional-ambient-pat')
    monkeypatch.setenv(SPEC.private_key_env, 'fictional-ambient-key')
    seen = []
    real = subprocess.Popen
    def spawn(argv, **kwargs):
        seen.append((argv, kwargs))
        return real(argv, **kwargs)
    client = runtime.BrokerVendGitHub({'app': credential(), 'alias': credential()},
                                     broker_command=(sys.executable, str(broker)), popen=spawn, start_timeout=10)
    yield client, seen
    client.close()


def get(client, token, path='/repos/example/project/actions/runners?per_page=100&page=1'):
    return client.transport('GET', 'https://api.github.com' + path,
                            headers={'Authorization': 'Bearer ' + token, 'If-None-Match': '"cached"'},
                            timeout=10, allow_redirects=False)


def test_real_child_mints_keeps_secrets_and_etag_transport(isolated):
    client, seen = isolated
    resolved = client('app')
    assert client('alias') == resolved
    assert resolved.key == SPEC.quota_key and 'fictional-child-only-token' not in resolved.token
    response = get(client, resolved.token)
    assert response.status == 200 and response.body['child'] != os.getpid()
    assert response.body['etag'] == '"cached"' and response.headers['etag'] == '"app-etag"'
    assert get(client, resolved.token, '/orgs/example/actions/runners').status == 200
    argv, options = seen[0]
    assert 'fictional' not in repr(argv)
    assert SPEC.private_key_env not in options['env']
    assert options['stdout'] == options['stderr'] == subprocess.DEVNULL
    assert len(seen) == 1
    with pytest.raises(runtime.RuntimeBoundaryError):
        get(client, resolved.token, '/repos/example/uncovered/actions/runners')
    with pytest.raises(runtime.RuntimeBoundaryError):
        client.api(resolved, 'POST', '/repos/example/project/actions/workflows/ci.yml/dispatches', body={'ref': 'main'})


def test_bad_app_grant_fails_closed_without_pat_or_retry(isolated, monkeypatch):
    client, seen = isolated
    monkeypatch.setenv('APP_BAD_GRANT', '1')
    for ref in ('app', 'alias', 'app'):
        with pytest.raises(runtime.RuntimeBoundaryError, match='^credential_unavailable$'):
            client(ref)
    assert len(seen) == 1 and not client._sessions


@pytest.mark.parametrize('dispatch', [False, True])
def test_child_rechecks_coverage_and_read_only_even_without_parent_validation(isolated, dispatch):
    client, _ = isolated
    client('app')
    session = next(iter(client._sessions.values()))
    if dispatch:
        request = dict(method='POST', path='/repos/example/project/actions/workflows/ci.yml/dispatches',
                       body={'ref': 'main'}, timeout=10, max_bytes=1024)
    else:
        request = dict(url='https://api.github.com/repos/example/other/actions/runners',
                       headers={}, timeout=10, max_body_bytes=1024, max_timeout=10)
    runtime._send_frame(session.channel, request)
    assert runtime._recv_frame(session.channel, 4096, time.monotonic() + 10) is None
    assert session.process.wait(timeout=10) != 0
