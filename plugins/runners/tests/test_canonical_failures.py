import pytest
from plugins.runners.tests.test_runtime_transport import Reply, transport
from plugins.runners.tests.test_github import Harness, scope, response
from plugins.runners.providers.base import HTTPResponse, Credential
from plugins.runners.providers.github_actions import GitHubActions


@pytest.mark.parametrize('body,headers', [(b'', []), (b'not json', []),
    (b'{}', [('Content-Length', '50000000')]), (b'{}', [('Content-Encoding', 'gzip')]),
    (b'{}', [('Content-Length', '4')])])
def test_bad_301_body_is_still_scope_local(body, headers):
    client, _, _ = transport(Reply(body, 301, headers))
    h = GitHubActions(transport=client, credential_resolver=lambda ref: Credential(ref, 'fictional-token'))
    assert h.enumerate(scope()).error == 'moved'
    state = h.export_state()
    assert state[scope()['credential_ref']]['until'] == 0
    assert any(v['cause'] == 'moved' for v in state.values())


@pytest.mark.parametrize('status,code', [(403, 'forbidden'), (404, 'not_found'), (410, 'not_found'), (301, 'moved')])
def test_numeric_target_cooldown_blocks_original_alias_without_io(status, code):
    h = Harness([HTTPResponse(301, {}, {'url': 'https://api.github.com/repositories/42/actions/runners?per_page=100&page=1'}), response(status=status)])
    assert h.api.enumerate(scope()).error == code
    assert len(h.calls) == 2
    assert h.api.enumerate(scope()).error == code
    assert len(h.calls) == 2
    after_restart = Harness([])
    after_restart.api.restore_state(h.api.export_state())
    assert after_restart.api.enumerate(scope()).error == code
    assert not after_restart.calls
