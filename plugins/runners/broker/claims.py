"""Execution-claim broker: atomic single-use claims authenticated by GitHub OIDC.

Separate from the management broker (``server.py``): its own OS identity, its own
policy, its own durable ledger and its own listeners. It holds NO management or
GitHub credentials and exposes only:

* an owner-side Unix socket (kernel SO_PEERCRED uid allowlist) where the gateway
  registers a dispatch intent BEFORE dispatching, and reads claim status;
* a workflow-facing HTTP listener where a running workflow presents a GitHub
  Actions OIDC token plus the request ID and exact source SHA it was dispatched
  with, and receives a single-use execution claim.

Rules (plan section 8 item 5):
* The token signature (RS256 only, issuer JWKS, pinned kid), issuer, audience,
  expiry/not-before/issued-at, repository, workflow file @ trusted ref, ref,
  event and self-hosted runner environment are verified. Dispatch inputs are NOT
  OIDC claims: request ID and source SHA must equal the persisted intent.
* The first (run_id, run_attempt) to claim a request wins, atomically. A lost
  response may be retried by the SAME run and attempt only. A re-run attempt or a
  different run fails closed. There is no lease expiry or takeover after an
  ambiguous or crashed build: an explicit retry is a new authorized request.

Stdlib only; runs from a root-owned copy as ``python3 -I claims.py``.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import pwd
import re
import signal
import socket
import sqlite3
import stat
import struct
import sys
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ISSUER = 'https://token.actions.githubusercontent.com'
JWKS_URL = ISSUER + '/.well-known/jwks'
REQUEST_ID = re.compile(r'request_[0-9a-f]{32}')
SHA = re.compile(r'[0-9a-f]{40}')
REPOSITORY = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/(?!\.\.?$)[A-Za-z0-9_.-]{1,100}')
WORKFLOW_PATH = re.compile(r'\.github/workflows/[A-Za-z0-9_.-]{1,100}\.ya?ml')
REF = re.compile(r'refs/heads/[A-Za-z0-9_./-]{1,200}')
DIGITS = re.compile(r'[1-9][0-9]{0,19}')
B64URL = re.compile(r'[A-Za-z0-9_-]+')
MAX_TOKEN = 8192
MAX_BODY = 4096
MAX_SOCKET_REQUEST = 16384
SKEW = 60
MAX_TOKEN_LIFETIME = 3600
INTENT_TTL_MAX = 7 * 86400
# DER prefix of DigestInfo for SHA-256 (RFC 8017 section 9.2 note 1).
_SHA256_DIGEST_INFO = bytes.fromhex('3031300d060960864801650304020105000420')


class Refused(Exception):
    """A fixed, non-sensitive refusal code; never carries submitted data."""

    def __init__(self, code, status=403):
        super().__init__(code)
        self.code, self.status = code, status


# ---------------------------------------------------------------- OIDC / RSA

def b64url_decode(text):
    if type(text) is not str or not text or not B64URL.fullmatch(text):
        raise Refused('token_malformed', 401)
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def rsa_sha256_verify(n, e, message, signature):
    """RSASSA-PKCS1-v1_5 verification with SHA-256 (public operation only)."""
    k = (n.bit_length() + 7) // 8
    if k < 256 or len(signature) != k or e not in (3, 65537):
        return False
    s = int.from_bytes(signature, 'big')
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, 'big')
    digest = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    expected = b'\x00\x01' + b'\xff' * (k - 3 - len(digest)) + b'\x00' + digest
    return hmac.compare_digest(em, expected)


class JWKS:
    """Issuer signing keys; refetched on unknown kid at most every ``min_refresh`` s."""

    def __init__(self, fetch, *, clock=time.time, ttl=3600, min_refresh=60):
        self.fetch, self.clock, self.ttl, self.min_refresh = fetch, clock, ttl, min_refresh
        self._keys, self._fetched, self._attempted = {}, None, None
        self._lock = threading.Lock()

    @staticmethod
    def parse(document):
        keys = {}
        for jwk in (document or {}).get('keys', [])[:64]:
            if not isinstance(jwk, dict) or jwk.get('kty') != 'RSA' or jwk.get('use', 'sig') != 'sig':
                continue
            if jwk.get('alg', 'RS256') != 'RS256' or type(jwk.get('kid')) is not str:
                continue
            try:
                n = int.from_bytes(b64url_decode(jwk['n']), 'big')
                e = int.from_bytes(b64url_decode(jwk['e']), 'big')
            except (Refused, KeyError, ValueError):
                continue
            keys[jwk['kid']] = (n, e)
        return keys

    def _refresh(self):
        now = self.clock()
        if self._attempted is not None and now - self._attempted < self.min_refresh:
            return
        self._attempted = now
        try:
            keys = self.parse(self.fetch())
        except Exception:
            return
        if keys:
            self._keys, self._fetched = keys, now

    def key(self, kid):
        with self._lock:
            stale = self._fetched is None or self.clock() - self._fetched > self.ttl
            if stale or kid not in self._keys:
                self._refresh()
            if kid not in self._keys:
                raise Refused('signing_key_unknown', 401)
            return self._keys[kid]


def fetch_jwks(url=JWKS_URL, timeout=10):
    if not url.startswith('https://token.actions.githubusercontent.com/'):
        raise ValueError('jwks_url_not_allowlisted')
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed https URL
        return json.loads(response.read(1024 * 1024))


def verify_token(token, *, jwks, audience, now):
    """Return verified claims or raise Refused. Only RS256 is accepted."""
    if type(token) is not str or not 0 < len(token) <= MAX_TOKEN:
        raise Refused('token_malformed', 401)
    parts = token.split('.')
    if len(parts) != 3:
        raise Refused('token_malformed', 401)
    try:
        header = json.loads(b64url_decode(parts[0]))
        payload_raw = b64url_decode(parts[1])
        signature = b64url_decode(parts[2])
    except (ValueError, UnicodeDecodeError):
        raise Refused('token_malformed', 401) from None
    if not isinstance(header, dict) or header.get('alg') != 'RS256' or type(header.get('kid')) is not str:
        raise Refused('token_algorithm_refused', 401)
    if set(header) - {'alg', 'kid', 'typ', 'x5t'}:
        raise Refused('token_header_refused', 401)
    n, e = jwks.key(header['kid'])
    if not rsa_sha256_verify(n, e, (parts[0] + '.' + parts[1]).encode('ascii'), signature):
        raise Refused('token_signature_invalid', 401)
    try:
        claims = json.loads(payload_raw)
    except (ValueError, UnicodeDecodeError):
        raise Refused('token_malformed', 401) from None
    if not isinstance(claims, dict):
        raise Refused('token_malformed', 401)
    if claims.get('iss') != ISSUER:
        raise Refused('token_issuer_invalid', 401)
    if type(claims.get('aud')) is not str or not hmac.compare_digest(claims['aud'], audience):
        raise Refused('token_audience_invalid', 401)
    exp, iat, nbf = claims.get('exp'), claims.get('iat'), claims.get('nbf', claims.get('iat'))
    if any(type(v) is not int for v in (exp, iat, nbf)):
        raise Refused('token_time_invalid', 401)
    if now >= exp + SKEW:
        raise Refused('token_expired', 401)
    if nbf > now + SKEW or iat > now + SKEW or exp - iat > MAX_TOKEN_LIFETIME:
        raise Refused('token_time_invalid', 401)
    return claims


# ---------------------------------------------------------------- policy

def load_policy(raw):
    if not isinstance(raw, dict) or raw.get('version') != 1:
        raise ValueError('policy_invalid')
    audience = raw.get('audience')
    if type(audience) is not str or not re.fullmatch(r'[A-Za-z0-9:._-]{8,200}', audience):
        raise ValueError('policy_invalid')
    clients = raw.get('clients')
    if not isinstance(clients, list) or not clients or any(
            not isinstance(c, dict) or type(c.get('uid')) is not int or c['uid'] < 0 for c in clients):
        raise ValueError('policy_invalid')
    workflows = []
    for w in raw.get('workflows', []):
        if (not isinstance(w, dict) or type(w.get('repository')) is not str or not REPOSITORY.fullmatch(w['repository'])
                or type(w.get('workflow_path')) is not str or not WORKFLOW_PATH.fullmatch(w['workflow_path'])
                or not isinstance(w.get('refs'), list) or not w['refs']
                or any(type(r) is not str or not REF.fullmatch(r) for r in w['refs'])):
            raise ValueError('policy_invalid')
        workflows.append(dict(repository=w['repository'], workflow_path=w['workflow_path'], refs=list(w['refs'])))
    rate = dict(per_source_per_minute=30, global_per_minute=120, intents_per_minute=30)
    for key, value in (raw.get('rate') or {}).items():
        if key not in rate or type(value) is not int or not 1 <= value <= 10000:
            raise ValueError('policy_invalid')
        rate[key] = value
    return dict(version=1, audience=audience, clients=[dict(uid=c['uid']) for c in clients],
                workflows=workflows, rate=rate)


# ---------------------------------------------------------------- ledger

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS intents (
  request_id TEXT PRIMARY KEY, repository TEXT NOT NULL, workflow_path TEXT NOT NULL,
  ref TEXT NOT NULL, source_sha TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS claims (
  request_id TEXT PRIMARY KEY REFERENCES intents(request_id), claim_id TEXT NOT NULL UNIQUE,
  run_id TEXT NOT NULL, run_attempt TEXT NOT NULL, workflow_sha TEXT NOT NULL,
  repository TEXT NOT NULL, workflow_path TEXT NOT NULL, claimed_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS refusals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT, code TEXT NOT NULL,
  run_id TEXT, run_attempt TEXT, at REAL NOT NULL);
'''


class RateLimiter:
    def __init__(self, clock):
        self.clock, self._events, self._lock = clock, {}, threading.Lock()

    def allow(self, key, per_minute):
        now = self.clock()
        with self._lock:
            events = [t for t in self._events.get(key, ()) if now - t < 60]
            if len(events) >= per_minute:
                self._events[key] = events
                return False
            events.append(now)
            self._events[key] = events
            if len(self._events) > 4096:
                self._events = {k: v for k, v in self._events.items() if v and now - v[-1] < 60}
            return True


class Ledger:
    """Durable unique-key claim ledger (SQLite, one connection per call)."""

    def __init__(self, path, policy, *, jwks, clock=time.time):
        self.path, self.policy, self.jwks, self.clock = str(path), policy, jwks, clock
        self.rates = RateLimiter(clock)
        with self._db() as db:
            db.executescript(_SCHEMA)

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        return _Closing(db)

    def _workflow(self, repository, workflow_path, ref):
        for w in self.policy['workflows']:
            if w['repository'] == repository and w['workflow_path'] == workflow_path and ref in w['refs']:
                return w
        raise Refused('workflow_not_allowlisted')

    # ---- owner-side intent API (Unix socket) ----
    def register(self, body):
        if not self.rates.allow('intents', self.policy['rate']['intents_per_minute']):
            raise Refused('rate_limited', 429)
        if set(body) - {'api', 'op', 'request_id', 'repository', 'workflow_path', 'ref', 'source_sha', 'ttl_seconds'}:
            raise Refused('unknown_field', 400)
        rid, repo, path, ref, sha = (body.get(k) for k in ('request_id', 'repository', 'workflow_path', 'ref', 'source_sha'))
        ttl = body.get('ttl_seconds', 86400)
        if (type(rid) is not str or not REQUEST_ID.fullmatch(rid) or type(sha) is not str or not SHA.fullmatch(sha)
                or type(repo) is not str or type(path) is not str or type(ref) is not str
                or type(ttl) is not int or not 60 <= ttl <= INTENT_TTL_MAX):
            raise Refused('invalid_request', 400)
        self._workflow(repo, path, ref)
        now = self.clock()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute('SELECT repository, workflow_path, ref, source_sha FROM intents WHERE request_id=?',
                                 (rid,)).fetchone()
                if row is None:
                    db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?)', (rid, repo, path, ref, sha, now, now + ttl))
                elif tuple(row) != (repo, path, ref, sha):
                    raise Refused('intent_conflict', 409)
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise
        return dict(ok=True, request_id=rid, registered=True)

    def status(self, body):
        rid = body.get('request_id')
        if type(rid) is not str or not REQUEST_ID.fullmatch(rid):
            raise Refused('invalid_request', 400)
        with self._db() as db:
            intent = db.execute('SELECT repository, workflow_path, ref, source_sha, expires_at FROM intents WHERE request_id=?',
                                (rid,)).fetchone()
            claim = db.execute('SELECT claim_id, run_id, run_attempt, workflow_sha, claimed_at FROM claims WHERE request_id=?',
                               (rid,)).fetchone()
            refused = db.execute('SELECT COUNT(*) FROM refusals WHERE request_id=?', (rid,)).fetchone()[0]
        if intent is None:
            return dict(ok=True, request_id=rid, intent=None, claim=None, refusals=refused)
        return dict(ok=True, request_id=rid, refusals=refused,
                    intent=dict(repository=intent[0], workflow_path=intent[1], ref=intent[2], source_sha=intent[3],
                                expires_at=intent[4]),
                    claim=None if claim is None else dict(claim_id=claim[0], run_id=claim[1], run_attempt=claim[2],
                                                          workflow_sha=claim[3], claimed_at=claim[4]))

    def reachability(self, body):
        """Latest successful claim per allowlisted workflow: proof runners reach and authenticate here."""
        with self._db() as db:
            rows = db.execute('SELECT repository, workflow_path, MAX(claimed_at) FROM claims GROUP BY repository, workflow_path').fetchall()
        return dict(ok=True, audience=self.policy['audience'],
                    workflows=[dict(repository=r[0], workflow_path=r[1], last_claim_at=r[2]) for r in rows])

    def handle_owner(self, body):
        op = body.get('op')
        if op == 'register':
            return self.register(body)
        if op == 'status':
            return self.status(body)
        if op == 'reachability':
            return self.reachability(body)
        if op == 'health':
            return dict(ok=True, audience=self.policy['audience'], workflows=len(self.policy['workflows']))
        raise Refused('unknown_op', 400)

    # ---- workflow-facing claim API (HTTP) ----
    def _refuse(self, code, rid=None, run_id=None, attempt=None, status=403):
        try:
            with self._db() as db:
                db.execute('INSERT INTO refusals (request_id, code, run_id, run_attempt, at) VALUES (?,?,?,?,?)',
                           (rid if isinstance(rid, str) and REQUEST_ID.fullmatch(rid) else None, code,
                            run_id, attempt, self.clock()))
        except sqlite3.Error:
            pass
        raise Refused(code, status)

    def claim(self, token, body, *, source='unknown'):
        rate = self.policy['rate']
        # Per-source before verification; the global bucket is charged only for verified tokens,
        # so junk from a few sources cannot starve real claims.
        if not self.rates.allow('source:' + source, rate['per_source_per_minute']):
            raise Refused('rate_limited', 429)
        claims = verify_token(token, jwks=self.jwks, audience=self.policy['audience'], now=self.clock())
        if not self.rates.allow('global', rate['global_per_minute']):
            raise Refused('rate_limited', 429)
        if not isinstance(body, dict) or set(body) - {'request_id', 'source_sha'}:
            raise Refused('invalid_request', 400)
        rid, sha = body.get('request_id'), body.get('source_sha')
        if type(rid) is not str or not REQUEST_ID.fullmatch(rid) or type(sha) is not str or not SHA.fullmatch(sha):
            raise Refused('invalid_request', 400)
        run_id, attempt = str(claims.get('run_id', '')), str(claims.get('run_attempt', ''))
        if not DIGITS.fullmatch(run_id) or not DIGITS.fullmatch(attempt):
            self._refuse('token_run_identity_invalid', rid)
        workflow_sha = claims.get('sha')
        if type(workflow_sha) is not str or not SHA.fullmatch(workflow_sha):
            self._refuse('token_sha_invalid', rid, run_id, attempt)
        with self._db() as db:
            intent = db.execute('SELECT repository, workflow_path, ref, source_sha, expires_at FROM intents WHERE request_id=?',
                                (rid,)).fetchone()
        if intent is None:
            self._refuse('request_unknown', rid, run_id, attempt)
        repo, path, ref, intended_sha, expires_at = intent
        try:
            self._workflow(repo, path, ref)
        except Refused:
            self._refuse('workflow_not_allowlisted', rid, run_id, attempt)
        if claims.get('repository') != repo:
            self._refuse('repository_mismatch', rid, run_id, attempt)
        expected_ref = '%s/%s@%s' % (repo, path, ref)
        if claims.get('workflow_ref') != expected_ref or claims.get('job_workflow_ref', expected_ref) != expected_ref:
            self._refuse('workflow_mismatch', rid, run_id, attempt)
        if claims.get('ref') != ref:
            self._refuse('ref_mismatch', rid, run_id, attempt)
        if claims.get('event_name') != 'workflow_dispatch':
            self._refuse('event_mismatch', rid, run_id, attempt)
        if claims.get('runner_environment') != 'self-hosted':
            self._refuse('runner_environment_mismatch', rid, run_id, attempt)
        if not hmac.compare_digest(sha, intended_sha):
            self._refuse('source_sha_mismatch', rid, run_id, attempt)
        now = self.clock()
        expired = now > expires_at
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute('SELECT claim_id, run_id, run_attempt, workflow_sha, claimed_at FROM claims WHERE request_id=?',
                                 (rid,)).fetchone()
                if row is None and not expired:
                    claim_id = 'claim_' + uuid.uuid4().hex
                    db.execute('INSERT INTO claims VALUES (?,?,?,?,?,?,?,?)',
                               (rid, claim_id, run_id, attempt, workflow_sha, repo, path, now))
                    db.execute('COMMIT')
                    return dict(ok=True, claim_id=claim_id, request_id=rid, source_sha=sha, run_id=run_id,
                                run_attempt=attempt, workflow_sha=workflow_sha, replay=False)
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise
        if row is None:
            self._refuse('intent_expired', rid, run_id, attempt)
        if row[1] == run_id and row[2] == attempt and row[3] == workflow_sha:
            return dict(ok=True, claim_id=row[0], request_id=rid, source_sha=sha, run_id=run_id,
                        run_attempt=attempt, workflow_sha=workflow_sha, replay=True)
        self._refuse('claim_already_used', rid, run_id, attempt, status=409)


class _Closing:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *exc):
        self.db.close()


# ---------------------------------------------------------------- listeners

def peer_uid(conn):
    raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
    return struct.unpack('3i', raw)[1]


class OwnerSocket:
    """Intent/status API for the owner gateway only; never routes management."""

    def __init__(self, path, ledger, *, socket_mode=0o666, max_concurrent=8):
        self.path, self.ledger = path, ledger
        self._slots = threading.BoundedSemaphore(max_concurrent)
        if os.path.exists(path):
            os.unlink(path)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            self.sock.bind(path)
        finally:
            os.umask(old)
        os.chmod(path, socket_mode)
        self.sock.listen(16)
        self.sock.settimeout(0.5)
        self._stop = threading.Event()

    def _serve(self, conn):
        try:
            conn.settimeout(10)
            uid = peer_uid(conn)
            if not any(c['uid'] == uid for c in self.ledger.policy['clients']):
                # Refuse before reading anything from an unlisted peer.
                conn.sendall(json.dumps(dict(ok=False, refusal='peer_not_authorized')).encode())
                return
            data = b''
            while len(data) <= MAX_SOCKET_REQUEST:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            if len(data) > MAX_SOCKET_REQUEST:
                result = dict(ok=False, refusal='request_too_large')
            else:
                try:
                    body = json.loads(data)
                    if not isinstance(body, dict) or body.get('api') != 'claims_owner':
                        raise Refused('unknown_api', 400)
                    result = self.ledger.handle_owner(body)
                except Refused as exc:
                    result = dict(ok=False, refusal=exc.code)
                except ValueError:
                    result = dict(ok=False, refusal='invalid_request')
            conn.sendall(json.dumps(result).encode())
        except (OSError, sqlite3.Error):
            pass
        finally:
            conn.close()
            self._slots.release()

    def serve_forever(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                raise
            if not self._slots.acquire(blocking=False):
                conn.close()
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def shutdown(self):
        self._stop.set()
        try:
            self.sock.close()
        finally:
            if os.path.exists(self.path):
                os.unlink(self.path)


def make_http_server(address, ledger, *, max_concurrent=32, io_timeout=10):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'hermes-runners-claims/1'
        sys_version = ''
        timeout = io_timeout  # per-connection socket timeout: slow/stalled clients are dropped

        def log_message(self, *args):  # no request logging of headers/tokens
            pass

        def _send(self, status, payload):
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == '/v1/health':
                return self._send(200, dict(ok=True, service='hermes-runners-claims', audience=ledger.policy['audience']))
            self._send(404, dict(ok=False, refusal='not_found'))

        def do_POST(self):
            if self.path != '/v1/claims':
                return self._send(404, dict(ok=False, refusal='not_found'))
            try:
                if self.headers.get('Transfer-Encoding') is not None:
                    raise Refused('invalid_request', 400)
                try:
                    length = int(self.headers.get('Content-Length', '-1'))
                except ValueError:
                    raise Refused('invalid_request', 400) from None
                auth = self.headers.get('Authorization', '')
                if not 0 < length <= MAX_BODY:
                    raise Refused('invalid_request', 400)
                if not auth.startswith('Bearer ') or len(auth) > MAX_TOKEN + 7:
                    raise Refused('token_missing', 401)
                try:
                    body = json.loads(self.rfile.read(length))
                except ValueError:
                    raise Refused('invalid_request', 400) from None
                result = ledger.claim(auth[7:], body, source=self.client_address[0])
                self._send(200, result)
            except Refused as exc:
                self._send(exc.status, dict(ok=False, refusal=exc.code))
            except (sqlite3.Error, OSError):
                try:
                    self._send(503, dict(ok=False, refusal='ledger_unavailable'))
                except OSError:
                    pass

    slots = threading.BoundedSemaphore(max_concurrent)

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 32

        def process_request(self, request, client_address):
            if not slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            super().process_request(request, client_address)

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                slots.release()

    server = Server(address, Handler)
    server.timeout = 10
    return server


def read_policy(path, *, require_root_owner=True):
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise PermissionError('policy_not_regular')
    if stat.S_IMODE(st.st_mode) & 0o022:
        raise PermissionError('policy_writable_by_others')
    if require_root_owner and st.st_uid != 0:
        raise PermissionError('policy_not_root_owned')
    with open(path, encoding='utf-8') as f:
        return load_policy(json.load(f))


def main(argv=None):
    ap = argparse.ArgumentParser(description='Hermes runners execution-claim broker')
    ap.add_argument('--policy', default='/etc/hermes-runners-claims/policy.json')
    ap.add_argument('--state-dir', default='/var/lib/hermes-runners-claims')
    ap.add_argument('--socket', default='/run/hermes-runners-claims/intent.sock')
    ap.add_argument('--listen', required=True, help='workflow-facing HOST:PORT (tailnet address)')
    args = ap.parse_args(argv)
    if os.geteuid() == 0:
        raise SystemExit('refusing to run as root; run as the dedicated claims identity')
    policy = read_policy(args.policy)
    if any(c['uid'] == os.geteuid() for c in policy['clients']):
        raise SystemExit('claims identity must differ from every client identity')
    host, _, port = args.listen.rpartition(':')
    ledger = Ledger(os.path.join(args.state_dir, 'ledger.sqlite'), policy, jwks=JWKS(fetch_jwks))
    owner = OwnerSocket(args.socket, ledger)
    http = make_http_server((host, int(port)), ledger)
    threading.Thread(target=owner.serve_forever, daemon=True).start()

    def stop(*_):
        owner.shutdown()
        threading.Thread(target=http.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    print(json.dumps(dict(event='claims_ready', user=pwd.getpwuid(os.geteuid()).pw_name, listen=args.listen,
                          workflows=len(policy['workflows']))), flush=True)
    http.serve_forever()


if __name__ == '__main__':
    main()
