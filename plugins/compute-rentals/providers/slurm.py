"""Internal scheduler inventory only. Never allocate or execute a workload."""
import importlib.util
import json
import re
import os
import selectors
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from .common import machine, now, result, spawn_argv

# Load the core's canonical identity validator without depending on sys.path.
_spec = importlib.util.spec_from_file_location('_internal_ownership', Path(__file__).resolve().parents[1] / 'ownership.py')
_ownership = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ownership)


def identity_key(provider, account, ident):
    return _ownership.validate_key(json.dumps([provider, account, 'internal', ident], ensure_ascii=False, separators=(',', ':')))


def utc_timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.year < 1970:
            return None
        return parsed.replace(tzinfo=timezone.utc).isoformat() if parsed.tzinfo is None else parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, AttributeError):
        return None


def expand_hosts(expression, limit=4096):
    """Bounded Slurm hostlist parser; no shell interpolation or controller RPC."""
    if expression in ('', '(null)', 'None', 'N/A'):
        return []
    if len(expression) > 16384 or not re.fullmatch(r'[A-Za-z0-9_.\-,\[\]]+', expression):
        raise ValueError('invalid hostlist')
    parts = re.split(r',(?![^\[]*\])', expression)
    output = []
    for part in parts:
        match = re.search(r'\[([^\[\]]+)\]', part)
        if not match:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', part):
                raise ValueError('invalid hostname')
            output.append(part)
        else:
            values = []
            for span in match[1].split(','):
                ends = span.split('-')
                if len(ends) > 2 or not all(x.isdigit() for x in ends):
                    raise ValueError('invalid range')
                lo, hi = int(ends[0]), int(ends[-1])
                if hi < lo or hi - lo + 1 > limit:
                    raise ValueError('hostlist cap')
                width = len(ends[0]) if ends[0].startswith('0') else 0
                values.extend(str(i).zfill(width) for i in range(lo, hi + 1))
            for value in values:
                output.extend(expand_hosts(part[:match.start()] + value + part[match.end():], limit))
                if len(output) > limit:
                    raise ValueError('hostlist cap')
        if len(output) > limit:
            raise ValueError('hostlist cap')
    return list(dict.fromkeys(output))


READ_COMMANDS = frozenset(('TZ=UTC scontrol show config', 'TZ=UTC scontrol show nodes --oneliner',
    "TZ=UTC squeue --array -h -o '%i|%T|%N|%C|%b|%S|%e'"))


def _slurm_config(config):
    if config is None:
        from .config import load_config
        config = load_config()['slurm']
    return dict(dict(hosts=[], user=None, control_host=None), **config)


def ssh_read(host, command, timeout=15, byte_limit=2_000_000, *, config=None, extra_commands=()):
    """Fixed inventory commands on configured hosts only; bound time and bytes, discard raw stderr.

    config is the `slurm` config section (hosts, user); default: loaded from
    compute-rentals.yaml. extra_commands lets extension providers allowlist
    their own fixed read-only commands (exact strings, never interpolated).
    """
    cfg = _slurm_config(config)
    if host not in cfg['hosts'] or (command not in READ_COMMANDS and command not in extra_commands):
        raise ValueError('inventory command not allowlisted')
    target = cfg['user'] + '@' + host if cfg.get('user') else host
    argv = ['ssh', '-n', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
            '-o', 'StrictHostKeyChecking=yes', target, command]
    proc = subprocess.Popen(spawn_argv(argv), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=False)
    data = bytearray()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise RuntimeError('inventory timeout')
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > byte_limit:
                    raise RuntimeError('inventory byte cap')
        if proc.wait(timeout=max(.01, deadline - time.monotonic())):
            raise RuntimeError('inventory SSH read failed')
        return data.decode('utf-8', errors='strict')
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()


class SlurmCollector:
    """config: the `slurm` section (hosts, user, control_host). An injected
    read(host, command) replaces SSH (tests); without one and without
    configured hosts the provider reports `unconfigured`."""

    def __init__(self, read=None, max_rows=10000, config=None):
        self.config = _slurm_config(config if config is not None or read is None else {})
        self.read = read or (lambda host, command: ssh_read(host, command, config=self.config))
        self.configured = read is not None or bool(self.config['hosts'])
        hosts = self.config['hosts']
        self.control_host = self.config.get('control_host') or (hosts[0] if hosts else 'controller')
        self.max_rows = max_rows

    def collect(self):
        if not self.configured:
            out = result('slurm', [], 'No Slurm hosts configured (slurm.hosts)', ['Slurm hosts not configured'], True)
            out['workloads'] = []
            return out
        rows, workloads, errors = [], [], []
        cluster = 'unknown'
        try:
            config = self.read(self.control_host, 'TZ=UTC scontrol show config')
            cluster = re.search(r'^ClusterName\s*=\s*([\w.-]+)\s*$', config, re.M).group(1)
            nodes = self.read(self.control_host, 'TZ=UTC scontrol show nodes --oneliner')
            for line in nodes.splitlines():
                if not line.strip():
                    continue
                if len(rows) >= self.max_rows:
                    raise ValueError('node row cap')
                fields = dict(re.findall(r'(\w+)=([^\s]+)', line))
                ident = fields['NodeName']
                if not re.fullmatch(r'[A-Za-z0-9_.-]+', ident):
                    raise ValueError('invalid node')
                raw = fields.get('State', 'UNKNOWN')
                status = ('error' if any(s in raw for s in ('DOWN', 'DRAIN', 'FAIL')) else
                          'running' if any(s in raw for s in ('ALLOCATED', 'MIXED')) else
                          'idle' if raw == 'IDLE' else 'unknown')
                rows.append(machine('slurm', ident, cluster, 'internal',
                    resource_kind='physical_node', billing_class='internal_prepaid',
                    hardware=f"{fields.get('Gres', 'unknown')}; {fields.get('CPUTot', '?')} CPUs; {fields.get('RealMemory', '?')} MiB",
                    status=status, currency=None, rate_note='Internal prepaid; no incremental rental charge',
                    state_detail='Scheduler state: ' + raw + '; not GPU occupancy'))
        except Exception:
            errors.append('Slurm cluster/node inventory read failed')
        if cluster != 'unknown':
            try:
                jobs = self.read(self.control_host, "TZ=UTC squeue --array -h -o '%i|%T|%N|%C|%b|%S|%e'")
                seen = set()
                for line in jobs.splitlines():
                    if not line.strip():
                        continue
                    if len(workloads) >= self.max_rows:
                        raise ValueError('job row cap')
                    ident, raw, hosts, cpus, gres, start, end = line.split('|')
                    if not re.fullmatch(r'\d+(?:_\d+)?(?:\+\d+)?', ident):
                        raise ValueError('invalid job ID')
                    if ident in seen:
                        continue
                    seen.add(ident)
                    allocated = raw in ('RUNNING', 'COMPLETING', 'SUSPENDED', 'RESIZING', 'STAGE_OUT')
                    parents = expand_hosts(hosts) if allocated else []
                    workloads.append(dict(key=identity_key('slurm', cluster, ident), provider='slurm', account=cluster,
                        provider_id=ident, resource_kind='allocation' if allocated else 'queued_job',
                        display_name='Slurm job ' + ident, status=raw.lower(),
                        parent_machine_keys=[identity_key('slurm', cluster, h) for h in parents],
                        hardware=f'{gres}; {cpus} requested CPUs', started_at=utc_timestamp(start) if allocated else None,
                        expires_at=utc_timestamp(end) if allocated else None, observed_at=now(),
                        state_detail='Scheduler state: ' + raw + '; allocation deadline, not host expiry; not GPU occupancy'))
            except Exception:
                errors.append('Slurm job inventory incomplete')
        out = result('slurm', rows, 'All nodes visible to Slurm cluster ' + cluster + '; current allocations and queued jobs', errors)
        out['workloads'] = workloads
        if errors and workloads:
            out['status'] = 'partial'
        return out
