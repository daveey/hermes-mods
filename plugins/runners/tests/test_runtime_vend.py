"""BrokerVendGitHub: every GET runs inside an open broker vend (offline fakes only).

A fake `token-broker exec` injects a fictional token and a fake sitecustomize
(standing in for the GitHub quota supervisor's PYTHONPATH instrumentation) that
replaces HTTPSConnection, so no network, broker or real credential is touched.
"""
import json
import os
import stat
import sys
import time

import pytest

from plugins.runners.providers import runtime
from plugins.runners.providers.base import Credential
from plugins.runners.providers.runtime import BrokerCredential, BrokerVendGitHub, RuntimeBoundaryError

TOKEN = 'fictional-vend-token'

SITE = '''
import http.client, json, os, time
class Reply:
    def __init__(self, path, status):
        self.status = status
        self.body = json.dumps({'path': path}).encode()
    def getheaders(self):
        return [('X-RateLimit-Remaining', '4321'), ('X-RateLimit-Reset', '1')]
    def read1(self, size):
        body, self.body = self.body, b''
        return body
class Connection:
    sock = None
    def __init__(self, host, **kwargs):
        self.host = host
    def request(self, method, path, headers=None, **kwargs):
        with open(os.environ['FAKE_MODE']) as f:
            mode = f.read().strip()
        with open(os.environ['FAKE_LOG'], 'a') as f:
            f.write(json.dumps(dict(pid=os.getpid(), method=method, path=path,
                                    auth=headers.get('authorization'))) + '\\n')
        if mode == 'fail':
            raise OSError('fictional refusal')
        if mode == 'slow':
            time.sleep(30)
        self.path = path
        self.status = 403 if mode == 'forbidden' else 200
    def getresponse(self):
        return Reply(self.path, self.status)
    def close(self):
        pass
http.client.HTTPSConnection = Connection
http.client.HTTPConnection._hermes_quota = True
'''

BROKER = '''#!{python}
import os, sys
argv = sys.argv[1:]
with open(os.environ['FAKE_LOG'] + '.exec', 'a') as f:
    f.write(' '.join(argv[:5]) + '\\n')
if os.path.exists(os.environ['FAKE_DENY']):
    sys.exit(1)
assert argv[:2] == ['exec', '--scope'] and argv[3] == '--reason'
command = argv[argv.index('--') + 1:]
env = dict(os.environ)
env[command[-2]] = {token!r}
if os.environ.get('FAKE_SITE'):
    env['PYTHONPATH'] = os.environ['FAKE_SITE']
os.execvpe(command[0], command, env)
'''


def specs():
    return {'first': BrokerCredential('user/fictional-github', 'GH_TOKEN', 'shared-user'),
            'alias': BrokerCredential('user/fictional-github', 'GH_TOKEN', 'shared-user')}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    site = tmp_path / 'site'
    site.mkdir()
    (site / 'sitecustomize.py').write_text(SITE)
    broker = tmp_path / 'token-broker'
    broker.write_text(BROKER.format(python=sys.executable, token=TOKEN))
    broker.chmod(broker.stat().st_mode | stat.S_IXUSR)
    log, mode, deny = tmp_path / 'requests.log', tmp_path / 'mode', tmp_path / 'deny'
    mode.write_text('ok')
    monkeypatch.setenv('FAKE_LOG', str(log))
    monkeypatch.setenv('FAKE_MODE', str(mode))
    monkeypatch.setenv('FAKE_DENY', str(deny))
    monkeypatch.setenv('FAKE_SITE', str(site))
    monkeypatch.setenv('GH_TOKEN', 'fictional-ambient-token')  # Must never reach a vend.

    class Fake:
        clients = []
        def client(self, **options):
            options.setdefault('broker_command', (str(broker),))
            made = BrokerVendGitHub(specs(), **options)
            self.clients.append(made)
            return made
        def requests(self):
            return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        def execs(self):
            return log.with_name(log.name + '.exec').read_text().splitlines() if log.with_name(log.name + '.exec').exists() else []
        def mode(self, value):
            mode.write_text(value)
        def deny(self, value=True):
            deny.write_text('') if value else deny.unlink()
    instance = Fake()
    yield instance
    for made in instance.clients:
        made.close()


def get(client, credential, path='/orgs/example/actions/runners?per_page=100&page=1', timeout=10):
    return client.transport('GET', 'https://api.github.com' + path,
                            headers={'Authorization': 'Bearer ' + credential.token,
                                     'Accept': 'application/vnd.github+json'},
                            timeout=timeout, allow_redirects=False)


def test_token_stays_in_the_vend_and_every_get_runs_there(fake):
    client = fake.client()
    credential = client('first')
    assert isinstance(credential, Credential) and credential.key == 'shared-user'
    assert TOKEN not in credential.token and 'fictional-ambient-token' not in credential.token
    assert client('alias') == credential  # One vend per broker identity.
    for page in (1, 2, 3):
        response = get(client, credential, f'/orgs/example/actions/runners?per_page=100&page={page}')
        assert response.status == 200
        assert response.body == {'path': f'/orgs/example/actions/runners?per_page=100&page={page}'}
        assert response.headers['x-ratelimit-remaining'] == '4321'
    rows = fake.requests()
    assert [r['auth'] for r in rows] == ['Bearer ' + TOKEN] * 3
    assert len({r['pid'] for r in rows}) == 1 and rows[0]['pid'] != os.getpid()
    assert fake.execs() == ['exec --scope user/fictional-github --reason Read runner inventory']
    session = next(iter(client._sessions.values()))
    assert session.metered is True and session.used == 3


def test_vend_argv_and_environment_carry_no_token_and_keep_instrumentation(fake, monkeypatch):
    seen = []
    real = runtime.subprocess.Popen
    def spawn(argv, **kwargs):
        seen.append((argv, kwargs))
        return real(argv, **kwargs)
    client = fake.client(popen=spawn)
    get(client, client('first'))
    argv, kwargs = seen[0]
    assert TOKEN not in repr(argv) and 'fictional-ambient-token' not in repr(argv)
    assert 'GH_TOKEN' not in kwargs['env']  # Ambient credentials never substitute for the vend.
    child = argv[argv.index('--') + 1:]
    # Not isolated (-I/-E): the supervisor's PYTHONPATH instrumentation must load.
    assert child[:5] == [sys.executable, '-s', '-P', '-B', '-c']
    assert kwargs['stdout'] == kwargs['stderr'] == kwargs['stdin'] == runtime.subprocess.DEVNULL
    assert kwargs['close_fds'] and len(kwargs['pass_fds']) == 1 and kwargs['shell'] is False


def test_vends_rotate_by_request_count_ttl_and_idle(fake):
    now = [0.0]
    client = fake.client(max_requests=2, ttl=100, idle=10, clock=lambda: now[0])
    credential = client('first')
    for _ in range(3):
        get(client, credential)
    assert len(fake.execs()) == 2  # max_requests stays under the supervisor's per-vend budget.
    first_pid = fake.requests()[0]['pid']
    now[0] += 9
    get(client, credential)
    assert len(fake.execs()) == 2
    now[0] += 10
    get(client, credential)  # Idle: a fresh vend.
    assert len(fake.execs()) == 3
    for _ in range(5):
        now[0] += 9
        client('first')
        if len(fake.execs()) == 4:
            break
        get(client, credential)
    assert len(fake.execs()) == 4  # ttl ends even a busy vend.
    assert first_pid not in {r['pid'] for r in fake.requests()[-1:]}


def test_closed_vends_are_reaped(fake):
    client = fake.client()
    get(client, client('first'))
    process = next(iter(client._sessions.values())).process
    client.close()
    assert process.poll() is not None and not client._sessions
    with pytest.raises(ChildProcessError):
        os.waitpid(process.pid, os.WNOHANG)


def test_broker_denial_fails_resolution_with_per_key_cool_down(fake):
    now = [100.0]
    fake.deny()
    client = fake.client(clock=lambda: now[0], failure_ttl=90)
    for _ in range(5):
        for ref in ('first', 'alias'):
            with pytest.raises(RuntimeBoundaryError, match='^credential_unavailable$'):
                client(ref)
    assert len(fake.execs()) == 1
    now[0] += 90
    fake.deny(False)
    credential = client('first')
    assert get(client, credential).status == 200 and len(fake.execs()) == 2


def test_refusal_in_the_vend_closes_it_and_the_next_get_starts_a_new_one(fake):
    # The supervisor refusing a request (reserve, budget, brake) looks like this
    # to the vend's transport: the request fails before any response.
    client = fake.client()
    credential = client('first')
    get(client, credential)
    fake.mode('fail')
    with pytest.raises(RuntimeBoundaryError, match='^http_transport_failed$') as error:
        get(client, credential)
    assert error.value.__suppress_context__ and not client._sessions
    fake.mode('ok')
    assert get(client, credential).status == 200
    assert len(fake.execs()) == 2


def test_forbidden_reply_is_returned_and_retires_its_vend(fake):
    # github_quota latches a vend blocked after 401/403/429; the next GET must
    # not be refused for it.
    client = fake.client()
    credential = client('first')
    fake.mode('forbidden')
    assert get(client, credential).status == 403
    assert not client._sessions
    fake.mode('ok')
    assert get(client, credential).status == 200
    assert len(fake.execs()) == 2


def test_stalled_request_is_killed_at_its_deadline(fake):
    client = fake.client(max_timeout=1)
    credential = client('first')
    process = next(iter(client._sessions.values())).process
    fake.mode('slow')
    start = time.monotonic()
    with pytest.raises(RuntimeBoundaryError, match='^http_transport_failed$'):
        get(client, credential, timeout=1)
    assert time.monotonic() - start < 15
    assert process.poll() is not None and not client._sessions


@pytest.mark.parametrize('authorization', ['Bearer ' + TOKEN, 'Bearer vend-unknown', 'token x', None])
def test_unknown_credentials_are_refused_before_any_vend(fake, authorization):
    client = fake.client()
    headers = {} if authorization is None else {'Authorization': authorization}
    with pytest.raises(RuntimeBoundaryError, match='^http_transport_failed$'):
        client.transport('GET', 'https://api.github.com/user', headers=headers, timeout=5, allow_redirects=False)
    assert not fake.execs()


@pytest.mark.parametrize('url', ['http://api.github.com/x', 'https://example.invalid/x', 'https://api.github.com/x#frag'])
def test_unsafe_requests_never_reach_the_vend(fake, url):
    client = fake.client()
    credential = client('first')
    with pytest.raises(RuntimeBoundaryError):
        client.transport('GET', url, headers={'Authorization': 'Bearer ' + credential.token},
                         timeout=5, allow_redirects=False)
    assert not fake.requests()


def test_invalidate_closes_the_vend_and_clears_the_cool_down(fake):
    now = [0.0]
    client = fake.client(clock=lambda: now[0])
    credential = client('first')
    client.invalidate(key=credential.key)
    assert not client._sessions
    fake.deny()
    with pytest.raises(RuntimeBoundaryError):
        client('first')
    fake.deny(False)
    client.invalidate('alias')
    assert client('first') == credential and len(fake.execs()) == 3


def test_github_actions_adapter_contract(fake):
    from plugins.runners.providers.github_actions import GitHubActions
    client = BrokerVendGitHub({'cred_' + '1' * 16: specs()['first']},
                              broker_command=fake.client().__dict__['_command'])
    fake.clients.append(client)
    api = GitHubActions(transport=client.transport, credential_resolver=client)
    result = api.enumerate(dict(id='scope_' + '1' * 16, provider='github_actions',
                                endpoint='https://api.github.com', kind='organization',
                                name='example', credential_ref='cred_' + '1' * 16, permissions=[]))
    # The fake answers every path with {'path': ...}: not an inventory page.
    assert result.error == 'invalid_payload', result
    assert fake.requests()[0]['auth'] == 'Bearer ' + TOKEN
    assert fake.requests()[0]['path'] == '/orgs/example/actions/runners?per_page=100&page=1'


@pytest.mark.parametrize('options', [dict(max_requests=250), dict(max_requests=0), dict(ttl=0),
                                     dict(idle=float('nan')), dict(failure_ttl=3601), dict(max_timeout=61),
                                     dict(broker_command=()), dict(start_timeout=0)])
def test_options_are_bounded(options):
    with pytest.raises(ValueError):
        BrokerVendGitHub(specs(), **options)
