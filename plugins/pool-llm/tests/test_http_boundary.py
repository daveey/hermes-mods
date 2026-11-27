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
def endpoint(monkeypatch, request):
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
            for key, value in (headers if headers is not None else [('Host', authority)]):
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
