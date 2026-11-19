"""A moved installation scope must not poison its sibling's quota bucket."""
from test_github import Harness, scope, response
from plugins.runners.providers.base import Credential, HTTPResponse


def test_installation_redirect_is_scope_local_and_never_follows_numeric_alias():
    h = Harness([])
    h.api.credential_resolver = lambda _: Credential(
        'github-app-installation:123', 'opaque', quota_kind='installation')
    def send(method, url, **kwargs):
        h.calls.append(url)
        if '/repositories/' in url:
            raise ValueError('outside exact App allowlist')
        if '/repos/example/old/' in url:
            return HTTPResponse(301, {}, {'url':
                'https://api.github.com/repositories/123/actions/runners?per_page=100&page=1'})
        return response()
    h.api.transport = send
    for _ in range(20):
        moved = h.api.enumerate(scope(name='example/old'))
        sibling = h.api.enumerate(scope(name='example/sibling'))
        assert not moved.complete and moved.error == 'moved'
        assert sibling.complete
        h.now += 5
    assert not any('/repositories/' in url for url in h.calls)
    assert sum('/example/sibling/' in url for url in h.calls) == 20
    assert h.api.export_state()['github-app-installation:123']['failures'] == 0
