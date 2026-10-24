"""Production read-only boundaries; no ambient GitHub credentials or proxies.

Credential mappings are private runtime configuration, not inventory YAML. Each
reference maps to a broker scope/env name and an explicit stable budget key;
aliases MUST use the same key. Tokens exist only in process memory and the
anonymous child pipe. No broker stdout/stderr is retained. Call invalidate(ref)
or invalidate(key=credential.key) after rejected authorization; TTL is a cache
ceiling, not a claim about the issuer's actual credential lifetime.
"""
from dataclasses import dataclass
import http.client
import json
import math
import os
import re
import ssl
import socket
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
        try:
            if (method != 'GET' or allow_redirects is not False
                    or not _bounded_number(timeout, 300) or type(url) is not str
                    or len(url) > 8192 or any(ord(c) <= 32 or ord(c) >= 127 for c in url)):
                raise ValueError()
            target = urlsplit(url)
            if (target.scheme != 'https' or target.netloc not in ('api.github.com', 'api.github.com:443')
                    or target.fragment or '#' in url):
                raise ValueError()
            allowed = {'authorization', 'accept', 'x-github-api-version', 'if-none-match', 'user-agent'}
            safe_headers = {}
            for key, value in headers.items():
                if (type(key) is not str or key.lower() not in allowed
                        or key.lower() in safe_headers or type(value) is not str
                        or not value or len(value) > 8192
                        or any(ord(c) < 32 or ord(c) >= 127 for c in value)):
                    raise ValueError()
                safe_headers[key.lower()] = value
            safe_headers.setdefault('user-agent', 'hermes-runners')
            duration = min(timeout, self.max_timeout)
            if self.connection_factory is None:
                return self._isolated_request(url, safe_headers, duration)
            safe_headers['accept-encoding'] = 'identity'
            deadline = self.clock() + duration
            conn = self.connection_factory('api.github.com', timeout=duration, context=self._context)
            conn.request('GET', (target.path or '/') + ('?' + target.query if target.query else ''), headers=safe_headers)
            response = conn.getresponse()
            response_headers = {k.lower(): v for k, v in response.getheaders()}
            # Error and redirect payloads can echo credentials. Never retain them.
            if response.status != 200:
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
            return HTTPResponse(response.status, response_headers, json.loads(body))
        except Exception:
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
                 ttl=300, timeout=30, broker_command=('token-broker',)):
        if not _bounded_number(ttl, 3600) or not _bounded_number(timeout, 300):
            raise ValueError('invalid_broker_options')
        self._references = dict(references)
        for ref, spec in self._references.items():
            if (type(ref) is not str or not ref or not isinstance(spec, BrokerCredential)
                    or type(spec.scope) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,199}', spec.scope)
                    or type(spec.env_var) is not str or not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,127}', spec.env_var)
                    or type(spec.key) is not str or not spec.key or len(spec.key) > 256):
                raise ValueError('invalid_broker_reference')
        identities = {}
        for spec in self._references.values():
            identity = (spec.scope, spec.env_var)
            if identities.setdefault(identity, spec.key) != spec.key:
                raise ValueError('invalid_broker_reference')
        if (not isinstance(broker_command, (tuple, list)) or not broker_command
                or any(type(x) is not str or not x or '\x00' in x for x in broker_command)):
            raise ValueError('invalid_broker_options')
        self._command = tuple(broker_command)
        self.runner, self.clock, self.ttl, self.timeout = runner, clock, ttl, timeout
        self._cache = {}
        self._lock = RLock()

    def invalidate(self, ref=None, *, key=None):
        """Drop all aliases for ref/key; without either argument clear all."""
        with self._lock:
            if ref is None and key is None:
                self._cache.clear()
                return
            if ref in self._references:
                key = self._references[ref].key
            self._cache = {r: item for r, item in self._cache.items()
                           if self._references[r].key != key}

    def __call__(self, ref):
        with self._lock:
            read_fd = write_fd = None
            try:
                spec = self._references[ref]
                now = self.clock()
                cached = self._cache.get(ref)
                if cached is not None and now < cached[0]:
                    return cached[1]
                self._cache.pop(ref, None)
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
                raise RuntimeBoundaryError('credential_unavailable') from None
            finally:
                for fd in (read_fd, write_fd):
                    if fd is not None:
                        os.close(fd)
