"""Read-only installation credentials used only inside an isolated broker vend.

Configuration contains env *names*, never key material. Coverage is an exact,
case-insensitive allowlist, not an owner wildcard. No discovery or PAT fallback.
"""
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import http.client
import json
import os
import re
import ssl
import subprocess
import time
from urllib.parse import urlsplit


@dataclass(frozen=True)
class GitHubAppInstallation:
    app_id_env: str
    private_key_env: str  # base64-encoded PEM supplied by the broker
    installation_id: str
    repositories: tuple[str, ...] = ()  # owner/name; token narrowed to exactly these
    organizations: tuple[str, ...] = ()  # exact org runner-list coverage

    def validate(self):
        env = r'[A-Z_][A-Z0-9_]{0,127}'
        owner = r'[A-Za-z0-9][A-Za-z0-9-]{0,38}'
        repo = owner + r'/(?!\.{1,2}$)[A-Za-z0-9_.-]{1,100}'
        if (type(self.app_id_env) is not str or not re.fullmatch(env, self.app_id_env)
                or type(self.private_key_env) is not str or not re.fullmatch(env, self.private_key_env)
                or self.app_id_env == self.private_key_env
                or type(self.installation_id) is not str
                or not re.fullmatch(r'[1-9][0-9]{0,19}', self.installation_id)
                or type(self.repositories) is not tuple or type(self.organizations) is not tuple
                or not (self.repositories or self.organizations)):
            raise ValueError('invalid_app_configuration')
        for values, pattern in ((self.repositories, repo), (self.organizations, owner)):
            if (len(values) > 500 or any(type(v) is not str or not re.fullmatch(pattern, v) for v in values)
                    or len({v.lower() for v in values}) != len(values)):
                raise ValueError('invalid_app_configuration')
        owners = {v.split('/')[0].lower() for v in self.repositories} | {v.lower() for v in self.organizations}
        if len(owners) != 1:
            raise ValueError('invalid_app_configuration')
        return self

    @property
    def quota_key(self):
        # Installation core quotas are shared across Apps' tokens for this installation.
        return 'github-app-installation:' + self.installation_id

    @property
    def permissions(self):
        result = {'metadata': 'read'}
        if self.repositories:
            result.update(actions='read', administration='read')
        if self.organizations:
            result['organization_self_hosted_runners'] = 'read'
        return result

    def permits(self, url):
        target = urlsplit(url)
        if target.scheme != 'https' or target.netloc not in ('api.github.com', 'api.github.com:443') or target.fragment:
            return False
        # Reject encoded path separators, dot segments and numeric moved-repo
        # targets: an unverified redirect cannot establish configured coverage.
        repo_match = re.fullmatch(r'/repos/([^/]+/[^/]+)(/actions/(?:runners|runs(?:/[1-9][0-9]*/jobs)?))', target.path)
        org_match = re.fullmatch(r'/orgs/([^/]+)/actions/runners', target.path)
        return bool((repo_match and repo_match[1].lower() in {r.lower() for r in self.repositories})
                    or (org_match and org_match[1].lower() in {o.lower() for o in self.organizations}))


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def _sign(message, pem):
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError:
        # No disk file, key in argv or inherited environment. Linux stdlib-only
        # workers use anonymous memory; other platforms fail closed without crypto.
        fd = os.memfd_create('runners-app-signing', os.MFD_CLOEXEC)
        try:
            with os.fdopen(os.dup(fd), 'wb') as stream:
                stream.write(pem)
            os.lseek(fd, 0, os.SEEK_SET)
            result = subprocess.run(
                ['openssl', 'dgst', '-sha256', '-sign', '/proc/self/fd/' + str(fd)],
                input=message, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=20, pass_fds=(fd,), env={'PATH': os.defpath}, check=True)
            return result.stdout
        finally:
            os.close(fd)
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError()
    return key.sign(message, padding.PKCS1v15(), hashes.SHA256())


def _jwt(app_id, pem, now):
    message = (_b64(b'{"alg":"RS256","typ":"JWT"}') + '.' + _b64(json.dumps(
        {'iat': int(now) - 60, 'exp': int(now) + 300, 'iss': app_id},
        separators=(',', ':')).encode('ascii'))).encode('ascii')
    return message.decode('ascii') + '.' + _b64(_sign(message, pem))


def _exchange(jwt, spec, timeout):
    conn = http.client.HTTPSConnection('api.github.com', timeout=timeout, context=ssl.create_default_context())
    try:
        body = {'permissions': spec.permissions}
        if spec.repositories:
            body['repositories'] = [r.split('/')[1] for r in spec.repositories]
        else:
            body['repository_ids'] = []
        conn.request('POST', '/app/installations/' + spec.installation_id + '/access_tokens',
                     body=json.dumps(body).encode('ascii'), headers={
                         'authorization': 'Bearer ' + jwt, 'accept': 'application/vnd.github+json',
                         'content-type': 'application/json', 'user-agent': 'hermes-runners',
                         'x-github-api-version': '2022-11-28', 'accept-encoding': 'identity'})
        response = conn.getresponse()
        if response.status != 201 or response.getheader('content-encoding', 'identity') != 'identity':
            raise ValueError()
        raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError()
        return json.loads(raw)
    finally:
        conn.close()


def mint_installation_token(spec, *, minimum_lifetime=300, clock=time.time, exchange=_exchange):
    """Return (token, UTC expiry epoch); never call outside the broker child.

    Token grant, repository selection and expiry must exactly match the request.
    GitHub's automatic metadata read permission is included explicitly. Any
    missing/mismatched response or secret is fatal; never obtain a user token.
    """
    spec.validate()
    app_id = os.environ.pop(spec.app_id_env, '')
    encoded = os.environ.pop(spec.private_key_env, '')
    if not re.fullmatch(r'[1-9][0-9]{0,19}', app_id) or not 0 < len(encoded) <= 65536:
        raise ValueError()
    pem = base64.b64decode(encoded, validate=True)
    now = clock()
    grant = exchange(_jwt(app_id, pem, now), spec, 20)
    if type(grant) is not dict or grant.get('permissions') != spec.permissions:
        raise ValueError()
    repos = grant.get('repositories')
    if (type(repos) is not list or any(type(r) is not dict or type(r.get('full_name')) is not str for r in repos)
            or sorted(r['full_name'].lower() for r in repos) != sorted(r.lower() for r in spec.repositories)):
        raise ValueError()
    expiry_text = grant.get('expires_at')
    if type(expiry_text) is not str or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', expiry_text):
        raise ValueError()
    expiry = datetime.strptime(expiry_text, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp()
    if not clock() + minimum_lifetime < expiry <= now + 3660:
        raise ValueError()
    token = grant.get('token')
    if type(token) is not str or not 0 < len(token) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError()
    return token, expiry
