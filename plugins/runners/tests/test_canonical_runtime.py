"""Exercise real transport decoding, provider budgets, and config reconciliation."""
import json
import pytest
from plugins.runners.tests.test_runtime_transport import Reply, Connection
from plugins.runners.tests.test_github import scope, Harness, runner, response
from plugins.runners.tests.test_config import BASE
from plugins.runners.config import normalize
from plugins.runners.providers.runtime import GitHubHTTPTransport
from plugins.runners.providers.github_actions import GitHubActions
from plugins.runners.providers.base import Credential, HTTPResponse
from plugins.runners.scripts.reconcile_scopes import reconcile


def client(replies):
    connections = []
    def factory(*a, **kw):
        conn = Connection(replies.pop(0)); connections.append(conn)
        return conn
    return GitHubHTTPTransport(connection_factory=factory), connections


def test_transport_to_inventory_preserves_only_safe_numeric_route():
    transport, calls = client([
        Reply(json.dumps({'url': 'https://api.github.com/repositories/42/actions/runners?per_page=100&page=1', 'message': 'fictional-secret'}).encode(), 301),
        Reply(b'{"total_count": 0, "runners": []}')])
    adapter = GitHubActions(transport=transport, credential_resolver=lambda ref: Credential(ref, 'fictional-token'))
    assert adapter.enumerate(scope()).complete
    assert len(calls) == 2
    assert calls[1].calls[0][0][1] == '/repositories/42/actions/runners?per_page=100&page=1'


def test_transport_to_generator_and_both_hops_budgeted():
    transport, calls = client([
        Reply(b'{"url":"https://api.github.com/repositories/42","message":"fictional-secret"}', 301),
        Reply(b'{"id":42,"full_name":"example/new"}')])
    resolver = lambda ref: Credential(ref, 'fictional-token')
    adapter = GitHubActions(transport=transport, credential_resolver=resolver, max_cycle_requests=2)
    saved = []
    adapter.state_sink = lambda state: saved.append(state)
    config = normalize(BASE); config['scopes'] = [scope()]
    updated, report = reconcile(config, {}, transport, resolver, adapter=adapter, pause=lambda _: None)
    assert updated['scopes'][0]['name'] == 'example/new' and not report['errors']
    assert adapter._cycle_remaining == 0 and len(calls) == 2
    assert len(saved) == 4  # reserve and settle each HTTP request
    assert 'fictional-secret' not in repr(saved)


def test_generator_honors_persisted_credential_backoff_before_io():
    h = Harness([])
    h.api.restore_state({scope()['credential_ref']: {'until': h.now + 60}})
    config = normalize(BASE); config['scopes'] = [scope()]
    with pytest.raises(RuntimeError, match='backoff'):
        reconcile(config, {}, h.api.transport, h.api.credential_resolver, adapter=h.api, pause=lambda _: None)
    assert not h.calls


@pytest.mark.parametrize('url', ['https://evil.invalid/repositories/42', 'https://api.github.com/repositories/42?token=fictional-secret'])
def test_transport_sanitizes_untrusted_301_body(url):
    transport, _ = client([Reply(json.dumps({'url': url, 'message': 'fictional-secret'}).encode(), 301)])
    result = transport('GET', 'https://api.github.com/repos/example/old', headers={}, timeout=2, allow_redirects=False)
    assert result.body == {} and 'fictional-secret' not in repr(result)


def test_numeric_alias_pagination_and_no_org_alias():
    replies = []
    for page in (1, 2):
        replies += [HTTPResponse(301, {}, {'url': f'https://api.github.com/repositories/42/actions/runners?per_page=100&page={page}'}),
                    response([runner(i) for i in range(1, 101)] if page == 1 else [runner(101)], total=101)]
    h = Harness(replies)
    result = h.api.enumerate(scope())
    assert result.complete and len(result.rows) == 101 and len(h.calls) == 4
    h = Harness([HTTPResponse(301, {}, {'url': 'https://api.github.com/repositories/42/actions/runners?per_page=100&page=1'})])
    assert h.api.enumerate(scope('organization', 'example')).error == 'moved'
    assert len(h.calls) == 1
