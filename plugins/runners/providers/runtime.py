"""Production read-only boundaries; no ambient GitHub credentials or proxies.

Credential mappings are private runtime configuration, not inventory YAML. Each
reference maps to a broker scope/env name and an explicit stable budget key;
aliases MUST use the same key. Tokens exist only in process memory and the
anonymous child pipe. No broker stdout/stderr is retained. Call invalidate(ref)
or invalidate(key=credential.key) after rejected authorization; TTL is a cache
ceiling, not a claim about the issuer's actual credential lifetime. A failed vend
(a broker denial such as the GitHub quota brake, a timeout, a bad pipe value) is
remembered per budget key for failure_ttl seconds: every alias sharing that key
fails fast without spawning the broker until the cool-down ends.

BrokerVendGitHub (collector, reconcile) is the metered variant: its token stays
inside the open broker vend, which performs and records every GET.
"""
from dataclasses import dataclass
import hashlib
import http.client
import json
import math
import os
import re
import ssl
import socket
import struct
import subprocess
import sys
from threading import RLock
import time
from urllib.parse import urlsplit

from .base import Credential, HTTPResponse


class RuntimeBoundaryError(RuntimeError):
    """Only fixed, non-sensitive reason codes cross the runtime boundary."""


def _bounded_number(value, maximum):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value <= maximum


_ALLOWED_HEADERS = frozenset({'authorization', 'accept', 'x-github-api-version', 'if-none-match', 'user-agent'})


def _validated_request(method, url, headers, timeout, allow_redirects):
    """(urlsplit target, lower-cased safe headers) of an admissible GET, else ValueError."""
    if (method != 'GET' or allow_redirects is not False
            or not _bounded_number(timeout, 300) or type(url) is not str
            or len(url) > 8192 or any(ord(c) <= 32 or ord(c) >= 127 for c in url)):
        raise ValueError()
    target = urlsplit(url)
    if (target.scheme != 'https' or target.netloc not in ('api.github.com', 'api.github.com:443')
            or target.fragment or '#' in url):
        raise ValueError()
    safe_headers = {}
    for key, value in headers.items():
        if (type(key) is not str or key.lower() not in _ALLOWED_HEADERS
                or key.lower() in safe_headers or type(value) is not str
                or not value or len(value) > 8192
                or any(ord(c) < 32 or ord(c) >= 127 for c in value)):
            raise ValueError()
        safe_headers[key.lower()] = value
    safe_headers.setdefault('user-agent', 'hermes-runners')
    return target, safe_headers


# A fresh interpreter avoids forking a multithreaded collector's locks. Import
# only this provider package, not plugin initialization. Request/response values
# travel over an anonymous socket, never argv, environment, files or stdout.
_HTTP_CHILD = '''
import importlib, json, os, socket, sys, types
try:
    package = types.ModuleType('_runners_http_worker')
    package.__path__ = [sys.argv[1]]
    sys.modules[package.__name__] = package
    runtime = importlib.import_module(package.__name__ + '.runtime')
    with socket.socket(fileno=int(sys.argv[2])) as channel:
        chunks = bytearray()
        while True:
            chunk = channel.recv(65536)
            if not chunk: break
            chunks.extend(chunk)
            if len(chunks) > 131072: raise ValueError()
        request = json.loads(chunks)
        client = runtime.GitHubHTTPTransport(
            connection_factory=runtime.http.client.HTTPSConnection,
            max_body_bytes=request.pop('max_body_bytes'),
            max_timeout=request.pop('max_timeout'))
        result = client(**request)
        channel.sendall(json.dumps([result.status, dict(result.headers), result.body],
                                   ensure_ascii=True).encode('ascii'))
except BaseException:
    os._exit(1)
'''


class GitHubHTTPTransport:
    """Direct TLS GET only; http.client never follows redirects or reads proxies.

    Production IO runs in a disposable subprocess under a parent monotonic
    deadline, including OS DNS, TLS, headers, body and connection cleanup. On
    expiry the worker is killed and reaped; no resolver threads survive. Process
    launch/reaping still depends on OS scheduling (not a hard real-time bound).
    Explicit connection_factory is an in-process, offline test/worker seam;
    callers supplying it are responsible for its blocking behavior.
    """
    def __init__(self, *, connection_factory=None,
                 max_body_bytes=4 * 1024 * 1024, max_timeout=30, clock=time.monotonic):
        if (type(max_body_bytes) is not int or not 0 < max_body_bytes <= 16 * 1024 * 1024
                or not _bounded_number(max_timeout, 60)):
            raise ValueError('invalid_transport_options')
        self.connection_factory = connection_factory
        self.max_body_bytes, self.max_timeout, self.clock = max_body_bytes, max_timeout, clock
        self._context = ssl.create_default_context() if connection_factory is not None else None

    def _isolated_request(self, url, headers, duration):
        # Use a real monotonic clock for the watchdog, not the injected unit-test
        # clock. One deadline covers launch, IPC, HTTP and normal worker exit.
        deadline = time.monotonic() + duration
        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError()
            return value

        process = None
        parent, child = socket.socketpair()
        try:
            process = subprocess.Popen(
                [sys.executable, '-I', '-B', '-c', _HTTP_CHILD,
                 os.path.dirname(os.path.abspath(__file__)), str(child.fileno())],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, pass_fds=(child.fileno(),),
                close_fds=True, shell=False, env={})
            child.close()
            payload = json.dumps(dict(method='GET', url=url, headers=headers,
                                      timeout=duration, allow_redirects=False,
                                      max_body_bytes=self.max_body_bytes,
                                      max_timeout=self.max_timeout)).encode('ascii')
            parent.settimeout(remaining())
            parent.sendall(payload)
            parent.shutdown(socket.SHUT_WR)
            raw = bytearray()
            # JSON escaping and bounded http.client headers add IPC overhead.
            limit = 6 * self.max_body_bytes + 8 * 1024 * 1024
            while True:
                parent.settimeout(remaining())
                chunk = parent.recv(min(65536, limit + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > limit:
                    raise ValueError()
            if process.wait(timeout=remaining()) != 0:
                raise ValueError()
            status, response_headers, body = json.loads(raw)
            remaining()
            return HTTPResponse(status, response_headers, body)
        finally:
            parent.close()
            child.close()
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()  # Reap even on timeout/cancellation; no zombies.

    def __call__(self, method, url, *, headers, timeout, allow_redirects):
        conn = None
        response = None
        response_headers = {}
        try:
            target, safe_headers = _validated_request(method, url, headers, timeout, allow_redirects)
            duration = min(timeout, self.max_timeout)
            if self.connection_factory is None:
                return self._isolated_request(url, safe_headers, duration)
            safe_headers['accept-encoding'] = 'identity'
            deadline = self.clock() + duration
            conn = self.connection_factory('api.github.com', timeout=duration, context=self._context)
            conn.request('GET', (target.path or '/') + ('?' + target.query if target.query else ''), headers=safe_headers)
            response = conn.getresponse()
            response_headers = {k.lower(): v for k, v in response.getheaders()}
            # Error bodies can echo credentials. For a 301 retain only the
            # strictly validated numeric repository route below, never messages.
            if response.status not in (200, 301):
                return HTTPResponse(response.status, response_headers)
            if response_headers.get('content-encoding', 'identity').lower() != 'identity':
                raise ValueError()
            length = response_headers.get('content-length')
            if length is not None and not 0 <= int(length) <= self.max_body_bytes:
                raise ValueError()
            body = bytearray()
            while True:
                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise TimeoutError()
                if conn.sock is not None:
                    conn.sock.settimeout(remaining)
                chunk = response.read1(min(65536, self.max_body_bytes + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > self.max_body_bytes:
                    raise ValueError()
            if length is not None and len(body) != int(length):
                raise ValueError()
            parsed = json.loads(body)
            if response.status == 301:
                url = parsed.get('url') if type(parsed) is dict else None
                allowed = r'https://api\.github\.com/repositories/[1-9][0-9]*(?:/actions/runners\?per_page=100&page=[1-9][0-9]*)?'
                parsed = {'url': url} if type(url) is str and re.fullmatch(allowed, url) else {}
            return HTTPResponse(response.status, response_headers, parsed)
        except Exception:
            if response is not None and response.status == 301:
                # A malformed moved resource must not poison its credential's
                # other scopes. Preserve the known status, discard all payload.
                return HTTPResponse(301, response_headers, {})
            raise RuntimeBoundaryError('http_transport_failed') from None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass  # Cleanup must not expose a transport exception.


@dataclass(frozen=True)
class BrokerCredential:
    scope: str
    env_var: str
    key: str


def _validated_references(references):
    references = dict(references)
    for ref, spec in references.items():
        if (type(ref) is not str or not ref or not isinstance(spec, BrokerCredential)
                or type(spec.scope) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,199}', spec.scope)
                or type(spec.env_var) is not str or not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,127}', spec.env_var)
                or type(spec.key) is not str or not spec.key or len(spec.key) > 256):
            raise ValueError('invalid_broker_reference')
    identities = {}
    for spec in references.values():
        identity = (spec.scope, spec.env_var)
        if identities.setdefault(identity, spec.key) != spec.key:
            raise ValueError('invalid_broker_reference')
    return references


def _validated_command(broker_command):
    if (not isinstance(broker_command, (tuple, list)) or not broker_command
            or any(type(x) is not str or not x or '\x00' in x for x in broker_command)):
        raise ValueError('invalid_broker_options')
    return tuple(broker_command)


# Values are not arguments: only env name and anonymous descriptor are passed.
# 4096 bytes fits PIPE_BUF on supported Linux/macOS runtimes, avoiding a blocked
# writer while the parent waits for the bounded broker process to finish.
_PIPE_CHILD = '''import os, sys
try:
    value = os.environ[sys.argv[1]].encode('ascii')
    if not value or len(value) > 4096 or any(c < 33 or c > 126 for c in value):
        raise ValueError()
    if os.write(int(sys.argv[2]), value) != len(value):
        raise ValueError()
except BaseException:
    os._exit(1)
'''


class BrokerCredentialResolver:
    """Callable CredentialResolver with per-reference bounded in-memory cache.

    The runner has subprocess.run's signature and is injectable for offline
    tests. Never use a shell or capture broker logs. No auto-approval or fallback
    to credentials in the parent environment is implemented.
    """
    def __init__(self, references, *, runner=subprocess.run, clock=time.monotonic,
                 ttl=300, timeout=30, broker_command=('token-broker',), failure_ttl=60):
        if (not _bounded_number(ttl, 3600) or not _bounded_number(timeout, 300)
                or not _bounded_number(failure_ttl, 3600)):
            raise ValueError('invalid_broker_options')
        self._references = _validated_references(references)
        self._command = _validated_command(broker_command)
        self.runner, self.clock, self.ttl, self.timeout = runner, clock, ttl, timeout
        self.failure_ttl = failure_ttl
        self._cache = {}
        # Budget key -> monotonic time before which a failed vend is not retried.
        # Keyed by key, not ref, so hundreds of scopes sharing one credential
        # cost one broker exec per cool-down instead of one each.
        self._failures = {}
        self._lock = RLock()

    def invalidate(self, ref=None, *, key=None):
        """Drop all aliases for ref/key; without either argument clear all."""
        with self._lock:
            if ref is None and key is None:
                self._cache.clear()
                self._failures.clear()
                return
            if ref in self._references:
                key = self._references[ref].key
            self._failures.pop(key, None)
            self._cache = {r: item for r, item in self._cache.items()
                           if self._references[r].key != key}

    def __call__(self, ref):
        with self._lock:
            read_fd = write_fd = None
            spec = None
            try:
                spec = self._references[ref]
                now = self.clock()
                cached = self._cache.get(ref)
                if cached is not None and now < cached[0]:
                    return cached[1]
                self._cache.pop(ref, None)
                retry_at = self._failures.get(spec.key)
                if retry_at is not None:
                    if now < retry_at:
                        spec = None  # Already recorded; do not extend the cool-down.
                        raise ValueError()
                    del self._failures[spec.key]
                read_fd, write_fd = os.pipe()
                os.set_blocking(read_fd, False)
                argv = [*self._command, 'exec', '--scope', spec.scope,
                        '--reason', 'Read runner inventory', '--', sys.executable,
                        '-I', '-c', _PIPE_CHILD, spec.env_var, str(write_fd)]
                # Broker authentication may need its normal environment, but the
                # requested credential must be supplied by exec, never inherited.
                environment = dict(os.environ)
                for configured in self._references.values():
                    environment.pop(configured.env_var, None)
                result = self.runner(argv, stdin=subprocess.DEVNULL, env=environment,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     pass_fds=(write_fd,), timeout=self.timeout, check=False,
                                     shell=False, close_fds=True)
                os.close(write_fd)
                write_fd = None
                if result.returncode != 0:
                    raise ValueError()
                raw = os.read(read_fd, 4097)
                if not raw or len(raw) > 4096 or any(c < 33 or c > 126 for c in raw):
                    raise ValueError()
                credential = Credential(spec.key, raw.decode('ascii'))
                self._cache[ref] = (self.clock() + self.ttl, credential)
                return credential
            except Exception:
                if spec is not None:
                    self._failures[spec.key] = self.clock() + self.failure_ttl
                raise RuntimeBoundaryError('credential_unavailable') from None
            finally:
                for fd in (read_fd, write_fd):
                    if fd is not None:
                        os.close(fd)



# ------------------------------------------------------------------ metered vends
#
# BrokerCredentialResolver returns the token to this process. The vend the GitHub
# quota supervisor (token-broker's user-scope wrapper, github_quota.py) records
# for it ends before any request, with zero requests, while the collector and the
# 6-hourly reconcile spend the shared per-user core bucket with nothing in the
# quota ledger (2026-11-12: ~520 unledgered calls per reconcile run, 2-5/min
# from the collector). BrokerVendGitHub keeps the token inside the vend instead.

_FRAME = struct.Struct('>I')
_REQUEST_LIMIT = 64 * 1024


def _send_frame(channel, payload):
    data = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    channel.sendall(_FRAME.pack(len(data)) + data)


def _recv_frame(channel, limit, deadline):
    """One length-prefixed JSON frame; None on a clean EOF before its first byte.
    `deadline` is time.monotonic(); expiry raises TimeoutError."""
    def exact(size, first):
        data = bytearray()
        while len(data) < size:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError()
            channel.settimeout(left)
            chunk = channel.recv(min(65536, size - len(data)))
            if not chunk:
                if first and not data:
                    return None
                raise ValueError()
            data.extend(chunk)
        return bytes(data)
    header = exact(_FRAME.size, True)
    if header is None:
        return None
    (size,) = _FRAME.unpack(header)
    if not 0 < size <= limit:
        raise ValueError()
    return json.loads(exact(size, False))


# Not -I: the quota supervisor loads its request instrumentation through
# PYTHONPATH (sitecustomize). -P and -s still keep cwd and user site out of
# sys.path. Values travel only over the inherited socket; the token is read
# from the vend's environment and never written to the channel.
_VEND_CHILD = '''
import importlib, os, sys, types
try:
    package = types.ModuleType('_runners_http_worker')
    package.__path__ = [sys.argv[1]]
    sys.modules[package.__name__] = package
    runtime = importlib.import_module(package.__name__ + '.runtime')
    runtime._serve_vend(int(sys.argv[2]), sys.argv[3], float(sys.argv[4]))
except BaseException:
    os._exit(1)
os._exit(0)
'''


def _serve_vend(fd, env_var, idle):
    """Vend side of BrokerVendGitHub: add the vended token to each validated GET.

    Runs under `token-broker exec`, so the quota supervisor's http.client hook
    admits (reserve, per-vend budget, brake) and records every request. Exits on
    EOF, after `idle` seconds without a request, or on any protocol error."""
    token = os.environ.get(env_var, '')
    raw = token.encode('ascii')
    if not raw or len(raw) > 4096 or any(c < 33 or c > 126 for c in raw):
        raise ValueError()
    for name in {env_var, 'GH_TOKEN', 'GITHUB_TOKEN'}:
        os.environ.pop(name, None)
    metered = getattr(http.client.HTTPConnection, '_hermes_quota', False) is True
    with socket.socket(fileno=fd) as channel:
        _send_frame(channel, {'ready': True, 'metered': metered})
        while True:
            request = _recv_frame(channel, _REQUEST_LIMIT, time.monotonic() + idle)
            if request is None:
                return
            if type(request) is not dict or set(request) != {'url', 'headers', 'timeout', 'max_body_bytes', 'max_timeout'}:
                raise ValueError()
            headers = request['headers']
            if type(headers) is not dict or any(type(k) is not str or k.lower() == 'authorization' for k in headers):
                raise ValueError()
            client = GitHubHTTPTransport(connection_factory=http.client.HTTPSConnection,
                                         max_body_bytes=request['max_body_bytes'],
                                         max_timeout=request['max_timeout'])
            try:
                result = client('GET', request['url'], headers=dict(headers, authorization='Bearer ' + token),
                                timeout=request['timeout'], allow_redirects=False)
                reply = {'status': result.status, 'headers': dict(result.headers), 'body': result.body}
            except RuntimeBoundaryError:
                # Includes the supervisor refusing the request (reserve, budget, brake).
                reply = {'error': 'http_transport_failed'}
            _send_frame(channel, reply)


class _VendSession:
    def __init__(self, process, channel, started, metered):
        self.process, self.channel, self.metered = process, channel, metered
        self.started = self.last = started
        self.used = 0


def _stop_vend(process, graceful):
    """Reap a vend. Graceful: its command already saw EOF; the supervisor takes a
    last quota sample and ends the vend. Otherwise SIGTERM the supervisor, which
    stops its command's process group and records the vend as ended."""
    if graceful:
        try:
            process.wait(timeout=15)
            return
        except subprocess.TimeoutExpired:
            pass
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            process.kill()
    process.wait()


class BrokerVendGitHub:
    """Credential resolver and transport whose GitHub GETs all run inside a broker vend.

    Per broker identity (scope, env var) one `token-broker exec` runs a small
    request server (_VEND_CHILD) for at most `ttl` seconds, `max_requests` GETs or
    `idle` seconds without a request, then a new vend starts on demand. The token
    never enters this process: the resolver returns Credential(key, <opaque
    handle>) and the transport maps the handle back to its vend, so the provider's
    budgets, ETag cache and 401 invalidation work unchanged. Where the broker
    supervises the scope (GitHub user scopes), every GET becomes a ledger
    `requests` row (rate_limit_used/resource) and is refused at the shared
    reserve or brake. Any failure or refusal closes that vend; a vend that cannot
    start (a broker denial, the reserve) fails resolution for failure_ttl per
    budget key, like BrokerCredentialResolver. max_requests stays below the
    supervisor's per-vend budget and own-burst brake (250 each).

    Usage: client = BrokerVendGitHub(refs); GitHubActions(transport=client.transport,
    credential_resolver=client); client.close() when done.
    """
    def __init__(self, references, *, broker_command=('token-broker',), ttl=240, idle=60,
                 max_requests=200, start_timeout=60, failure_ttl=60,
                 max_body_bytes=4 * 1024 * 1024, max_timeout=30,
                 popen=subprocess.Popen, clock=time.monotonic, executable=None):
        if (not _bounded_number(ttl, 3600) or not _bounded_number(idle, 3600)
                or not _bounded_number(start_timeout, 300) or not _bounded_number(failure_ttl, 3600)
                or type(max_requests) is not int or not 0 < max_requests < 250
                or type(max_body_bytes) is not int or not 0 < max_body_bytes <= 16 * 1024 * 1024
                or not _bounded_number(max_timeout, 60)):
            raise ValueError('invalid_broker_options')
        self._references = _validated_references(references)
        self._command = _validated_command(broker_command)
        self.ttl, self.idle, self.max_requests = ttl, idle, max_requests
        self.start_timeout, self.failure_ttl = start_timeout, failure_ttl
        self.max_body_bytes, self.max_timeout = max_body_bytes, max_timeout
        self.popen, self.clock = popen, clock
        self.executable = executable or sys.executable
        self._keys, self._handles = {}, {}
        for spec in self._references.values():
            identity = (spec.scope, spec.env_var)
            self._keys[identity] = spec.key
            # Stable per identity (the provider's ETag cache keys on it); not a secret.
            self._handles[identity] = 'vend-' + hashlib.sha256(
                (spec.scope + '\0' + spec.env_var).encode()).hexdigest()[:40]
        self._identities = {handle: identity for identity, handle in self._handles.items()}
        self._sessions = {}
        self._failures = {}
        self._lock = RLock()

    # -- vend lifecycle (call with the lock held)

    def _start(self, identity):
        scope, env_var = identity
        parent, child = socket.socketpair()
        process = None
        try:
            argv = [*self._command, 'exec', '--scope', scope, '--reason', 'Read runner inventory', '--',
                    self.executable, '-s', '-P', '-B', '-c', _VEND_CHILD,
                    os.path.dirname(os.path.abspath(__file__)), str(child.fileno()), env_var,
                    str(self.ttl + 60)]
            environment = dict(os.environ)
            for configured in self._references.values():
                environment.pop(configured.env_var, None)
            process = self.popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, env=environment, pass_fds=(child.fileno(),),
                                 close_fds=True, shell=False)
            child.close()
            ready = _recv_frame(parent, 4096, time.monotonic() + self.start_timeout)
            if (type(ready) is not dict or set(ready) != {'ready', 'metered'} or ready['ready'] is not True
                    or type(ready['metered']) is not bool):
                raise ValueError()
            return _VendSession(process, parent, self.clock(), ready['metered'])
        except BaseException:
            child.close()
            parent.close()
            if process is not None:
                _stop_vend(process, graceful=False)
            raise

    def _close(self, identity, graceful=True):
        session = self._sessions.pop(identity, None)
        if session is not None:
            try:
                session.channel.close()
            finally:
                _stop_vend(session.process, graceful)

    def _live(self, identity):
        session = self._sessions.get(identity)
        now = self.clock()
        if session is not None and (session.process.poll() is not None or now - session.started >= self.ttl
                                    or now - session.last >= self.idle or session.used >= self.max_requests):
            self._close(identity)
            session = None
        if session is None:
            key = self._keys[identity]
            retry_at = self._failures.get(key)
            if retry_at is not None:
                if now < retry_at:
                    raise ValueError()  # Already recorded; do not extend the cool-down.
                del self._failures[key]
            try:
                session = self._start(identity)
            except Exception:
                self._failures[key] = self.clock() + self.failure_ttl
                raise
            self._sessions[identity] = session
        return session

    # -- public surface

    def __call__(self, ref):
        with self._lock:
            try:
                spec = self._references[ref]
                identity = (spec.scope, spec.env_var)
                self._live(identity)
                return Credential(spec.key, self._handles[identity])
            except Exception:
                raise RuntimeBoundaryError('credential_unavailable') from None

    def transport(self, method, url, *, headers, timeout, allow_redirects):
        with self._lock:
            identity = None
            graceful = False
            try:
                _, safe_headers = _validated_request(method, url, headers, timeout, allow_redirects)
                scheme, _, handle = safe_headers.pop('authorization', '').partition(' ')
                if scheme.lower() != 'bearer' or handle not in self._identities:
                    raise ValueError()
                identity = self._identities[handle]
                session = self._live(identity)
                session.used += 1
                session.last = self.clock()
                duration = min(timeout, self.max_timeout)
                _send_frame(session.channel, dict(url=url, headers=safe_headers, timeout=duration,
                                                  max_body_bytes=self.max_body_bytes,
                                                  max_timeout=self.max_timeout))
                # JSON escaping and bounded http.client headers add IPC overhead.
                reply = _recv_frame(session.channel, 6 * self.max_body_bytes + 8 * 1024 * 1024,
                                    time.monotonic() + duration + 5)
                if type(reply) is dict and set(reply) == {'error'}:
                    graceful = True  # The vend answered: a transport failure or its refusal.
                    raise ValueError()
                if (type(reply) is not dict or set(reply) != {'status', 'headers', 'body'}
                        or type(reply['status']) is not int or type(reply['headers']) is not dict):
                    raise ValueError()
                if reply['status'] in (401, 403, 429):
                    # The quota supervisor latches such a vend blocked; start the next
                    # request in a fresh vend instead of failing it.
                    self._close(identity)
                return HTTPResponse(reply['status'], reply['headers'], reply['body'])
            except Exception:
                if identity is not None:
                    try:
                        self._close(identity, graceful)
                    except Exception:
                        pass  # Cleanup must not expose a process/transport exception.
                raise RuntimeBoundaryError('http_transport_failed') from None

    def invalidate(self, ref=None, *, key=None):
        """Close the vends of ref/key (all without either) and clear their cool-down."""
        with self._lock:
            if ref is None and key is None:
                self._failures.clear()
                targets = list(self._sessions)
            else:
                if ref in self._references:
                    key = self._references[ref].key
                self._failures.pop(key, None)
                targets = [i for i in self._sessions if self._keys[i] == key]
            for identity in targets:
                self._close(identity)

    def close(self):
        with self._lock:
            for identity in list(self._sessions):
                self._close(identity)
