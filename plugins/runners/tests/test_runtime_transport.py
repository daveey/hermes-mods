"""Offline production-boundary tests. All credentials and hosts are fictional."""
import io
from types import SimpleNamespace

import pytest

from plugins.runners.providers.runtime import (
    GitHubHTTPTransport, RuntimeBoundaryError,
)


class Reply(io.BytesIO):
    def __init__(self, body=b'{"ok": true}', status=200, headers=()):
        super().__init__(body)
        self.status = status
        self.headers = headers
    def getheaders(self):
        return self.headers
    def read1(self, size=-1):
        return self.read(size)


class Connection:
    def __init__(self, reply=None):
        self.reply = reply or Reply()
        self.calls = []
        self.closed = False
        self.sock = SimpleNamespace(settimeout=lambda value: None)
    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
    def getresponse(self):
        return self.reply
    def close(self):
        self.closed = True


def transport(reply=None, **options):
    conn = Connection(reply)
    made = []
    def factory(host, **kwargs):
        made.append((host, kwargs))
        return conn
    return GitHubHTTPTransport(connection_factory=factory, **options), conn, made


def send(client, url='https://api.github.com/orgs/example/actions/runners', **kwargs):
    return client('GET', url, headers={'Authorization': 'Bearer fictional-token'},
                  timeout=2, allow_redirects=False, **kwargs)


def test_http_json_tls_timeout_and_cleanup():
    client, conn, made = transport(Reply(headers=[('ETag', 'fictional-etag')]))
    result = send(client)
    assert result.status == 200 and result.body == {'ok': True}
    assert result.headers['etag'] == 'fictional-etag'
    assert made[0][0] == 'api.github.com' and made[0][1]['timeout'] == 2
    assert made[0][1]['context'].check_hostname
    assert conn.calls[0][0] == ('GET', '/orgs/example/actions/runners')
    assert conn.closed


@pytest.mark.parametrize('url', [
    'http://api.github.com/x', 'https://example.com/x',
    'https://api.github.com.evil.invalid/x', 'https://user@api.github.com/x',
    'https://api.github.com:444/x', 'https://api.github.com/x#fragment',
    'https://api.github.com/x\r\nX: bad', 'https://api.github.com./x',
])
def test_reject_unsafe_url_before_io(url):
    client, conn, made = transport()
    with pytest.raises(RuntimeBoundaryError):
        send(client, url)
    assert not made and not conn.calls


@pytest.mark.parametrize('options', [dict(method='POST'), dict(allow_redirects=True),
                                       dict(timeout=float('nan')), dict(timeout=0),
                                       dict(headers={'Host': 'evil.invalid'})])
def test_reject_unsafe_request_options(options):
    client, _, made = transport()
    args = dict(method='GET', url='https://api.github.com/x', headers={}, timeout=2, allow_redirects=False)
    args.update(options)
    with pytest.raises(RuntimeBoundaryError):
        client(**args)
    assert not made


def test_redirect_never_followed_or_body_decoded():
    client, conn, made = transport(Reply(b'not json', 302, [('Location', 'https://evil.invalid')]))
    assert send(client).status == 302
    assert len(made) == 1 and len(conn.calls) == 1 and conn.closed


@pytest.mark.parametrize('body,headers', [(b'123456789', []), (b'{}', [('Content-Length', '9000')]),
                                        (b'{}', [('Content-Encoding', 'gzip')]), (b'invalid', [])])
def test_bounded_body_and_invalid_payload(body, headers):
    client, conn, _ = transport(Reply(body, headers=headers), max_body_bytes=8)
    with pytest.raises(RuntimeBoundaryError):
        send(client)
    assert conn.closed


def test_http_errors_are_sanitized():
    client, conn, _ = transport()
    conn.request = lambda *a, **k: (_ for _ in ()).throw(OSError('fictional-token private url'))
    with pytest.raises(RuntimeBoundaryError) as exc:
        send(client)
    assert str(exc.value) == 'http_transport_failed' and exc.value.__suppress_context__
    assert conn.closed


def test_body_deadline_and_timeout_cap():
    now = [0.0]
    client, conn, made = transport(clock=lambda: now[0], max_timeout=1)
    def expired_response():
        now[0] = 2.0
        return conn.reply
    conn.getresponse = expired_response
    with pytest.raises(RuntimeBoundaryError):
        send(client)
    assert made[0][1]['timeout'] == 1 and conn.closed


def test_real_adapter_contract_with_production_boundaries():
    from plugins.runners.providers.base import Credential
    from plugins.runners.providers.github_actions import GitHubActions
    ref = 'cred_' + '1' * 16
    def resolver(name):
        if name != ref:
            raise RuntimeBoundaryError('credential_unavailable')
        return Credential('installation-example', 'fictional-token')
    client, conn, _ = transport(Reply(b'{"total_count":0,"runners":[]}'))
    api = GitHubActions(transport=client, credential_resolver=lambda name: resolver('unknown'))
    scope = dict(id='scope_' + '1' * 16, provider='github_actions', endpoint='https://api.github.com',
                 kind='organization', name='example', credential_ref=ref, permissions=[])
    result = api.enumerate(scope)
    assert result.error == 'credential_unavailable' and not conn.calls
    api.credential_resolver = resolver
    result = api.enumerate(scope)
    assert result.complete and result.rows == () and result.error is None
