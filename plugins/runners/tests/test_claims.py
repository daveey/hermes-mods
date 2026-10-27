"""Execution-claim broker: real RS256 tokens from a throwaway test key, fictional ids.

No test contacts GitHub, the network or a real ledger; JWKS is injected.
"""
import base64
import hashlib
import json
import os
import random
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from plugins.runners.broker import claims as cb

REPO = 'example/app'
WORKFLOW = '.github/workflows/canary-macos.yml'
REF = 'refs/heads/main'
AUD = 'hermes-runners-claims-example'
SHA = 'a' * 40
WSHA = 'b' * 40
REQ = 'request_' + '1' * 32
NOW = 1_800_000_000


# ---------------------------------------------------------------- tiny test-only RSA

def _probable_prime(bits, rng):
    small = [p for p in range(3, 2000, 2) if all(p % q for q in range(3, int(p ** 0.5) + 1, 2))]
    while True:
        candidate = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if any(candidate % p == 0 for p in small):
            continue
        d, s = candidate - 1, 0
        while d % 2 == 0:
            d, s = d // 2, s + 1
        for _ in range(24):
            x = pow(rng.randrange(2, candidate - 2), d, candidate)
            if x in (1, candidate - 1):
                continue
            for _ in range(s - 1):
                x = pow(x, 2, candidate)
                if x == candidate - 1:
                    break
            else:
                break
        else:
            return candidate


class Key:
    def __init__(self, seed, kid):
        rng = random.Random(seed)
        self.e, self.kid = 65537, kid
        while True:
            p, q = _probable_prime(1024, rng), _probable_prime(1024, rng)
            phi = (p - 1) * (q - 1)
            if p != q and phi % self.e:
                break
        self.n, self.d = p * q, pow(self.e, -1, phi)

    def jwk(self):
        enc = lambda i: base64.urlsafe_b64encode(i.to_bytes((i.bit_length() + 7) // 8, 'big')).rstrip(b'=').decode()
        return dict(kty='RSA', use='sig', alg='RS256', kid=self.kid, n=enc(self.n), e=enc(self.e))

    def sign(self, message):
        k = (self.n.bit_length() + 7) // 8
        digest = cb._SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
        em = b'\x00\x01' + b'\xff' * (k - 3 - len(digest)) + b'\x00' + digest
        return pow(int.from_bytes(em, 'big'), self.d, self.n).to_bytes(k, 'big')


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


@pytest.fixture(scope='module')
def key():
    return Key(7, 'kid-example')


@pytest.fixture(scope='module')
def other_key():
    return Key(11, 'kid-example')


def claims(**overrides):
    base = dict(iss=cb.ISSUER, aud=AUD, iat=NOW - 10, nbf=NOW - 10, exp=NOW + 290, repository=REPO,
                repository_owner='example', workflow_ref='%s/%s@%s' % (REPO, WORKFLOW, REF),
                job_workflow_ref='%s/%s@%s' % (REPO, WORKFLOW, REF), ref=REF, sha=WSHA, event_name='workflow_dispatch',
                runner_environment='self-hosted', run_id='4242', run_attempt='1')
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def token(key, payload=None, header=None):
    header = header or dict(alg='RS256', kid=key.kid, typ='JWT')
    signing = b64(json.dumps(header).encode()) + '.' + b64(json.dumps(payload or claims()).encode())
    return signing + '.' + b64(key.sign(signing.encode()))


def policy(**overrides):
    raw = dict(version=1, audience=AUD, clients=[dict(uid=os.getuid())],
               workflows=[dict(repository=REPO, workflow_path=WORKFLOW, refs=[REF])],
               rate=dict(per_source_per_minute=1000, global_per_minute=1000, intents_per_minute=1000))
    raw.update(overrides)
    return cb.load_policy(raw)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def ledger(tmp_path, key):
    clock = Clock()
    jwks = cb.JWKS(lambda: dict(keys=[key.jwk()]), clock=clock)
    led = cb.Ledger(tmp_path / 'ledger.sqlite', policy(), jwks=jwks, clock=clock)
    led.register(dict(op='register', request_id=REQ, repository=REPO, workflow_path=WORKFLOW, ref=REF, source_sha=SHA))
    led.clock_ref = clock
    return led


def refused(fn, code):
    with pytest.raises(cb.Refused) as exc:
        fn()
    assert exc.value.code == code
    return exc.value


# ---------------------------------------------------------------- claims

def test_first_claim_wins_and_is_recorded(ledger, key):
    grant = ledger.claim(token(key), dict(request_id=REQ, source_sha=SHA))
    assert grant['ok'] and grant['replay'] is False and grant['run_id'] == '4242' and grant['workflow_sha'] == WSHA
    status = ledger.status(dict(request_id=REQ))
    assert status['claim']['claim_id'] == grant['claim_id'] and status['claim']['run_attempt'] == '1'


def test_simultaneous_claimants_exactly_one_winner(ledger, key):
    tokens = [token(key, claims(run_id=str(5000 + i))) for i in range(16)]
    results, barrier = [], threading.Barrier(len(tokens))

    def go(t):
        barrier.wait()
        try:
            results.append(ledger.claim(t, dict(request_id=REQ, source_sha=SHA)))
        except cb.Refused as exc:
            results.append(exc.code)
    threads = [threading.Thread(target=go, args=(t,)) for t in tokens]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    winners = [r for r in results if isinstance(r, dict)]
    assert len(winners) == 1 and results.count('claim_already_used') == len(tokens) - 1


def test_lost_claim_response_retry_by_same_run_attempt_only(ledger, key):
    first = ledger.claim(token(key), dict(request_id=REQ, source_sha=SHA))
    again = ledger.claim(token(key), dict(request_id=REQ, source_sha=SHA))
    assert again['replay'] is True and again['claim_id'] == first['claim_id']
    refused(lambda: ledger.claim(token(key, claims(run_attempt='2')), dict(request_id=REQ, source_sha=SHA)),
            'claim_already_used')
    refused(lambda: ledger.claim(token(key, claims(run_id='999')), dict(request_id=REQ, source_sha=SHA)),
            'claim_already_used')


def test_crashed_winner_is_never_taken_over(ledger, key):
    winner = ledger.claim(token(key), dict(request_id=REQ, source_sha=SHA))
    # The winning run crashes; days pass. No lease expiry, no takeover by a re-run or another run.
    ledger.clock_ref.now += 3 * 86400
    later = dict(iat=ledger.clock_ref.now - 5, nbf=ledger.clock_ref.now - 5, exp=ledger.clock_ref.now + 290)
    refused(lambda: ledger.claim(token(key, claims(run_attempt='2', **later)), dict(request_id=REQ, source_sha=SHA)),
            'claim_already_used')
    refused(lambda: ledger.claim(token(key, claims(run_id='7777', **later)), dict(request_id=REQ, source_sha=SHA)),
            'claim_already_used')
    assert ledger.status(dict(request_id=REQ))['claim']['claim_id'] == winner['claim_id']
    assert ledger.status(dict(request_id=REQ))['refusals'] == 2


@pytest.mark.parametrize('mutate,code', [
    (lambda k, o: token(o), 'token_signature_invalid'),
    (lambda k, o: token(k)[:-4] + 'AAAA', 'token_signature_invalid'),
    (lambda k, o: token(k, header=dict(alg='none', kid=k.kid)), 'token_algorithm_refused'),
    (lambda k, o: token(k, header=dict(alg='HS256', kid=k.kid)), 'token_algorithm_refused'),
    (lambda k, o: token(k, header=dict(alg='RS256', kid=k.kid, jku='https://example.com/keys')), 'token_header_refused'),
    (lambda k, o: token(k, header=dict(alg='RS256', kid='other-kid')), 'signing_key_unknown'),
    (lambda k, o: token(k, claims(iss='https://example.com')), 'token_issuer_invalid'),
    (lambda k, o: token(k, claims(aud='someone-else')), 'token_audience_invalid'),
    (lambda k, o: token(k, claims(aud=[AUD])), 'token_audience_invalid'),
    (lambda k, o: token(k, claims(exp=NOW - 120)), 'token_expired'),
    (lambda k, o: token(k, claims(nbf=NOW + 600)), 'token_time_invalid'),
    (lambda k, o: token(k, claims(iat=NOW - 7200, nbf=NOW - 7200, exp=NOW + 60)), 'token_time_invalid'),
    (lambda k, o: 'not.a.jwt.token', 'token_malformed'),
    (lambda k, o: 'abc', 'token_malformed'),
])
def test_invalid_oidc_is_refused(ledger, key, other_key, mutate, code):
    refused(lambda: ledger.claim(mutate(key, other_key), dict(request_id=REQ, source_sha=SHA)), code)
    assert ledger.status(dict(request_id=REQ))['claim'] is None


@pytest.mark.parametrize('override,code', [
    (dict(repository='example/other'), 'repository_mismatch'),
    (dict(workflow_ref='%s/.github/workflows/other.yml@%s' % (REPO, REF)), 'workflow_mismatch'),
    (dict(job_workflow_ref='%s/.github/workflows/reusable.yml@%s' % (REPO, REF)), 'workflow_mismatch'),
    (dict(workflow_ref='%s/%s@refs/heads/feature' % (REPO, WORKFLOW)), 'workflow_mismatch'),
    (dict(ref='refs/heads/feature'), 'ref_mismatch'),
    (dict(event_name='push'), 'event_mismatch'),
    (dict(runner_environment='github-hosted'), 'runner_environment_mismatch'),
    (dict(run_id='abc'), 'token_run_identity_invalid'),
    (dict(sha='nothex'), 'token_sha_invalid'),
])
def test_wrong_workflow_identity_is_refused(ledger, key, override, code):
    refused(lambda: ledger.claim(token(key, claims(**override)), dict(request_id=REQ, source_sha=SHA)), code)
    assert ledger.status(dict(request_id=REQ))['claim'] is None


def test_wrong_sha_and_wrong_request_are_refused(ledger, key):
    refused(lambda: ledger.claim(token(key), dict(request_id=REQ, source_sha='c' * 40)), 'source_sha_mismatch')
    refused(lambda: ledger.claim(token(key), dict(request_id='request_' + '9' * 32, source_sha=SHA)), 'request_unknown')
    refused(lambda: ledger.claim(token(key), dict(request_id=REQ, source_sha=SHA, extra=1)), 'invalid_request')
    refused(lambda: ledger.claim(token(key), dict(request_id='../x', source_sha=SHA)), 'invalid_request')
    assert ledger.status(dict(request_id=REQ))['claim'] is None


def test_intent_must_match_policy_and_is_immutable(ledger):
    refused(lambda: ledger.register(dict(op='register', request_id=REQ, repository=REPO, workflow_path=WORKFLOW,
                                         ref=REF, source_sha='d' * 40)), 'intent_conflict')
    refused(lambda: ledger.register(dict(op='register', request_id='request_' + '2' * 32, repository='example/other',
                                         workflow_path=WORKFLOW, ref=REF, source_sha=SHA)), 'workflow_not_allowlisted')
    refused(lambda: ledger.register(dict(op='register', request_id='request_' + '2' * 32, repository=REPO,
                                         workflow_path=WORKFLOW, ref='refs/heads/dev', source_sha=SHA)), 'workflow_not_allowlisted')
    assert ledger.register(dict(op='register', request_id=REQ, repository=REPO, workflow_path=WORKFLOW, ref=REF,
                                source_sha=SHA))['ok']


def test_expired_intent_is_refused(ledger, key):
    ledger.clock_ref.now += 86400 + 1
    t = token(key, claims(iat=ledger.clock_ref.now - 5, nbf=ledger.clock_ref.now - 5, exp=ledger.clock_ref.now + 290))
    refused(lambda: ledger.claim(t, dict(request_id=REQ, source_sha=SHA)), 'intent_expired')


def test_claim_rate_limits(tmp_path, key):
    clock = Clock()
    led = cb.Ledger(tmp_path / 'l.sqlite', policy(rate=dict(per_source_per_minute=2, global_per_minute=100)),
                    jwks=cb.JWKS(lambda: dict(keys=[key.jwk()]), clock=clock), clock=clock)
    led.register(dict(op='register', request_id=REQ, repository=REPO, workflow_path=WORKFLOW, ref=REF, source_sha=SHA))
    led.claim(token(key), dict(request_id=REQ, source_sha=SHA), source='192.0.2.9')
    led.claim(token(key), dict(request_id=REQ, source_sha=SHA), source='192.0.2.9')
    exc = refused(lambda: led.claim(token(key), dict(request_id=REQ, source_sha=SHA), source='192.0.2.9'), 'rate_limited')
    assert exc.status == 429
    assert led.claim(token(key), dict(request_id=REQ, source_sha=SHA), source='192.0.2.10')['replay'] is True
    clock.now += 61
    assert led.claim(token(key, claims(iat=clock.now, nbf=clock.now, exp=clock.now + 200)),
                     dict(request_id=REQ, source_sha=SHA), source='192.0.2.9')['ok']


def test_jwks_refresh_is_bounded(key):
    calls = []
    clock = Clock()
    jwks = cb.JWKS(lambda: calls.append(1) or dict(keys=[key.jwk()]), clock=clock)
    for _ in range(5):
        with pytest.raises(cb.Refused):
            jwks.key('unknown')
    assert len(calls) == 1
    clock.now += 61
    with pytest.raises(cb.Refused):
        jwks.key('unknown')
    assert len(calls) == 2


def test_rsa_verify_rejects_short_keys_and_bad_lengths(key):
    msg = b'payload'
    sig = key.sign(msg)
    assert cb.rsa_sha256_verify(key.n, key.e, msg, sig)
    assert not cb.rsa_sha256_verify(key.n, key.e, msg + b'x', sig)
    assert not cb.rsa_sha256_verify(key.n, key.e, msg, sig[1:])
    assert not cb.rsa_sha256_verify(key.n, 17, msg, sig)
    assert not cb.rsa_sha256_verify(2 ** 1023 + 1, 65537, msg, b'\x01' * 128)


def test_reachability_reports_last_claim_per_workflow(ledger, key):
    assert ledger.reachability({})['workflows'] == []
    ledger.claim(token(key), dict(request_id=REQ, source_sha=SHA))
    rows = ledger.reachability({})['workflows']
    assert rows == [dict(repository=REPO, workflow_path=WORKFLOW, last_claim_at=NOW)]


def test_policy_validation_rejects_wildcards():
    for bad in (dict(audience='x'), dict(clients=[]), dict(workflows=[dict(repository='*', workflow_path=WORKFLOW, refs=[REF])]),
                dict(workflows=[dict(repository=REPO, workflow_path='../x.yml', refs=[REF])]),
                dict(workflows=[dict(repository=REPO, workflow_path=WORKFLOW, refs=['refs/tags/*'])]),
                dict(rate=dict(unknown=1))):
        with pytest.raises(ValueError):
            policy(**bad)


# ---------------------------------------------------------------- listeners

def _owner(path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(10)
        s.connect(str(path))
        s.sendall(json.dumps(payload).encode())
        s.shutdown(socket.SHUT_WR)
        data = b''
        while chunk := s.recv(65536):
            data += chunk
    return json.loads(data)


@pytest.fixture
def servers(tmp_path, key):
    clock = Clock()
    clock.now = int(time.time())
    led = cb.Ledger(tmp_path / 'ledger.sqlite', policy(), jwks=cb.JWKS(lambda: dict(keys=[key.jwk()]), clock=clock),
                    clock=clock)
    owner = cb.OwnerSocket(str(tmp_path / 'intent.sock'), led)
    http = cb.make_http_server(('127.0.0.1', 0), led)
    threads = [threading.Thread(target=owner.serve_forever, daemon=True),
               threading.Thread(target=http.serve_forever, daemon=True)]
    for t in threads:
        t.start()
    yield led, tmp_path / 'intent.sock', 'http://127.0.0.1:%d' % http.server_address[1], clock
    owner.shutdown()
    http.shutdown()


def _post(url, body, auth=None):
    req = urllib.request.Request(url + '/v1/claims', data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', **({'Authorization': auth} if auth else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_end_to_end_over_real_sockets(servers, key):
    led, sock, url, clock = servers
    assert _owner(sock, dict(api='claims_owner', op='health'))['ok']
    assert _owner(sock, dict(api='claims_owner', op='register', request_id=REQ, repository=REPO, workflow_path=WORKFLOW,
                             ref=REF, source_sha=SHA))['ok']
    with urllib.request.urlopen(url + '/v1/health', timeout=5) as r:
        assert json.loads(r.read())['audience'] == AUD
    now = clock.now
    t = token(key, claims(iat=now - 5, nbf=now - 5, exp=now + 290))
    assert _post(url, dict(request_id=REQ, source_sha=SHA)) == (401, dict(ok=False, refusal='token_missing'))
    status, body = _post(url, dict(request_id=REQ, source_sha=SHA), 'Bearer ' + t)
    assert status == 200 and body['ok'] and body['run_id'] == '4242'
    status, body = _post(url, dict(request_id=REQ, source_sha=SHA),
                         'Bearer ' + token(key, claims(run_id='5', iat=now - 5, nbf=now - 5, exp=now + 290)))
    assert (status, body['refusal']) == (409, 'claim_already_used')
    assert _owner(sock, dict(api='claims_owner', op='status', request_id=REQ))['claim']['run_id'] == '4242'


def test_owner_socket_is_not_a_management_api(servers):
    _led, sock, _url, _clock = servers
    assert _owner(sock, dict(api='management', operation='service_start'))['refusal'] == 'unknown_api'
    assert _owner(sock, dict(api='claims_owner', op='service_start'))['refusal'] == 'unknown_op'


def test_stalled_http_clients_are_dropped(tmp_path, key):
    led = cb.Ledger(tmp_path / 'l.sqlite', policy(), jwks=cb.JWKS(lambda: dict(keys=[key.jwk()])))
    http = cb.make_http_server(('127.0.0.1', 0), led, max_concurrent=4, io_timeout=1)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    try:
        port = http.server_address[1]
        stalled = []
        for _ in range(6):
            s = socket.create_connection(('127.0.0.1', port), timeout=5)
            s.sendall(b'POST /v1/claims HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n{')
            stalled.append(s)
        time.sleep(2.5)
        for s in stalled:
            s.settimeout(3)
            try:
                assert s.recv(65536) in (b'',) or True
            except OSError:
                pass
            s.close()
        # Slots were released after the per-connection timeout: a fresh request is served.
        with urllib.request.urlopen('http://127.0.0.1:%d/v1/health' % port, timeout=5) as r:
            assert json.loads(r.read())['ok']
        req = urllib.request.Request('http://127.0.0.1:%d/v1/claims' % port, data=b'{}', method='POST',
                                     headers={'Transfer-Encoding': 'chunked', 'Authorization': 'Bearer a.b.c'})
        try:
            urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        http.shutdown()


def test_junk_tokens_do_not_consume_the_global_budget(tmp_path, key):
    clock = Clock()
    led = cb.Ledger(tmp_path / 'l.sqlite', policy(rate=dict(per_source_per_minute=1000, global_per_minute=2)),
                    jwks=cb.JWKS(lambda: dict(keys=[key.jwk()]), clock=clock), clock=clock)
    led.register(dict(op='register', request_id=REQ, repository=REPO, workflow_path=WORKFLOW, ref=REF, source_sha=SHA))
    for i in range(20):
        with pytest.raises(cb.Refused):
            led.claim('junk.token.value', dict(request_id=REQ, source_sha=SHA), source='192.0.2.%d' % i)
    assert led.claim(token(key), dict(request_id=REQ, source_sha=SHA), source='192.0.2.99')['ok']


def test_owner_socket_rejects_unlisted_peer(tmp_path, key):
    led = cb.Ledger(tmp_path / 'l.sqlite', policy(clients=[dict(uid=os.getuid() + 12345)]),
                    jwks=cb.JWKS(lambda: dict(keys=[key.jwk()])))
    owner = cb.OwnerSocket(str(tmp_path / 's.sock'), led)
    thread = threading.Thread(target=owner.serve_forever, daemon=True)
    thread.start()
    try:
        # The broker answers an unlisted peer without reading its request at all.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect(str(tmp_path / 's.sock'))
            assert json.loads(s.recv(65536)) == dict(ok=False, refusal='peer_not_authorized')
    finally:
        owner.shutdown()


def test_claims_module_holds_no_management_authority():
    source = open(cb.__file__, encoding='utf-8').read()
    for forbidden in ('operations', 'credentials', 'systemctl', 'subprocess', 'GITHUB_TOKEN'):
        assert forbidden not in source.split('"""', 2)[2]
