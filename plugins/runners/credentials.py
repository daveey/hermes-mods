"""Credential-reference bridge for the management broker (stdlib only).

Management credentials live in a directory owned by the broker's own OS
identity (mode 0700, files 0600). The repair agent's Unix user cannot open
them; only the broker process resolves an opaque credential_ref to a value,
uses it for one outbound request, and never returns, logs or echoes it.
"""
import base64
import json
import os
import re
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

REF = re.compile(r'cred_[0-9a-f]{16,64}')
MAX_BYTES = 8192


class CredentialError(Exception):
    """Raised without ever including credential material in the message."""


def _check_dir(directory, expected_uid):
    st = os.lstat(directory)
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise CredentialError('credential_dir_invalid')
    if st.st_uid not in (0, expected_uid):
        raise CredentialError('credential_dir_wrong_owner')
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise CredentialError('credential_dir_not_private')


def _read_raw(directory, ref, expected_uid):
    if not isinstance(ref, str) or not REF.fullmatch(ref):
        raise CredentialError('credential_ref_invalid')
    directory = os.fspath(directory)
    try:
        _check_dir(directory, expected_uid)
        fd = os.open(os.path.join(directory, ref), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except CredentialError:
        raise
    except OSError:
        raise CredentialError('credential_unavailable') from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise CredentialError('credential_not_regular')
        if st.st_uid != expected_uid:
            raise CredentialError('credential_wrong_owner')
        if stat.S_IMODE(st.st_mode) & 0o077:
            raise CredentialError('credential_not_private')
        if st.st_size > MAX_BYTES:
            raise CredentialError('credential_too_large')
        raw = os.read(fd, MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > MAX_BYTES:
        raise CredentialError('credential_too_large')
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        raise CredentialError('credential_not_text') from None


def load_secret(directory, ref, *, expected_uid):
    value = _read_raw(directory, ref, expected_uid).strip()
    if not value or any(c.isspace() for c in value):
        raise CredentialError('credential_malformed')
    return value


PEM_RE = re.compile(r'-----BEGIN (RSA )?PRIVATE KEY-----\n[A-Za-z0-9+/=\n]+-----END (RSA )?PRIVATE KEY-----\n?')


def load_private_key(directory, ref, *, expected_uid):
    """Same owner/mode checks as ``load_secret``; returns a PEM private key (multi-line)."""
    text = _read_raw(directory, ref, expected_uid).strip() + '\n'
    if not PEM_RE.fullmatch(text):
        raise CredentialError('credential_not_private_key')
    return text


def sign_rs256(pem, data):
    """RSASSA-PKCS1-v1_5/SHA-256 via the openssl CLI; the key travels over a pipe, never a file or argv."""
    r, w = os.pipe()
    try:
        os.write(w, pem.encode())
        os.close(w)
        w = None
        proc = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', '/dev/fd/%d' % r], input=data,
                              capture_output=True, timeout=15, check=False, pass_fds=(r,))
    except (OSError, subprocess.SubprocessError):
        raise CredentialError('app_jwt_sign_failed') from None
    finally:
        os.close(r)
        if w is not None:
            os.close(w)
    if proc.returncode != 0 or not proc.stdout:
        raise CredentialError('app_jwt_sign_failed')
    return proc.stdout


GITHUB_API = 'https://api.github.com'
TOKEN_SOURCE = 'app_installation'
EXPIRY_MARGIN = 300
# The only permissions a broker token may ever carry: Administration:read is what GET runner needs,
# Metadata:read is implicit on every installation token.
REQUESTED_PERMISSIONS = {'administration': 'read'}
ALLOWED_GRANTED = {'administration': 'read', 'metadata': 'read'}


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def _http(method, url, headers, body, timeout):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as resp:
            return resp.status, resp.read(65536)
    except urllib.error.HTTPError as exc:
        return exc.code, b''


class AppTokenMinter:
    """Short-lived GitHub App installation tokens, scoped to one repo, cached in memory only.

    There is deliberately no other credential path: any failure raises ``CredentialError`` with an
    explicit code and the caller must not substitute another token.
    """

    def __init__(self, directory, *, expected_uid, transport=None, clock=time.time, signer=None):
        self.directory, self.expected_uid = directory, expected_uid
        self.transport, self.clock, self.signer = transport or _http, clock, signer or sign_rs256
        self._lock = threading.Lock()
        self._installations, self._tokens = {}, {}

    def _call(self, method, url, headers, body):
        try:
            return self.transport(method, url, headers, body, 10)
        except Exception:  # noqa: BLE001 - message may echo headers
            raise CredentialError('app_request_failed') from None

    def _jwt(self, app_id, pem):
        now = int(self.clock())
        head = _b64(json.dumps({'alg': 'RS256', 'typ': 'JWT'}, separators=(',', ':')).encode())
        claims = _b64(json.dumps({'iat': now - 60, 'exp': now + 540, 'iss': str(app_id)}, separators=(',', ':')).encode())
        signing_input = head + '.' + claims
        return signing_input + '.' + _b64(self.signer(pem, signing_input.encode()))

    def token(self, *, app_id, key_ref, repo):
        if type(app_id) is not int:
            raise CredentialError('app_id_missing')
        key = (app_id, repo)
        with self._lock:
            cached = self._tokens.get(key)
            if cached and self.clock() < cached[1] - EXPIRY_MARGIN:
                return cached[0]
            pem = load_private_key(self.directory, key_ref, expected_uid=self.expected_uid)
            headers = {'Authorization': 'Bearer ' + self._jwt(app_id, pem), 'Accept': 'application/vnd.github+json',
                       'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'hermes-runners-broker'}
            owner_name = repo.split('/', 1)
            if len(owner_name) != 2:
                raise CredentialError('app_repo_invalid')
            inst = self._installations.get(key)
            if inst is None:
                status, body = self._call('GET', '%s/repos/%s/installation' % (GITHUB_API, repo), headers, None)
                if status != 200:
                    raise CredentialError('app_installation_lookup_failed_status_%d' % int(status))
                try:
                    inst = int(json.loads(body)['id'])
                except (ValueError, KeyError, TypeError):
                    raise CredentialError('app_installation_malformed') from None
                self._installations[key] = inst
            payload = json.dumps({'repositories': [owner_name[1]], 'permissions': REQUESTED_PERMISSIONS}).encode()
            status, body = self._call('POST', '%s/app/installations/%d/access_tokens' % (GITHUB_API, inst),
                                      dict(headers, **{'Content-Type': 'application/json'}), payload)
            if status != 201:
                raise CredentialError('app_token_mint_failed_status_%d' % int(status))
            try:
                data = json.loads(body)
                token, expires = data['token'], data['expires_at']
                expires_at = datetime.strptime(expires, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp()
                granted = data.get('permissions')
                names = [r.get('name') for r in data.get('repositories', [])]
            except (ValueError, KeyError, TypeError, AttributeError):
                raise CredentialError('app_token_malformed') from None
            if not isinstance(token, str) or not token or any(c.isspace() for c in token):
                raise CredentialError('app_token_malformed')
            if (not isinstance(granted, dict) or not granted.items() <= ALLOWED_GRANTED.items()
                    or names != [owner_name[1]]):
                raise CredentialError('app_token_overbroad')
            self._tokens[key] = (token, expires_at)
            return token


def redact(text, secrets):
    out = str(text)
    for secret in secrets:
        if secret:
            out = out.replace(secret, '[REDACTED]')
    # Defense in depth for token shapes we did not load ourselves.
    return re.sub(r'\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})', '[REDACTED]', out)
