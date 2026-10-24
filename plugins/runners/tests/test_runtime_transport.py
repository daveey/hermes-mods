"""Offline production-boundary tests. All credentials and hosts are fictional."""
import io
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from plugins.runners.providers.runtime import (
    BrokerCredential, BrokerCredentialResolver, GitHubHTTPTransport, RuntimeBoundaryError,
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


def specs():
    return {'first': BrokerCredential('user/fictional-github', 'GH_TOKEN', 'installation-example'),
            'alias': BrokerCredential('user/fictional-github', 'GH_TOKEN', 'installation-example')}


def fake_runner(calls, token=b'fictional-token'):
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert kwargs['stdout'] == subprocess.DEVNULL and kwargs['stderr'] == subprocess.DEVNULL
        assert kwargs['stdin'] == subprocess.DEVNULL and kwargs['timeout'] > 0
        assert kwargs.get('shell', False) is False
        os.write(kwargs['pass_fds'][0], token)
        return SimpleNamespace(returncode=0)
    return run


def test_broker_exec_pipe_cache_ttl_alias_budget_identity_and_invalidation():
    calls, now = [], [100.0]
    resolver = BrokerCredentialResolver(specs(), runner=fake_runner(calls), clock=lambda: now[0], ttl=5)
    first = resolver('first')
    assert first.token == 'fictional-token' and first.key == resolver('alias').key
    assert 'fictional-token' not in repr(first)
    assert resolver('first') is first and len(calls) == 2
    argv, kwargs = calls[0]
    assert argv[:4] == ['token-broker', 'exec', '--scope', 'user/fictional-github']
    assert 'fictional-token' not in repr(argv)
    assert '--reason' in argv and '--' in argv
    now[0] += 5
    resolver('first')
    assert len(calls) == 3
    resolver.invalidate('first')
    resolver('alias')
    assert len(calls) == 4
    resolver.invalidate(key=first.key)
    resolver('first')
    assert len(calls) == 5


@pytest.mark.parametrize('value', [b'', b'bad\nvalue', b'x' * 4097, b'\xff'])
def test_broker_bad_pipe_values_never_cached(value):
    calls = []
    resolver = BrokerCredentialResolver(specs(), runner=fake_runner(calls, value))
    for _ in range(2):
        with pytest.raises(RuntimeBoundaryError, match='credential_unavailable'):
            resolver('first')
    assert len(calls) == 2


def test_broker_unknown_reference_and_errors_never_leak():
    calls = []
    def fail(*a, **kw):
        calls.append(1)
        raise subprocess.TimeoutExpired('fictional-token', 1, output=b'fictional-token')
    resolver = BrokerCredentialResolver(specs(), runner=fail)
    with pytest.raises(RuntimeBoundaryError, match='credential_unavailable'):
        resolver('unknown')
    assert not calls
    with pytest.raises(RuntimeBoundaryError) as exc:
        resolver('first')
    assert str(exc.value) == 'credential_unavailable' and exc.value.__suppress_context__


def test_real_child_script_uses_only_injected_env_and_pipe():
    # Run only the bridge child, never token-broker or any real credential lookup.
    calls = []
    def run(argv, **kwargs):
        child = argv[argv.index('--') + 1:]
        calls.append(child)
        kwargs['env'] = {'GH_TOKEN': 'fictional-child-token'}
        return subprocess.run(child, **kwargs)
    resolver = BrokerCredentialResolver(specs(), runner=run)
    assert resolver('first').token == 'fictional-child-token'
    assert calls[0][0] == sys.executable


def test_ambient_credential_cannot_substitute_for_broker(monkeypatch):
    monkeypatch.setenv('GH_TOKEN', 'fictional-ambient-token')
    environments = []
    def run(argv, **kwargs):
        environments.append(kwargs.get('env', {}))
        return subprocess.run(argv[argv.index('--') + 1:], **kwargs)
    resolver = BrokerCredentialResolver(specs(), runner=run)
    with pytest.raises(RuntimeBoundaryError, match='credential_unavailable'):
        resolver('first')
    assert environments and 'GH_TOKEN' not in environments[0]


def test_aliases_cannot_split_same_broker_identity_budget():
    references = specs()
    references['alias'] = BrokerCredential('user/fictional-github', 'GH_TOKEN', 'different-budget')
    with pytest.raises(ValueError, match='invalid_broker_reference'):
        BrokerCredentialResolver(references, runner=fake_runner([]))


def test_broker_nonzero_exit_discards_even_valid_pipe_and_closes_fds():
    descriptors = []
    def run(argv, **kwargs):
        descriptors.extend(kwargs['pass_fds'])
        os.write(descriptors[-1], b'fictional-token')
        return SimpleNamespace(returncode=1)
    resolver = BrokerCredentialResolver(specs(), runner=run)
    with pytest.raises(RuntimeBoundaryError, match='credential_unavailable'):
        resolver('first')
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert not resolver._cache


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
    from plugins.runners.providers.github_actions import GitHubActions
    calls = []
    resolver = BrokerCredentialResolver(specs(), runner=fake_runner(calls))
    client, conn, _ = transport(Reply(b'{"total_count":0,"runners":[]}'))
    api = GitHubActions(transport=client, credential_resolver=resolver)
    result = api.enumerate(dict(id='scope_' + '1' * 16, provider='github_actions',
                                endpoint='https://api.github.com', kind='organization',
                                name='example', credential_ref='cred_' + '1' * 16, permissions=[]))
    assert result.error == 'credential_unavailable' and not conn.calls
    references = {'cred_' + '1' * 16: specs()['first']}
    api.credential_resolver = BrokerCredentialResolver(references, runner=fake_runner(calls))
    result = api.enumerate(dict(id='scope_' + '1' * 16, provider='github_actions',
                                endpoint='https://api.github.com', kind='organization',
                                name='example', credential_ref='cred_' + '1' * 16, permissions=[]))
    assert result.complete and result.rows == () and result.error is None


@pytest.mark.parametrize('options', [dict(ttl=0), dict(ttl=3601), dict(ttl=float('nan')),
                                     dict(timeout=0), dict(timeout=301)])
def test_broker_options_are_bounded(options):
    with pytest.raises(ValueError):
        BrokerCredentialResolver(specs(), runner=fake_runner([]), **options)
