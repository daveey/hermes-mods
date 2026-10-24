"""Provision fixed probes; collect only two protocol tokens, never commands.

Private provisioning (not performed by this module): resolve opaque service_refs
from Host/RunnerBinding to exact unit/launchd targets in ProbeSpec. Resolve the
opaque ssh_target_ref to SSHTarget outside public config. render_script returns
standalone Python source; install it and a Python3 interpreter at root-owned,
non-writable paths. In dedicated account authorized_keys use:
  restrict,command="/trusted/python3 -I /trusted/runner-probe.py" ssh-ed25519 ...
Also explicitly disable PTY, agent/X11/TCP forwarding and user rc (restrict does
so on modern OpenSSH); disable password/interactive auth for that account. Use a
dedicated key, not an existing administrative identity. Pre-pin the host key by
an authenticated out-of-band route. Never accept ssh-keyscan alone as trust.
The probe ignores all client input except two exact protocol tokens; no stdin,
script upload, arbitrary arguments or runtime service/path selection exists.
No sudo, service changes or remote file/cache writes. The gateway owns the
15-minute in-memory tool cache. Collector owns cadence and global concurrency.
Changing mappings requires explicit re-provisioning, not runtime substitution.
"""
from dataclasses import dataclass
import inspect
import json
import math
import os
import re
import selectors
import signal
import subprocess
import time
from types import MappingProxyType

from . import linux, macos

TOTAL_TIMEOUT = 15
MAX_OUTPUT = 65536
TOOL_TTL = 900
TOOLS = {
    'linux': {'git': ['/usr/bin/git', '--version'], 'python3': ['/usr/bin/python3', '--version']},
    'macos': {'git': ['/usr/bin/git', '--version'], 'python3': ['/usr/bin/python3', '--version'], 'xcode': ['/usr/bin/xcodebuild', '-version'], 'swift': ['/usr/bin/xcrun', 'swift', '--version']},
}


class ProbeError(RuntimeError):
    """Sanitized failure code; never includes SSH stderr or remote output."""


def _bounded_run(argv, *, timeout=15, max_output=65536):
    """Bound combined bytes and wall time; kill/reap process group on failure."""
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True,
                                   env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'C', 'LC_ALL': 'C'})
    except OSError:
        raise ProbeError('process_unavailable') from None
    output = bytearray()
    count = 0
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, True)
            selector.register(process.stderr, selectors.EVENT_READ, False)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProbeError('timeout')
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), min(8192, max_output - count + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    count += len(chunk)
                    if count > max_output:
                        raise ProbeError('output_limit')
                    if key.data:
                        output.extend(chunk)
            try:
                rc = process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise ProbeError('timeout') from None
            if rc:
                raise ProbeError('process_failed')
            return bytes(output)
    finally:
        # Kill descendants too: SSH wrappers must not survive timeout/pipe EOF.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()
        process.stderr.close()


def _path(value):
    if not isinstance(value, str) or len(value) > 1024 or not re.fullmatch(r'/[A-Za-z0-9_./ -]*', value) or any(p in ('.', '..') for p in value.split('/')):
        raise ValueError('invalid_path')
    return value


@dataclass(frozen=True)
class ProbeSpec:
    platform: str
    services: dict
    build_volume: str
    tools: tuple = ()

    def __post_init__(self):
        if self.platform not in TOOLS or not isinstance(self.services, dict) or not 1 <= len(self.services) <= 32:
            raise ValueError('invalid_spec')
        validator = linux if self.platform == 'linux' else macos
        for ref, target in self.services.items():
            if not isinstance(ref, str) or not re.fullmatch(r'[a-z]{2,12}_[0-9a-f]{16,64}', ref):
                raise ValueError('invalid_service_ref')
            validator.validate_service(target)
        if len(set(self.services.values())) != len(self.services):
            raise ValueError('duplicate_service')
        _path(self.build_volume)
        if not isinstance(self.tools, (tuple, list)) or any(not isinstance(t, str) or t not in TOOLS[self.platform] for t in self.tools) or len(set(self.tools)) != len(self.tools):
            raise ValueError('invalid_tools')
        object.__setattr__(self, 'services', MappingProxyType(dict(self.services)))
        object.__setattr__(self, 'tools', tuple(self.tools))


@dataclass(frozen=True)
class SSHTarget:
    hostname: str
    username: str
    identity_file: str
    known_hosts_file: str
    port: int = 22

    def __post_init__(self):
        if not isinstance(self.hostname, str) or len(self.hostname) > 253 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', self.hostname):
            raise ValueError('invalid_hostname')
        if not isinstance(self.username, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,63}', self.username):
            raise ValueError('invalid_username')
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('invalid_port')
        _path(self.identity_file)
        _path(self.known_hosts_file)
        # OpenSSH parses UserKnownHostsFile as a whitespace-separated list.
        if ' ' in self.identity_file or ' ' in self.known_hosts_file:
            raise ValueError('invalid_ssh_path')


def ssh_argv(target, *, include_tools=False):
    options = ['BatchMode=yes', 'ConnectTimeout=5', 'ConnectionAttempts=1',
               'StrictHostKeyChecking=yes', 'IdentitiesOnly=yes', 'IdentityAgent=none',
               'GlobalKnownHostsFile=/dev/null', 'UpdateHostKeys=no', 'VerifyHostKeyDNS=no',
               'ClearAllForwardings=yes', 'ForwardAgent=no', 'ForwardX11=no',
               'PermitLocalCommand=no', 'ProxyCommand=none', 'ProxyJump=none',
               'ControlMaster=no', 'ControlPath=none', 'ControlPersist=no',
               'PreferredAuthentications=publickey', 'PasswordAuthentication=no',
               'KbdInteractiveAuthentication=no', 'NumberOfPasswordPrompts=0',
               'ServerAliveInterval=5', 'ServerAliveCountMax=1', 'LogLevel=ERROR',
               'UserKnownHostsFile=' + target.known_hosts_file]
    argv = ['/usr/bin/ssh', '-F', '/dev/null', '-T', '-x', '-a', '-i', target.identity_file,
            '-p', str(target.port), '-l', target.username]
    for option in options:
        argv.extend(['-o', option])
    return argv + [target.hostname, 'probe-tools-v1' if include_tools else 'probe-v1']


_REMOTE_MAIN = '''
def main():
    token = os.environ.get('SSH_ORIGINAL_COMMAND', '')
    if token not in ('probe-v1', 'probe-tools-v1'):
        raise SystemExit(64)
    deadline = time.monotonic() + 12
    def check(argv):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            return _bounded_run(argv, timeout=min(2, remaining), max_output=8192).decode('utf-8', 'replace')
        except ProbeError:
            return None
    services = {}
    for ref, argv in SERVICE_COMMANDS.items():
        text = check(argv)
        services[ref] = service_state(text) if text is not None else 'unknown'
    try:
        disk = os.statvfs(BUILD_VOLUME)
        free = disk.f_bavail * disk.f_frsize
    except OSError:
        free = None
    try:
        load = list(os.getloadavg())
    except OSError:
        load = None
    tools = {}
    if token == 'probe-tools-v1':
        for name, argv in TOOL_COMMANDS.items():
            text = check(argv)
            tools[name] = text.strip()[:512] if text is not None else None
    print(json.dumps(dict(version=1, services=services, disk_free_bytes=free, load=load, tools=tools), allow_nan=False))

if __name__ == '__main__':
    main()
'''


def render_script(spec):
    """Return standalone source for trusted provisioning; no remote I/O."""
    adapter = linux if spec.platform == 'linux' else macos
    commands = {ref: adapter.service_command(target) for ref, target in spec.services.items()}
    return ('# Fixed runner probe. Run with trusted python3 -I.\n'
            'import json, os, re, selectors, signal, subprocess, time\n'
            'class ProbeError(RuntimeError): pass\n' + inspect.getsource(_bounded_run) + '\n'
            + inspect.getsource(adapter.service_state) + '\n'
            + 'SERVICE_COMMANDS = ' + repr(commands) + '\n'
            + 'BUILD_VOLUME = ' + repr(spec.build_volume) + '\n'
            + 'TOOL_COMMANDS = ' + repr({t: TOOLS[spec.platform][t] for t in spec.tools}) + '\n'
            + _REMOTE_MAIN)


def _decode(raw, spec, include_tools):
    try:
        data = json.loads(raw)
        if type(data) is not dict or set(data) != {'version', 'services', 'disk_free_bytes', 'load', 'tools'} or type(data['version']) is not int or data['version'] != 1:
            raise ValueError()
        if type(data['services']) is not dict or set(data['services']) != set(spec.services) or any(v not in ('running', 'stopped', 'failed', 'missing', 'unknown') for v in data['services'].values()):
            raise ValueError()
        disk = data['disk_free_bytes']
        if disk is not None and (type(disk) is not int or disk < 0):
            raise ValueError()
        load = data['load']
        if load is not None and (type(load) is not list or len(load) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in load)):
            raise ValueError()
        tools = data['tools']
        if type(tools) is not dict or set(tools) != (set(spec.tools) if include_tools else set()) or any(v is not None and (type(v) is not str or len(v) > 512 or any(ord(c) < 32 and c not in '\n\t' for c in v)) for v in tools.values()):
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise ProbeError('invalid_response') from None


class SSHProbe:
    """One instance per collector; serialize collect per host (caller-owned).

    collect returns version/services/disk_free_bytes/load/tools plus
    tools_age_seconds. Service dictionary is keyed by opaque service_ref. A
    ProbeError is management unavailable, NOT proof the host/runner is offline.
    None/unknown checks must never promote readiness. Cache is process-local;
    restarting the collector rechecks tools. No credentials/output in errors.
    """
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self._tools = {}

    def collect(self, target, spec, *, refresh_tools=False):
        key = (target, spec.platform, tuple(spec.services.items()), spec.build_volume, spec.tools)
        cached = self._tools.get(key)
        now = self.clock()
        include_tools = refresh_tools or cached is None or now - cached[0] >= TOOL_TTL
        raw = _bounded_run(ssh_argv(target, include_tools=include_tools), timeout=TOTAL_TIMEOUT, max_output=MAX_OUTPUT)
        data = _decode(raw, spec, include_tools)
        if include_tools:
            cached = (self.clock(), dict(data['tools']))
            self._tools[key] = cached
        data['tools'] = dict(cached[1])
        data['tools_age_seconds'] = max(0, self.clock() - cached[0])
        return data
