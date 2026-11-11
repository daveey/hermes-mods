"""Offline acceptance tests for destructive maintenance fences and opt-in discovery."""
from copy import deepcopy
import hashlib
import json
import stat

import pytest

from plugins.runners.config import ConfigFile, ConfigError, normalize
from plugins.runners.providers.base import Credential, HTTPResponse
from plugins.runners.providers.github_actions import _Failure
from plugins.runners.scripts import reconcile_scopes as cli
from plugins.runners.tests.test_config import BASE, FIXTURES
from plugins.runners.tests.test_github import Harness, scope
from plugins.runners.store import Store


def config():
    result = normalize(BASE)
    result['scopes'] = [scope()]
    return result


def snapshot(c, at=150, ident='collection_bbbbbbbbbbbbbbbb', state='complete'):
    return dict(at=at, collection=dict(id=ident, state=state), owner=cli._owner(c))


def run(c, evidence, now=100, snap=None, replies=None, **kwargs):
    h = Harness(replies if replies is not None else [HTTPResponse(404, {}, {}), HTTPResponse(200, {}, [])])
    updated, report = cli.reconcile(c, {}, h.api.transport, h.api.credential_resolver,
        adapter=h.api, pause=lambda _: None, evidence=evidence, now=now,
        snapshot=snap, interval=100, **kwargs)
    return updated, report, h


def test_two_real_404s_interval_and_completed_intervening_collection():
    c = config(); evidence = {}
    updated, report, _ = run(c, evidence)
    assert updated == c and len(report['pending']) == 1 and not report['removals']
    updated, report, h = run(c, evidence, now=200, snap=snapshot(c))
    assert updated['scopes'] == [] and report['removals'] == [dict(id=scope()['id'], name=scope()['name'])]
    assert '/orgs/example/repos?' in h.calls[1][1]
    assert h.resolutions == [scope()['credential_ref']]


@pytest.mark.parametrize('now,snap', [(199, 'good'), (200, None), (200, 'partial'),
    (200, 'old'), (200, 'future'), (200, 'wrong_owner'), (200, 'same_cycle')])
def test_confirmation_requires_interval_and_new_complete_owned_snapshot(now, snap):
    c = config(); evidence = {}
    initial = snapshot(c, at=50, ident='collection_aaaaaaaaaaaaaaaa')
    run(c, evidence, snap=initial)
    s = snapshot(c) if snap else None
    if snap == 'partial': s['collection']['state'] = 'partial'
    if snap == 'old': s['at'] = 99
    if snap == 'future': s['at'] = 201
    if snap == 'wrong_owner': s['owner']['owner_epoch'] += 1
    if snap == 'same_cycle': s['collection']['id'] = initial['collection']['id']
    updated, report, _ = run(c, evidence, now=now, snap=s)
    assert updated == c and report['pending'] and not report['removals']


@pytest.mark.parametrize('change', ['name', 'credential_ref', 'owner', 'revision', 'expired'])
def test_changed_identity_and_stale_evidence_reset_confirmation(change):
    c = config(); evidence = {}; run(c, evidence)
    now = 200
    if change == 'name': c['scopes'][0]['name'] = 'example/other'
    if change == 'credential_ref': c['scopes'][0]['credential_ref'] = 'cred_' + '2' * 16
    if change == 'owner': c['owner_epoch'] += 1
    if change == 'revision': c['config_revision'] += 1
    if change == 'expired': now = 100000
    updated, report, _ = run(c, evidence, now=now, snap=snapshot(c))
    assert updated == c and report['pending'] and not report['removals']
    assert evidence[scope()['id']]['at'] == now


@pytest.mark.parametrize('status', [403, 410, 500, 304])
def test_non404_is_never_confirmation(status):
    c = config(); evidence = {}; run(c, evidence)
    updated, report, h = run(c, evidence, now=200, snap=snapshot(c), replies=[HTTPResponse(status, {}, {})])
    assert updated == c and not evidence and report['errors'] and not report['removals']
    assert len(h.calls) == 1


def test_cached_not_found_is_not_a_real404_and_preserves_cooldown():
    c = config(); evidence = {}; run(c, evidence)
    h = Harness([HTTPResponse(410, {}, {})])
    with pytest.raises(_Failure, match='not_found'):
        h.api._get(Credential(scope()['credential_ref'], 'fictional-test-token'), '/repos/example/widget', metadata=True)
    updated, report = cli.reconcile(c, {}, h.api.transport, h.api.credential_resolver,
        adapter=h.api, evidence=evidence, now=200, snapshot=snapshot(c), interval=100, pause=lambda _: None)
    assert updated == c and not evidence and not report['removals']
    assert report['errors'][0]['error'] == 'not_found' and len(h.calls) == 1


@pytest.mark.parametrize('rows', [{}, [{'id': True, 'full_name': 'example/repo'}],
    [{'id': 1, 'full_name': 'other/repo'}], [{'id': 1, 'full_name': 'example/a'}, {'id': 1, 'full_name': 'example/b'}],
    [{'id': 1, 'full_name': 'example/a'}, {'id': 2, 'full_name': 'example/A'}],
    [{'id': 1, 'full_name': 'example/widget'}]])
def test_bad_or_present_listing_prevents_deletion_and_clears_proof(rows):
    c = config(); evidence = {}; run(c, evidence)
    updated, report, _ = run(c, evidence, now=200, snap=snapshot(c),
        replies=[HTTPResponse(404, {}, {}), HTTPResponse(200, {}, rows)])
    assert updated == c and not report['removals'] and report['errors'] and not evidence


def test_failed_later_page_prevents_deletion():
    c = config(); evidence = {}; run(c, evidence)
    rows = [dict(id=i + 1, full_name=f'example/repo{i}') for i in range(100)]
    updated, report, h = run(c, evidence, now=200, snap=snapshot(c), replies=[
        HTTPResponse(404, {}, {}), HTTPResponse(200, {}, rows), HTTPResponse(403, {}, {})])
    assert updated == c and not evidence and report['errors'] and len(h.calls) == 3


@pytest.mark.parametrize('link', ['garbage', '<https://evil.invalid/x>; rel="next"',
    '<https://api.github.com/orgs/example/repos?per_page=100&page=1>; rel="next"',
    '<https://api.github.com/orgs/example/repos?per_page=100&page=3>; rel="last", <https://api.github.com/orgs/example/repos?per_page=100&page=1>; rel="first"'])
def test_invalid_or_partial_link_fails_closed(link):
    with pytest.raises(cli.CanonicalError):
        cli.organization_repositories('example', lambda _: HTTPResponse(200, {'Link': link}, []))


def test_listing_short_page_link_and_full_page_fallback_and_bound(monkeypatch):
    calls = []
    replies = [HTTPResponse(200, {'Link': '<https://api.github.com/orgs/example/repos?per_page=100&page=2>; rel="next"'},
                            [dict(id=1, full_name='example/one')]), HTTPResponse(200, {}, [])]
    def get(path): calls.append(path); return replies.pop(0)
    assert len(cli.organization_repositories('example', get)) == 1
    assert calls[-1].endswith('page=2')
    monkeypatch.setattr(cli, 'MAX_PAGES', 1)
    with pytest.raises(cli.CanonicalError, match='page_limit'):
        cli.organization_repositories('example', lambda _: HTTPResponse(200, {},
            [dict(id=i+1, full_name=f'example/repo{i}') for i in range(100)]))


@pytest.mark.parametrize('reference', ['workflow', 'job'])
def test_references_prohibit_deletion(reference):
    c = config(); runtime = {}
    if reference == 'workflow':
        w = deepcopy(FIXTURES['Workflow']); w['scope_id'] = scope()['id']; c['allowed_workflows'] = [w]
    else:
        runtime['job_repositories'] = {scope()['id']: ['example/widget']}
    h = Harness([HTTPResponse(404, {}, {})]); evidence = {}
    updated, report = cli.reconcile(c, runtime, h.api.transport, h.api.credential_resolver,
        adapter=h.api, pause=lambda _: None, evidence=evidence)
    assert updated == c and not report['removals'] and not evidence
    assert report['errors'][0]['error'] == 'referenced_scope_requires_coordinated_removal'
    assert len(h.calls) == 1


def test_removal_cap_refuses_all_not_just_excess():
    c = config(); c['scopes'] = [dict(scope(name=f'example/repo{i}'), id='scope_' + f'{i:016x}') for i in range(26)]
    evidence = {}
    replies = [HTTPResponse(404, {}, {}), HTTPResponse(200, {}, [])] + [HTTPResponse(404, {}, {})] * 25
    run(c, evidence, replies=replies)
    updated, report, _ = run(c, evidence, now=200, snap=snapshot(c), replies=replies)
    assert updated == c and not report['removals'] and len(report['pending']) == 26
    assert report['errors'] == [dict(error='removal_cap_exceeded', count=26)]


def test_discovery_explicit_only_stable_ids_unknown_permissions_and_idempotence():
    c = config(); c['scopes'] = []
    updated, report, h = run(c, {}, replies=[])
    assert updated == c and not h.calls and not report['additions']
    ref = scope()['credential_ref']
    rows = [dict(id=42, full_name='example/new')]
    updated, report, h = run(c, {}, replies=[HTTPResponse(200, {}, rows)], discover_org=[('example', ref)])
    added = updated['scopes'][0]
    assert added['id'] == 'scope_' + hashlib.sha256(b'repository:example/new').hexdigest()[:32]
    assert added['credential_ref'] == ref and h.resolutions == [ref]
    assert len(report['additions']) == 1 and all(p['state'] == 'not_checked' and p['observation_id'] is None for p in added['permissions'])
    again, report, _ = run(updated, {}, replies=[HTTPResponse(200, {}, rows[0]), HTTPResponse(200, {}, rows)], discover_org=[('example', ref)])
    assert again == updated and not report['additions'] and not report['changes']


def test_config_backup_exact_bytes_private_and_cas(tmp_path):
    source = ConfigFile(tmp_path / 'config.yaml')
    c = source.update(config(), expected_revision=0, expected_owner_epoch=config()['owner_epoch'])
    original = source.path.read_bytes()
    candidate = deepcopy(c); candidate['scopes'] = []
    updated = source.update(candidate, expected_revision=c['config_revision'], expected_owner_epoch=c['owner_epoch'], backup=True)
    backups = list(tmp_path.glob('*.bak'))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    with pytest.raises(ConfigError, match='revision_conflict'):
        source.update(candidate, expected_revision=c['config_revision'], expected_owner_epoch=c['owner_epoch'], backup=True)
    with pytest.raises(ConfigError, match='owner_conflict'):
        source.update(dict(updated, owner_epoch=updated['owner_epoch']+1), expected_revision=updated['config_revision'], expected_owner_epoch=updated['owner_epoch'], backup=True)
    assert source.load() == updated and len(list(tmp_path.glob('*.bak'))) == 1


def test_cli_persists_evidence_dry_run_then_applies_only_after_snapshot(tmp_path, monkeypatch, capsys):
    source = ConfigFile(tmp_path / 'config.yaml')
    c = source.update(config(), expected_revision=0, expected_owner_epoch=config()['owner_epoch'])
    original = source.path.read_bytes()
    clock = [100]
    monkeypatch.setattr(cli.time, 'time', lambda: clock[0])
    monkeypatch.setattr(cli.time, 'sleep', lambda _: None)
    class ClockedAdapter(cli.GitHubActions):
        def __init__(self, **kw):
            super().__init__(clock=lambda: clock[0], **kw)
    monkeypatch.setattr(cli, 'GitHubActions', ClockedAdapter)
    monkeypatch.setattr(cli, 'load_runtime', lambda _: {'credentials': {}})
    monkeypatch.setattr(cli, 'BrokerCredentialResolver', lambda *a, **k: lambda ref: Credential(ref, 'fictional-token'))
    def transport(*a, **k):
        return HTTPResponse(200, {}, []) if '/orgs/' in a[1] else HTTPResponse(404, {}, {})
    monkeypatch.setattr(cli, 'GitHubHTTPTransport', lambda: transport)
    args = ['--config', str(source.path), '--runtime', 'unused', '--state-dir', str(tmp_path), '--confirmation-interval', '100']
    assert cli.main(args) == 0
    assert source.path.read_bytes() == original and not list(tmp_path.glob('*.bak'))
    path = tmp_path / 'scope-confirmations.json'
    assert json.loads(path.read_text())[scope()['id']]['at'] == 100
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with Store(tmp_path / 'state.sqlite') as store:
        store.connection.execute("INSERT OR REPLACE INTO collector_cache VALUES ('snapshot',?)", (json.dumps(snapshot(c)),))
    clock[0] = 200
    assert cli.main(args + ['--apply']) == 0
    assert source.load()['scopes'] == [] and len(list(tmp_path.glob('*.bak'))) == 1
    reports = json.JSONDecoder()
    output = capsys.readouterr().out.lstrip()
    first, end = reports.raw_decode(output)
    second, _ = reports.raw_decode(output[end:].lstrip())
    assert first['applied'] is False and first['pending']
    assert second['applied'] is True and second['removals']


def test_cli_repeatable_opt_in_organizations(tmp_path, monkeypatch):
    source = ConfigFile(tmp_path / 'config.yaml')
    c = config(); c['scopes'] = []
    source.update(c, expected_revision=0, expected_owner_epoch=c['owner_epoch'])
    monkeypatch.setattr(cli, 'load_runtime', lambda _: {'credentials': {}})
    monkeypatch.setattr(cli, 'BrokerCredentialResolver', lambda *a, **k: None)
    seen = []
    def fake(config, *a, **kw):
        seen.extend(kw['discover_org'])
        return config, dict(changes=[], additions=[], removals=[], errors=[], pending=[])
    monkeypatch.setattr(cli, 'reconcile', fake)
    ref = scope()['credential_ref']
    cli.main(['--config', str(source.path), '--runtime', 'unused', '--state-dir', str(tmp_path),
              '--discover-org', 'example=' + ref, '--discover-org', 'another=' + ref])
    assert seen == [('example', ref), ('another', ref)]


def test_user_owner_requires_verified_account_before_absence():
    c = config(); evidence = {}
    replies = [HTTPResponse(404, {}, {}), HTTPResponse(404, {}, {}),
               HTTPResponse(200, {}, {'type': 'User'}), HTTPResponse(200, {}, [])]
    run(c, evidence, replies=replies)
    updated, report, h = run(c, evidence, now=200, snap=snapshot(c), replies=replies)
    assert report['removals'] and updated['scopes'] == []
    assert '/users/example/repos?' in h.calls[-1][1]
    evidence = {}
    updated, report, _ = run(c, evidence, replies=[HTTPResponse(404, {}, {}),
        HTTPResponse(404, {}, {}), HTTPResponse(200, {}, {'type': 'Organization'})])
    assert updated == c and not evidence and report['errors']


def test_reused_name_does_not_recycle_renamed_scope_id():
    c = config(); ref = scope()['credential_ref']
    old_id = 'scope_' + hashlib.sha256(b'repository:example/new').hexdigest()[:32]
    c['scopes'][0]['id'] = old_id
    rows = [dict(id=42, full_name='example/widget'), dict(id=43, full_name='example/new')]
    updated, report, _ = run(c, {}, replies=[HTTPResponse(200, {}, rows[0]), HTTPResponse(200, {}, rows)],
                             discover_org=[('example', ref)])
    assert updated['scopes'][0]['id'] == old_id
    assert updated['scopes'][1]['id'] == 'scope_' + hashlib.sha256(b'repository:example/new:43').hexdigest()[:32]


def test_invalid_scope_name_refused_before_network():
    c = config(); c['scopes'][0]['name'] = 'example/../../bad'
    # Planning ultimately validates the unchanged config; input cannot generate a GET.
    h = Harness([])
    with pytest.raises(ConfigError):
        cli.reconcile(c, {}, h.api.transport, h.api.credential_resolver, adapter=h.api, pause=lambda _: None)
    assert not h.calls


@pytest.mark.parametrize('owner_type,route', [('orgs', 'organizations/9919'), ('users', 'user/1024025')])
def test_numeric_listing_links_use_locally_built_paths(owner_type, route):
    calls = []
    replies = [HTTPResponse(200, {'Link': f'<https://api.github.com/{route}/repos?per_page=100&page=2>; rel="next"'},
                            [dict(id=1, full_name='example/one')]), HTTPResponse(200, {}, [])]
    def get(path):
        calls.append(path)
        return replies.pop(0)
    assert len(cli.organization_repositories('example', get, owner_type=owner_type)) == 1
    assert calls == [f'/{owner_type}/example/repos?per_page=100&page={p}' for p in (1, 2)]


@pytest.mark.parametrize('reference', ['workflow', 'job'])
def test_refused_rename_does_not_add_duplicate_via_discovery(reference):
    c = config(); runtime = {}
    if reference == 'workflow':
        w = deepcopy(FIXTURES['Workflow']); w['scope_id'] = scope()['id']; c['allowed_workflows'] = [w]
    else:
        runtime['job_repositories'] = {scope()['id']: ['example/widget']}
    renamed = dict(id=42, full_name='example/renamed')
    h = Harness([HTTPResponse(301, {}, {'url': 'https://api.github.com/repositories/42'}),
                 HTTPResponse(200, {}, renamed), HTTPResponse(200, {}, [renamed])])
    updated, report = cli.reconcile(c, runtime, h.api.transport, h.api.credential_resolver,
        adapter=h.api, pause=lambda _: None, discover_org=[('example', scope()['credential_ref'])])
    assert updated == c and not report['additions'] and not report['changes']
    assert report['errors'][0]['error'] == 'referenced_scope_requires_coordinated_rename'

