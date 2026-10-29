"""Broker GitHub App installation tokens: scoped mint, cache, key-file hygiene, no classic fallback.

Fictional identifiers; a throwaway RSA key is generated locally. Nothing contacts GitHub.
"""
import base64
import json
import os
import shutil
import subprocess

import pytest

from plugins.runners import credentials, operations as ops

REF = 'cred_' + 'e' * 32
APP_ID = 424242
REPO = 'example/app'
CLASSIC = 'ghp_' + 'C' * 36
PEM = '-----BEGIN RSA PRIVATE KEY-----\nFAKEKEYBODY\n-----END RSA PRIVATE KEY-----\n'


def _key_dir(tmp_path, mode=0o600, text=PEM):
    os.chmod(tmp_path, 0o700)
    p = tmp_path / REF
    p.write_text(text)
    os.chmod(p, mode)
    return tmp_path


class Github:
    """Fake GitHub: records every call; mint answers are scripted."""

    def __init__(self, now, expires_in=3600, mint_status=201, perms=None, repos=('app',), installation_status=200):
        self.now, self.expires_in, self.mint_status = now, expires_in, mint_status
        self.perms = perms if perms is not None else {'administration': 'read', 'metadata': 'read'}
        self.repos, self.installation_status = list(repos), installation_status
        self.calls, self.n = [], 0

    def transport(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        if url.endswith('/installation'):
            return self.installation_status, json.dumps({'id': 99}).encode()
        if url.endswith('/access_tokens'):
            if self.mint_status != 201:
                return self.mint_status, json.dumps({'message': 'The permissions requested are not granted'}).encode()
            self.n += 1
            exp = self.now[0] + self.expires_in
            iso = '%s' % __import__('time').strftime('%Y-%m-%dT%H:%M:%SZ', __import__('time').gmtime(exp))
            return 201, json.dumps({'token': 'ghs_token%d' % self.n, 'expires_at': iso, 'permissions': self.perms,
                                    'repository_selection': 'selected',
                                    'repositories': [{'name': r} for r in self.repos]}).encode()
        return 404, b'{}'


def _minter(tmp_path, gh, now, **kw):
    return credentials.AppTokenMinter(tmp_path, expected_uid=os.getuid(), transport=gh.transport,
                                      clock=lambda: now[0], signer=lambda key, data: b'sig', **kw)


def test_mint_requests_only_administration_read_on_the_one_repo(tmp_path):
    now = [1_800_000_000.0]
    gh = Github(now)
    tok = _minter(_key_dir(tmp_path), gh, now).token(app_id=APP_ID, key_ref=REF, repo=REPO)
    assert tok == 'ghs_token1'
    inst, mint = gh.calls
    assert inst[0] == 'GET' and inst[1] == 'https://api.github.com/repos/example/app/installation'
    assert mint[0] == 'POST' and mint[1] == 'https://api.github.com/app/installations/99/access_tokens'
    assert json.loads(mint[3]) == {'repositories': ['app'], 'permissions': {'administration': 'read'}}
    jwt = mint[2]['Authorization'].split()[1]
    head, claims, _sig = jwt.split('.')
    pad = lambda s: s + '=' * (-len(s) % 4)
    assert json.loads(base64.urlsafe_b64decode(pad(head))) == {'alg': 'RS256', 'typ': 'JWT'}
    c = json.loads(base64.urlsafe_b64decode(pad(claims)))
    assert c['iss'] == str(APP_ID) and c['iat'] <= now[0] and c['exp'] - now[0] <= 600


def test_token_is_cached_until_five_minutes_before_expiry_then_refreshed(tmp_path):
    now = [1_800_000_000.0]
    gh = Github(now, expires_in=3600)
    m = _minter(_key_dir(tmp_path), gh, now)
    assert m.token(app_id=APP_ID, key_ref=REF, repo=REPO) == 'ghs_token1'
    now[0] += 3600 - 301
    assert m.token(app_id=APP_ID, key_ref=REF, repo=REPO) == 'ghs_token1'
    assert gh.n == 1
    now[0] += 2  # inside the 5 minute margin
    assert m.token(app_id=APP_ID, key_ref=REF, repo=REPO) == 'ghs_token2'
    assert gh.n == 2


def test_installation_lookup_is_cached(tmp_path):
    now = [1_800_000_000.0]
    gh = Github(now, expires_in=400)
    m = _minter(_key_dir(tmp_path), gh, now)
    m.token(app_id=APP_ID, key_ref=REF, repo=REPO)
    now[0] += 200
    m.token(app_id=APP_ID, key_ref=REF, repo=REPO)
    assert [c[1].endswith('/installation') for c in gh.calls].count(True) == 1


@pytest.mark.parametrize('mode', [0o640, 0o644, 0o604, 0o660, 0o666])
def test_key_file_group_or_world_readable_is_refused(tmp_path, mode):
    now = [1_800_000_000.0]
    gh = Github(now)
    with pytest.raises(credentials.CredentialError) as exc:
        _minter(_key_dir(tmp_path, mode=mode), gh, now).token(app_id=APP_ID, key_ref=REF, repo=REPO)
    assert 'credential_not_private' in str(exc.value)
    assert gh.calls == []


def test_key_file_wrong_owner_is_refused(tmp_path):
    now = [1_800_000_000.0]
    gh = Github(now)
    m = credentials.AppTokenMinter(_key_dir(tmp_path), expected_uid=os.getuid() + 1, transport=gh.transport,
                                   clock=lambda: now[0], signer=lambda k, d: b'sig')
    with pytest.raises(credentials.CredentialError):
        m.token(app_id=APP_ID, key_ref=REF, repo=REPO)
    assert gh.calls == []


def test_non_pem_key_file_is_refused(tmp_path):
    now = [1_800_000_000.0]
    gh = Github(now)
    with pytest.raises(credentials.CredentialError) as exc:
        _minter(_key_dir(tmp_path, text=CLASSIC + '\n'), gh, now).token(app_id=APP_ID, key_ref=REF, repo=REPO)
    assert 'private_key' in str(exc.value)
    assert gh.calls == []


@pytest.mark.parametrize('kw,code', [
    (dict(mint_status=422), 'app_token_mint_failed_status_422'),
    (dict(mint_status=401), 'app_token_mint_failed_status_401'),
    (dict(installation_status=404), 'app_installation_lookup_failed_status_404'),
])
def test_mint_failure_is_explicit_and_never_falls_back(tmp_path, kw, code):
    now = [1_800_000_000.0]
    gh = Github(now, **kw)
    with pytest.raises(credentials.CredentialError) as exc:
        _minter(_key_dir(tmp_path), gh, now).token(app_id=APP_ID, key_ref=REF, repo=REPO)
    assert code in str(exc.value)


@pytest.mark.parametrize('perms,repos', [
    ({'administration': 'read', 'metadata': 'read', 'contents': 'write'}, ('app',)),
    ({'administration': 'write', 'metadata': 'read'}, ('app',)),
    ({'administration': 'read', 'metadata': 'read'}, ('app', 'other')),
])
def test_overbroad_token_is_refused(tmp_path, perms, repos):
    now = [1_800_000_000.0]
    gh = Github(now, perms=perms, repos=repos)
    with pytest.raises(credentials.CredentialError) as exc:
        _minter(_key_dir(tmp_path), gh, now).token(app_id=APP_ID, key_ref=REF, repo=REPO)
    assert 'app_token_overbroad' in str(exc.value)


@pytest.mark.skipif(not shutil.which('openssl'), reason='openssl required')
def test_default_signer_produces_valid_rs256_signature(tmp_path):
    key = tmp_path / 'k.pem'
    subprocess.run(['openssl', 'genrsa', '-out', str(key), '2048'], check=True, capture_output=True)
    pub = tmp_path / 'p.pem'
    subprocess.run(['openssl', 'rsa', '-in', str(key), '-pubout', '-out', str(pub)], check=True, capture_output=True)
    sig = credentials.sign_rs256(key.read_text(), b'hello.world')
    (tmp_path / 's').write_bytes(sig)
    (tmp_path / 'd').write_bytes(b'hello.world')
    out = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(pub), '-signature', str(tmp_path / 's'),
                          str(tmp_path / 'd')], capture_output=True, text=True)
    assert 'Verified OK' in out.stdout


# --- GitHubPort integration -------------------------------------------------------------

def _port(tmp_path, gh, now, runner_transport):
    return ops.GitHubPort(_key_dir(tmp_path), expected_uid=os.getuid(), transport=runner_transport,
                          mint_transport=gh.transport, clock=lambda: now[0], signer=lambda k, d: b'sig')


def _gh_block():
    return dict(api='https://api.github.com', repo=REPO, runner_id=7, credential_ref=REF, app_id=APP_ID)


def test_github_port_reads_runner_with_the_app_token_and_reports_source(tmp_path):
    now = [1_800_000_000.0]
    gh, seen = Github(now), {}

    def runner_transport(url, headers, timeout):
        seen['auth'], seen['url'] = headers['Authorization'], url
        return 200, json.dumps(dict(id=7, status='online', busy=False)).encode()

    got = _port(tmp_path, gh, now, runner_transport).runner(_gh_block())
    assert seen['auth'] == 'Bearer ghs_token1'
    assert seen['url'] == 'https://api.github.com/repos/example/app/actions/runners/7'
    assert got['status'] == 'online' and got['token_source'] == 'app_installation'


def test_github_port_mint_failure_does_not_reach_runner_api_and_names_the_error(tmp_path):
    now = [1_800_000_000.0]
    gh = Github(now, mint_status=422)
    called = []
    port = _port(tmp_path, gh, now, lambda *a: called.append(a) or (200, b'{}'))
    with pytest.raises(ops.ActivityUnknown) as exc:
        port.runner(_gh_block())
    assert 'app_token_mint_failed_status_422' in str(exc.value)
    assert called == []


def test_github_port_refuses_block_without_app_id_no_classic_path(tmp_path):
    now = [1_800_000_000.0]
    (tmp_path / REF).write_text(CLASSIC + '\n')
    os.chmod(tmp_path, 0o700)
    os.chmod(tmp_path / REF, 0o600)
    called = []
    port = ops.GitHubPort(tmp_path, expected_uid=os.getuid(), transport=lambda *a: called.append(a) or (200, b'{}'),
                          mint_transport=Github(now).transport, clock=lambda: now[0])
    block = dict(_gh_block())
    del block['app_id']
    with pytest.raises(ops.ActivityUnknown) as exc:
        port.runner(block)
    assert 'app_id_missing' in str(exc.value) and called == []


def test_policy_requires_app_id_for_github_block():
    block = _gh_block()
    del block['app_id']
    with pytest.raises(ValueError):
        ops.load_policy(dict(version=1, host_id='00000000-0000-4000-8000-0000000000aa', management_operations=False,
                             clients=[dict(uid=1000)], targets=[dict(
                                 id='service_' + 'a' * 32, host_id='00000000-0000-4000-8000-0000000000aa',
                                 unit='gh-runner-canary@1.service', canary=True, description_contains='x',
                                 fragment_path='/etc/systemd/system/gh-runner-canary@.service',
                                 operations=['diagnose'], github=block)]))


# --- Engine: diagnose reports fresh GitHub activity + audit token source ------------------

class _AppGitHub:
    def __init__(self, error=None):
        self.error = error

    def runner(self, github):
        if self.error:
            raise self.error
        return dict(status='online', busy=False, observed_at=1000.0, token_source='app_installation')


def _engine(tmp_path, github, audit=None):
    from plugins.runners.tests.test_operations import FakeProcesses, FakeSystemd, policy
    return ops.Engine(policy(), systemd=FakeSystemd(), github=github, processes=FakeProcesses(),
                      locks=ops.FlockLocks(tmp_path), clock=lambda: 1000.0, audit=audit)


def _diagnose():
    from plugins.runners.tests.test_operations import request
    return request('diagnose')


def test_diagnose_returns_fresh_github_activity_and_audit_records_token_source(tmp_path):
    records = []
    res = _engine(tmp_path, _AppGitHub(), records.append).handle(_diagnose(), peer_uid=1000)
    assert res['ok'] is True
    assert res['data']['github'] == dict(status='online', busy=False, observed_at=1000.0, fresh=True,
                                         token_source='app_installation')
    assert records[-1]['token_source'] == 'app_installation'


def test_diagnose_reports_explicit_github_error_when_mint_fails(tmp_path):
    records = []
    res = _engine(tmp_path, _AppGitHub(ops.ActivityUnknown('app_token_mint_failed_status_422')),
                  records.append).handle(_diagnose(), peer_uid=1000)
    assert res['ok'] is True
    assert res['data']['github'] == dict(error='app_token_mint_failed_status_422')
    assert records[-1]['token_source'] is None


def test_start_refusal_names_mint_error(tmp_path):
    from plugins.runners.tests.test_operations import request
    res = _engine(tmp_path, _AppGitHub(ops.ActivityUnknown('app_token_mint_failed_status_422'))).handle(
        request(), peer_uid=1000)
    assert res['ok'] is False and res['refusal'] == 'activity_unknown'
    assert 'app_token_mint_failed_status_422' in res['detail']
