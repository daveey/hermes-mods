"""Observe-only supervisor with optional status-only repair reconciliation.

No work is performed on import. repair_sessions permits diagnosis status reads,
not session dispatch; dispatch and workspace_jobs remain
refused. The owning profile's existing canonical state.db supplies receipts.

Private JSON: {"credentials": {"opaque_ref": {"scope": "user/example",
"env_var": "GH_TOKEN", "key": "shared-budget-key"}}, "probes": {
"opaque_ssh_ref": {"target": {"hostname": "example.com", "username": "probe",
"identity_file": "/private/probe", "known_hosts_file": "/private/known_hosts",
"port": 22}, "spec": {"platform": "linux", "services": {"opaque_service_ref":
"example.service"}, "build_volume": "/build", "tools": ["git", "python3"]}}}}

Use real contract-valid opaque refs. The JSON must be owned by this uid, regular,
non-symlink and mode 0600 (or stricter). It contains references, NEVER tokens.
Owner identity comes only from trusted supervisor arguments, not that file.
Config/runtime edits require an explicit supervised restart; no fence adoption.
"""
import argparse
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import signal
import stat
import sys
from threading import Event
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from plugins.runners.collector import Collector, OwnerLock
from plugins.runners.config import ConfigFile
from plugins.runners.store import Store
from plugins.runners.probes.ssh import SSHProbe, SSHTarget, ProbeSpec
from plugins.runners.providers.github_actions import GitHubActions
from plugins.runners.providers.runtime import BrokerCredential, BrokerCredentialResolver, GitHubHTTPTransport


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def load_runtime(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError()
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError()
        data = json.loads(raw, object_pairs_hook=_pairs)
        if not {'credentials', 'probes'} <= set(data) or set(data) - {'credentials', 'probes', 'broker_executable', 'job_repositories'}:
            raise ValueError()
        executable = data.get('broker_executable')
        if executable is not None and (type(executable) is not str or not Path(executable).is_absolute() or any(ord(c) < 32 for c in executable)):
            raise ValueError()
        credentials = {k: BrokerCredential(**v) for k, v in data['credentials'].items()}
        BrokerCredentialResolver(credentials)  # Validate without vending anything.
        jobs = data.get('job_repositories', {})
        if type(jobs) is not dict or any(type(v) is not list or len(v) > 10 or
                any(not GitHubActions._name(repo, 2) for repo in v) for v in jobs.values()):
            raise ValueError()
        for value in data['probes'].values():
            if set(value) != {'target', 'spec'}:
                raise ValueError()
            SSHTarget(**value['target'])
            ProbeSpec(**value['spec'])
        return data
    except Exception:
        raise RuntimeError('invalid_runtime_configuration') from None


class CoreUnavailable(RuntimeError):
    """Sanitized dependency diagnostic, safe for supervisor stderr."""


def configure_core(root):
    """Explicit supervisor-owned checkout; never infer from an ambient cwd.

    Append only after this plugin is loaded: core has a regular plugins package
    which would otherwise shadow the public mods namespace. Dependencies belong
    in the supervisor interpreter; this performs no installs or gateway startup.
    """
    path = Path(root)
    required = ('hermes_state.py', 'tui_gateway/session_start_requests.py', 'hermes_cli/profiles.py')
    if not path.is_absolute() or not all((path / name).is_file() for name in required):
        raise RuntimeError('repair_core_unavailable')
    path = path.resolve()
    if str(path) not in sys.path:
        sys.path.append(str(path))
    try:
        from tui_gateway import session_start_requests as core
        from hermes_state import SessionDB
        from hermes_cli.profiles import get_profile_dir
        if Path(core.__file__).resolve() != path / 'tui_gateway/session_start_requests.py':
            raise ValueError('conflicting_core')
        if 'metadata' not in core.StartIntent.__dataclass_fields__:
            raise ValueError('unsupported_core')
    except ImportError as exc:
        # Class and validated module name only; exception text can contain secrets.
        name = exc.name or 'unknown'
        if len(name) > 160 or not all(part.isidentifier() for part in name.split('.')):
            name = 'unknown'
        kind = 'ModuleNotFoundError' if isinstance(exc, ModuleNotFoundError) else 'ImportError'
        raise CoreUnavailable(f'repair_core_unavailable:{kind}:{name}') from None
    except Exception:
        raise RuntimeError('repair_core_unavailable') from None


def _readonly(config):
    # management_operations gates the separate-identity broker (operations.py), which
    # this process never imports or calls; the collector stays observe-only with it on.
    flags = config['features']
    if flags['observe'] is not True or type(flags['management_operations']) is not bool or any(
            flags[k] is not False for k in ('dispatch', 'workspace_jobs')):
        raise RuntimeError('observe_only_required')


def run(config_path, runtime_path, identity, state_dir, *, stop=None, once=False,
        interval=30, startup_delay=30, core_root=None):
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (interval, startup_delay)) or not 0 < interval <= 300 or not 0 <= startup_delay <= 300:
        raise ValueError('invalid_interval')
    stop = Event() if stop is None else stop
    source = ConfigFile(config_path)
    config = source.load()
    _readonly(config)
    if config['features']['repair_sessions'] and core_root is not None:
        configure_core(core_root)
    private = load_runtime(runtime_path)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with OwnerLock(state_dir / 'collector.lock', config, identity):
        credentials = {k: BrokerCredential(**v) for k, v in private['credentials'].items()}
        if any(s['credential_ref'] not in credentials for s in config['scopes']):
            raise RuntimeError('missing_credential_mapping')
        targets = {k: (SSHTarget(**v['target']), ProbeSpec(**v['spec'])) for k, v in private['probes'].items()}
        if any(h['ssh_target_ref'] is not None and h['ssh_target_ref'] not in targets for h in config['hosts']):
            raise RuntimeError('missing_probe_mapping')
        probe = SSHProbe()
        def host_probe(host, *, refresh_tools=False):
            target, spec = targets[host['ssh_target_ref']]
            if host['os'] != spec.platform or set(host['service_refs']) != set(spec.services):
                raise RuntimeError('probe_mapping_mismatch')
            facts = probe.collect(target, spec, refresh_tools=refresh_tools)
            facts['tools_observed_at'] = time.time() - facts['tools_age_seconds']
            return facts
        host_probe.refresh = lambda host: host_probe(host, refresh_tools=True)
        resolver = BrokerCredentialResolver(credentials,
            broker_command=(private.get('broker_executable', 'token-broker'),))
        adapter = GitHubActions(transport=GitHubHTTPTransport(),
                                credential_resolver=resolver, pace_requests=True, pacing_window=30)
        scopes = {s['id']: s for s in config['scopes']}
        jobs = private.get('job_repositories', {})
        for sid, repositories in jobs.items():
            if sid not in scopes:
                raise RuntimeError('invalid_runtime_configuration')
            scope = scopes[sid]
            if any((scope['kind'] == 'repository' and repo != scope['name']) or
                   (scope['kind'] == 'organization' and repo.split('/')[0] != scope['name'])
                   for repo in repositories):
                raise RuntimeError('invalid_runtime_configuration')
        with ExitStack() as stack:
            store = stack.enter_context(Store(state_dir / 'state.sqlite'))
            repairs = None
            if config['features']['repair_sessions']:
                from plugins.runners.service import Service
                from plugins.runners.repair_status import repair_reconciler
                repairs = stack.enter_context(repair_reconciler(
                    Service(state_dir / 'state.sqlite', config)))
                interval = min(interval, 30)
            repair_options = {'repair_service': repairs} if repairs is not None else {}
            collector = Collector(store, config, adapter, host_probe=host_probe,
                                  job_repositories=jobs, **repair_options)
            # Conservative restart pacing: repeated supervisor crashes must not
            # discard header budgets and immediately burst requests again.
            if stop.wait(startup_delay):
                return
            while not stop.is_set():
                current = source.load()  # FULL validation every tick, not stat-only.
                _readonly(current)
                if current != config or load_runtime(runtime_path) != private:
                    raise RuntimeError('configuration_changed')
                started = time.monotonic()
                collector.tick()  # Failure exits; never reuse speculative state.
                # Repair polling is start-to-start, not collection time + 30s.
                delay = max(0, interval - (time.monotonic() - started)) if repairs is not None else interval
                if once or stop.wait(delay):
                    return


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    for name in ('config', 'runtime', 'state-dir', 'owner-gateway-id', 'owner-profile'):
        result.add_argument('--' + name, required=True)
    result.add_argument('--owner-epoch', required=True, type=int)
    result.add_argument('--once', action='store_true')
    result.add_argument('--core-root', help='Absolute compatible core checkout for isolated supervisor interpreters')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    stop = Event()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        run(args.config, args.runtime, (args.owner_gateway_id, args.owner_profile, args.owner_epoch), args.state_dir, stop=stop, once=args.once, core_root=args.core_root)
        return 0
    except Exception as exc:
        # Never emit config, broker output, remote payloads, or arbitrary errors.
        reason = str(exc) if str(exc) in {'collector_running', 'owner_conflict', 'configuration_changed', 'observe_only_required', 'invalid_runtime_configuration', 'missing_credential_mapping', 'missing_probe_mapping', 'repair_core_unavailable'} else 'collector_failed'
        if isinstance(exc, CoreUnavailable):
            reason = str(exc)
        print(reason, file=sys.stderr)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(main())
