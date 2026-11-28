"""B-PL1: real HTTP requests cannot spend the pool via browser/rebound origins."""
import http.client
import http.server
import json
import threading
from types import SimpleNamespace as NS

import pytest
from _load import load

pl = load('pool_llm')


@pytest.fixture
def endpoint(monkeypatch, request, tmp_path):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    token_dir = tmp_path / 'pool-llm'
    token_dir.mkdir(mode=0o700)
    token_file = token_dir / 'serve.token'
    token_file.write_text('boundary-fixture-token')
    token_file.chmod(0o600)
    ready = threading.Event()
    servers, calls = [], []
    original = http.server.ThreadingHTTPServer

    bind_host = getattr(request, 'param', '127.0.0.1')

    def factory(address, handler):
        server = original((bind_host, 0), handler)
        servers.append(server)
        ready.set()
        return server

    monkeypatch.setattr(http.server, 'ThreadingHTTPServer', factory)
    monkeypatch.setattr(pl, 'resolve_model', lambda *args: calls.append('resolve'))
    monkeypatch.setattr(pl, 'load_pool', lambda *args: calls.append('pool'))

    def upstream(*args, **kwargs):
        calls.append('upstream')
        return {'ok': True}

    for name in ('anthropic_messages', 'codex_responses', 'embeddings', '_chat_completions'):
        monkeypatch.setattr(pl, name, upstream)
    thread = threading.Thread(target=pl.serve, daemon=True)
    thread.start()
    assert ready.wait(5)
    server = servers[0]

    def send_request(method='POST', path='/v1/messages', headers=None, body=b'{}'):
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            conn.putrequest(method, path, skip_host=True)
            for key, value in (headers if headers is not None else [('Host', authority), ('x-api-key', 'boundary-fixture-token')]):
                conn.putheader(key, value)
            conn.putheader('Content-Length', str(len(body)))
            conn.endheaders(body)
            response = conn.getresponse()
            return response.status, response.read(), response.getheaders()
        finally:
            conn.close()

    authority = f'127.0.0.1:{server.server_port}'
    try:
        yield NS(request=send_request, authority=authority, calls=calls, server=server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@pytest.mark.parametrize('path', ['/v1/messages', '/v1/responses', '/v1/chat/completions', '/v1/embeddings'])
@pytest.mark.parametrize('host', ['rebind.example:{port}', '127.0.0.1', '127.0.0.1:1', 'localhost:{port}',
                                 '127.0.0.1:{port}.example', 'user@127.0.0.1:{port}'])
def test_rebound_host_refused_before_provider(endpoint, path, host):
    status, _, _ = endpoint.request(path=path, headers=[('Host', host.format(port=endpoint.server.server_port))])
    assert status == 403
    assert endpoint.calls == []


@pytest.mark.parametrize('origin', ['https://example.com', 'null', '', 'http://127.0.0.1'])
@pytest.mark.parametrize('method,path', [('POST', '/v1/messages'), ('POST', '/v1/embeddings'),
                                         ('GET', '/health'), ('OPTIONS', '/v1/messages')])
def test_any_browser_origin_refused(endpoint, origin, method, path):
    status, _, headers = endpoint.request(method, path, [('Host', endpoint.authority), ('Origin', origin)])
    assert status == 403
    assert endpoint.calls == []
    assert not any(k.lower() == 'access-control-allow-origin' for k, _ in headers)


@pytest.mark.parametrize('hosts', [[], ['valid', 'rebind.example'], ['valid', 'valid'], ['rebind.example', 'valid']])
def test_missing_or_duplicate_host_refused(endpoint, hosts):
    headers = [('Host', endpoint.authority if h == 'valid' else h) for h in hosts]
    status, _, _ = endpoint.request(headers=headers)
    assert status == 403
    assert endpoint.calls == []


def test_boundary_precedes_json_parsing(endpoint):
    status, _, _ = endpoint.request(headers=[('Host', 'rebind.example')], body=b'not json')
    assert status == 403
    assert endpoint.calls == []


@pytest.mark.parametrize('path', ['/v1/messages', '/v1/responses', '/v1/chat/completions', '/v1/embeddings'])
def test_nonbrowser_sdk_style_clients_work(endpoint, path):
    status, body, _ = endpoint.request(path=path)
    assert status == 200
    assert json.loads(body) == {'ok': True}
    assert 'upstream' in endpoint.calls


@pytest.mark.parametrize('endpoint', ['0.0.0.0'], indirect=True)
def test_nonloopback_binding_cannot_admit_requests(endpoint):
    status, _, _ = endpoint.request(headers=[('Host', f'0.0.0.0:{endpoint.server.server_port}')])
    assert status == 403
    assert endpoint.calls == []


def test_health_still_works(endpoint):
    status, body, _ = endpoint.request('GET', '/health')
    assert status == 200
    assert json.loads(body) == {'ok': True}
    assert endpoint.calls == []


@pytest.mark.parametrize('auth', [[], [('x-api-key', 'wrong-presented-token')],
    [('x-api-key', 'boundary-fixture-token'), ('Authorization', 'Bearer different-token')],
    [('x-api-key', 'boundary-fixture-token'), ('x-api-key', 'different-token')],
    [('Authorization', 'Basic boundary-fixture-token')],
    [('Authorization', 'Bearer boundary-fixture-token'), ('Authorization', 'Bearer different-token')]])
@pytest.mark.parametrize('method,path', [('POST', '/v1/messages'), ('GET', '/health'), ('OPTIONS', '/')])
def test_auth_refused_before_body_or_provider(endpoint, auth, method, path, capsys):
    status, body, _ = endpoint.request(method, path, [('Host', endpoint.authority)] + auth, b'not json')
    assert status == 401
    assert endpoint.calls == []
    assert 'hermes-pool-llm env' in json.loads(body)['error']['message']
    captured = capsys.readouterr()
    for secret in ['boundary-fixture-token', 'wrong-presented-token', 'different-token']:
        assert secret not in body.decode() + captured.out + captured.err


@pytest.mark.parametrize('auth', [[('x-api-key', 'boundary-fixture-token')],
    [('Authorization', 'Bearer boundary-fixture-token')],
    [('x-api-key', 'boundary-fixture-token'), ('Authorization', 'Bearer boundary-fixture-token')]])
def test_correct_token_served(endpoint, auth, capsys):
    status, body, _ = endpoint.request(headers=[('Host', endpoint.authority)] + auth)
    assert status == 200 and json.loads(body) == {'ok': True}
    assert 'upstream' in endpoint.calls
    captured = capsys.readouterr()
    assert 'boundary-fixture-token' not in body.decode() + captured.out + captured.err


@pytest.mark.parametrize('auth', [[], [('x-api-key', 'wrong-token')]])
@pytest.mark.parametrize('path', ['/v1/messages', '/v1/responses', '/v1/chat/completions', '/v1/embeddings'])
def test_unauthenticated_valid_body_never_calls_upstream(endpoint, auth, path):
    status, _, _ = endpoint.request(path=path, headers=[('Host', endpoint.authority)] + auth)
    assert status == 401
    assert endpoint.calls == []


@pytest.mark.parametrize('sdk', ['anthropic', 'openai'])
def test_real_sdks_accept_token(endpoint, sdk):
    module = pytest.importorskip(sdk)
    base = f'http://{endpoint.authority}'
    if sdk == 'anthropic':
        with module.Anthropic(base_url=base, api_key='boundary-fixture-token', max_retries=0) as client:
            reply = client.messages.create(model='fixture', max_tokens=10, messages=[])
    else:
        with module.OpenAI(base_url=base + '/v1', api_key='boundary-fixture-token', max_retries=0) as client:
            reply = client.responses.create(model='fixture', input='hello')
    assert reply.model_dump()['ok'] is True
    assert 'upstream' in endpoint.calls


def test_auth_does_not_wait_for_body(endpoint):
    conn = http.client.HTTPConnection('127.0.0.1', endpoint.server.server_port, timeout=2)
    try:
        conn.putrequest('POST', '/v1/messages')
        conn.putheader('Content-Length', '999')
        conn.endheaders()  # deliberately never send the promised body
        response = conn.getresponse()
        assert response.status == 401
        response.read()
        assert endpoint.calls == []
    finally:
        conn.close()


def test_serve_creates_private_token_before_binding(monkeypatch, tmp_path, capsys):
    import os
    import stat
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    class Bound(Exception):
        pass
    def bind(*args):
        path = tmp_path / 'pool-llm' / 'serve.token'
        assert path.exists()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert path.stat().st_uid == os.getuid()
        assert len(path.read_text()) >= 43
        assert path.read_text() not in ''.join(capsys.readouterr())
        raise Bound
    monkeypatch.setattr(http.server, 'ThreadingHTTPServer', bind)
    with pytest.raises(Bound):
        pl.serve(port=0)


@pytest.mark.parametrize('unsafe', ['mode', 'symlink', 'owner', 'directory', 'empty'])
def test_serve_refuses_unsafe_token_before_binding(monkeypatch, tmp_path, unsafe):
    import os
    import stat
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    directory = tmp_path / 'pool-llm'
    directory.mkdir(mode=0o700)
    path = directory / 'serve.token'
    path.write_text('private-test-token')
    path.chmod(0o600)
    if unsafe == 'mode':
        path.chmod(0o644)
    elif unsafe == 'symlink':
        path.rename(directory / 'target')
        path.symlink_to(directory / 'target')
    elif unsafe == 'directory':
        path.unlink()
        path.mkdir(mode=0o700)
    elif unsafe == 'empty':
        path.write_text('')
    else:
        # No privilege required: real lstat/fstat metadata except the foreign uid.
        original_lstat, original_fstat = os.lstat, os.fstat
        def foreign(info):
            values = list(info)
            if stat.S_ISREG(info.st_mode):
                values[4] = os.getuid() + 1
            return os.stat_result(values)
        monkeypatch.setattr(os, 'lstat', lambda *a, **kw: foreign(original_lstat(*a, **kw)))
        monkeypatch.setattr(os, 'fstat', lambda *a, **kw: foreign(original_fstat(*a, **kw)))
    monkeypatch.setattr(http.server, 'ThreadingHTTPServer',
                        lambda *a: pytest.fail('unsafe token reached bind'))
    with pytest.raises((ValueError, OSError), match='token'):
        pl.serve(port=0)


def test_env_real_launcher(tmp_path):
    import os
    import pathlib
    import shlex
    import stat
    import subprocess
    import sys
    launcher = pathlib.Path(__file__).resolve().parents[1] / 'bin' / 'hermes-pool-llm'
    # Managed publication fixture; no installed-core/provider claim.
    from test_runtime_binding import fixture_runtime
    _, _, env = fixture_runtime(tmp_path)
    outputs = []
    for args in [('env', '--port', '8791'), ('env',)]:
        result = subprocess.run([sys.executable, str(launcher), *args], env=env,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        shell_env = dict(env, ANTHROPIC_AUTH_TOKEN='stale-fixture-token')
        applied = subprocess.run(['/bin/sh', '-c', result.stdout + '\n'
                                 'test "${ANTHROPIC_AUTH_TOKEN+x}" != x'],
                                 env=shell_env, capture_output=True, text=True, timeout=5)
        assert applied.returncode == 0
        values = dict(shlex.split(line.removeprefix('export '))[0].split('=', 1)
                      for line in result.stdout.splitlines() if line.startswith('export '))
        outputs.append(values)
        port = '8791' if '--port' in args else '8765'
        assert values['ANTHROPIC_BASE_URL'] == f'http://127.0.0.1:{port}'
        assert values['OPENAI_BASE_URL'] == f'http://127.0.0.1:{port}/v1'
        assert values['ANTHROPIC_API_KEY'] == values['OPENAI_API_KEY']
        assert values['ANTHROPIC_API_KEY'] not in result.stderr
    path = pathlib.Path(env['HERMES_HOME']) / 'pool-llm' / 'serve.token'
    assert outputs[0]['ANTHROPIC_API_KEY'] == outputs[1]['ANTHROPIC_API_KEY'] == path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.chmod(0o644)
    result = subprocess.run([sys.executable, str(launcher), 'serve', '--port', '0'],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert 'token' in result.stderr
    assert path.read_text() not in result.stderr + result.stdout
