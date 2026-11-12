from copy import deepcopy
from types import SimpleNamespace
import pytest
from plugins.runners.providers.base import HTTPResponse, Credential
from plugins.runners.canonical import canonical_repository, CanonicalError
from plugins.runners.scripts.reconcile_scopes import reconcile
from plugins.runners.tests.test_github import Harness, scope, response
from plugins.runners.tests.test_config import BASE
from plugins.runners.config import normalize


def test_numeric_repository_inventory_redirect_is_bounded():
    h = Harness([HTTPResponse(301, {}, {'url': 'https://api.github.com/repositories/42/actions/runners?per_page=100&page=1'}), response()])
    assert h.api.enumerate(scope()).complete
    assert h.calls[1][1] == 'https://api.github.com/repositories/42/actions/runners?per_page=100&page=1'
    assert all(c[2]['allow_redirects'] is False for c in h.calls)


@pytest.mark.parametrize('url', ['https://evil.invalid/repositories/42/actions/runners?per_page=100&page=1',
    'https://api.github.com/repos/other/repo/actions/runners?per_page=100&page=1',
    'https://api.github.com/repositories/42/actions/runs?per_page=100&page=1',
    'https://api.github.com/repositories/42/actions/runners?per_page=100&page=2'])
def test_inventory_rejects_other_redirects(url):
    h = Harness([HTTPResponse(301, {}, {'url': url})])
    assert h.api.enumerate(scope()).error == 'moved'
    assert len(h.calls) == 1


def test_inventory_redirect_loop_stops_after_one_hop():
    r = HTTPResponse(301, {}, {'url': 'https://api.github.com/repositories/42/actions/runners?per_page=100&page=1'})
    h = Harness([r, r])
    assert h.api.enumerate(scope()).error == 'moved'
    assert len(h.calls) == 2


def test_redirect_uses_existing_cycle_budget():
    h = Harness([HTTPResponse(301, {}, {'url': 'https://api.github.com/repositories/42/actions/runners?per_page=100&page=1'})], max_cycle_requests=1)
    h.api.begin_cycle()
    assert h.api.enumerate(scope()).error == 'cycle_budget'
    assert len(h.calls) == 1


def test_canonical_metadata_identity_and_name():
    calls = []
    replies = [HTTPResponse(301, {}, {'url': 'https://api.github.com/repositories/42'}),
               HTTPResponse(200, {}, {'id': 42, 'full_name': 'example/new'})]
    def get(path):
        calls.append(path)
        return replies.pop(0)
    assert canonical_repository('example/old', get) == 'example/new'
    assert calls == ['/repos/example/old', '/repositories/42']


@pytest.mark.parametrize('url', ['https://evil.invalid/repositories/42', 'https://api.github.com/repositories/42?x=1',
    'https://api.github.com/repos/example/new', 'https://api.github.com/repositories/42/../43'])
def test_canonical_rejects_untrusted_url(url):
    with pytest.raises(CanonicalError):
        canonical_repository('example/old', lambda p: HTTPResponse(301, {}, {'url': url}))


def test_canonical_rejects_identity_mismatch():
    replies = iter([HTTPResponse(301, {}, {'url': 'https://api.github.com/repositories/42'}),
                    HTTPResponse(200, {}, {'id': 43, 'full_name': 'example/new'})])
    with pytest.raises(CanonicalError, match='identity_mismatch'):
        canonical_repository('example/old', lambda p: next(replies))


def test_generator_preserves_ids_and_references_and_is_idempotent():
    config = normalize(deepcopy(BASE))
    config['scopes'] = [scope()]
    def transport(*a, **kw):
        assert kw['allow_redirects'] is False
        return HTTPResponse(200, {}, {'id': 42, 'full_name': 'example/new'})
    resolver = lambda ref: Credential(ref, 'fictional-token')
    updated, report = reconcile(config, {}, transport, resolver, pause=lambda _: None)
    assert len(report['changes']) == 1 and not report['errors']
    assert updated['scopes'][0]['id'] == config['scopes'][0]['id']
    assert updated['scopes'][0]['permissions'] == config['scopes'][0]['permissions']
    again, report = reconcile(updated, {}, transport, resolver, pause=lambda _: None)
    assert again == updated and not report['changes']
    blocked, report = reconcile(config, {'job_repositories': {scope()['id']: ['example/widget']}}, transport, resolver, pause=lambda _: None)
    assert blocked['scopes'][0]['name'] == 'example/widget'
    assert report['errors'][0]['error'] == 'referenced_scope_requires_coordinated_rename'


def test_generator_rate_limit_aborts_without_applying():
    config = normalize(deepcopy(BASE)); config['scopes'] = [scope()]
    with pytest.raises(RuntimeError, match='rate_limited'):
        reconcile(config, {}, lambda *a, **kw: HTTPResponse(429, {'Retry-After': '60'}, {}),
                  lambda ref: Credential(ref, 'fictional-token'), pause=lambda _: None)


def test_service_manifest_matches_installer():
    import yaml
    from pathlib import Path
    from plugins.runners.scripts.install_service import UNIT, LABEL
    manifest = yaml.safe_load((Path(__file__).parents[1] / 'plugin.yaml').read_text())
    assert manifest['service'] == UNIT
    assert manifest['launchd_label'] == LABEL
    assert manifest['service_restart_safe'] is True


def test_generator_cli_dry_run_then_cas_apply(tmp_path, monkeypatch, capsys):
    from plugins.runners.config import ConfigFile
    from plugins.runners.scripts import reconcile_scopes as cli
    source = ConfigFile(tmp_path / 'runners.yaml')
    config = normalize(deepcopy(BASE)); config['scopes'] = [scope()]
    original = source.update(config, expected_revision=0, expected_owner_epoch=config['owner_epoch'])
    monkeypatch.setattr(cli, 'load_runtime', lambda _: {'credentials': {}})
    monkeypatch.setattr(cli, 'BrokerVendGitHub', lambda *a, **k: SimpleNamespace(transport=None, close=lambda: None))
    def fake(config, runtime, transport, resolver, **kwargs):
        from plugins.runners.canonical import repoint_scopes
        return repoint_scopes(config, {scope()['id']: 'example/new'}), {'changes': [scope()['id']], 'errors': []}
    monkeypatch.setattr(cli, 'reconcile', fake)
    args = ['--config', str(source.path), '--runtime', 'unused', '--state-dir', str(tmp_path)]
    assert cli.main(args) == 0
    assert source.load() == original
    assert cli.main(args + ['--apply']) == 0
    updated = source.load()
    assert updated['scopes'][0]['name'] == 'example/new'
    assert updated['scopes'][0]['id'] == original['scopes'][0]['id']
    assert updated['config_revision'] == original['config_revision'] + 1
