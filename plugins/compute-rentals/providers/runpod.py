"""RunPod inventory. Only the fixed pod-list and identity read are permitted."""
import copy
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone
from .common import machine, money, result, spawn_argv



def iso(value):
    try:
        if isinstance(value, (float, int)):
            return datetime.fromtimestamp(value, timezone.utc).isoformat() if value > 0 else None
        if not isinstance(value, str):
            return None
        if value.endswith(' UTC'):
            dt = datetime.strptime(value, '%Y-%m-%d %H:%M:%S.%f %z UTC')
        else:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.astimezone(timezone.utc).isoformat() if dt.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None


def scalar(value, default='unknown'):
    return str(value)[:256] if isinstance(value, (str, int, float)) else default


class ReadHTTP:
    """Bounded allowlisted HTTP, no redirects or retries (429 is surfaced)."""
    def __init__(self, token, allowed, timeout=55):
        self.token, self.allowed = token, allowed
        self.deadline = time.monotonic() + timeout

    def __call__(self, method, url, **kwargs):
        import httpx
        if (method, url) not in self.allowed:
            raise ValueError('Read not allowed')
        if method == 'POST' and (url != 'https://api.runpod.io/graphql' or kwargs != {'json': {'query': 'query { myself { id } }'}}):
            raise ValueError('Only fixed identity query allowed')
        if method == 'GET' and set(kwargs) - {'params'}:
            raise ValueError('Only inventory query parameters allowed')
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Inventory deadline')
        with httpx.Client(timeout=min(20, remaining), follow_redirects=False,
                          headers={'Authorization': 'Bearer ' + self.token,
                                   'User-Agent': 'compute-inventory/1.0'}) as client:
            with client.stream(method, url, **kwargs) as response:
                if response.status_code != 200:
                    raise RuntimeError('HTTP read unavailable')
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 8_000_000 or time.monotonic() > self.deadline:
                        raise ValueError('Response limit')
                return json.loads(data)


class BrokerCollector:
    """Credentials live only in a worker subprocess; bounded run and 60-second cache.

    The worker is `<python> -m providers.<module or provider> --worker` (cwd = plugin dir),
    optionally wrapped by `broker.command` from compute-rentals.yaml, an argv
    prefix such as a secrets manager's `exec ... --` that injects the provider
    API key into the worker's environment ('{provider}' is substituted). With no
    wrapper the worker inherits this process's environment.
    """
    provider = ''
    module = ''  # worker module under providers/ when it differs from the provider id
    scope = ''

    def __init__(self, config=None):
        self._cached, self._at = None, 0
        self.config = config

    def worker_argv(self):
        from .config import load_config, worker_python
        cfg = self.config if self.config is not None else load_config()
        prefix = [part.replace('{provider}', self.provider) for part in (cfg.get('broker') or {}).get('command') or []]
        return prefix + [worker_python(cfg), '-m', 'providers.' + (self.module or self.provider), '--worker']

    def broker_collect(self):
        if self._cached is not None and time.monotonic() - self._at < 60:
            return copy.deepcopy(self._cached)
        try:
            # PYTHONPATH, not cwd=, finds providers/: a cwd forces fork() (see spawn_argv).
            plugin_dir = str(Path(__file__).resolve().parents[1])
            pythonpath = os.pathsep.join(p for p in (plugin_dir, os.environ.get('PYTHONPATH')) if p)
            proc = subprocess.run(spawn_argv(self.worker_argv()), env={**os.environ, 'PYTHONPATH': pythonpath},
                capture_output=True, text=True, timeout=70, check=False, close_fds=False)
            if proc.returncode or len(proc.stdout) > 8_000_000:
                raise ValueError()
            out = json.loads(proc.stdout)
            if out.get('provider') != self.provider or not isinstance(out.get('machines'), list):
                raise ValueError()
        except Exception:
            out = result(self.provider, [], self.scope, ['Credential/runtime inventory read unavailable'])
        self._cached, self._at = out, time.monotonic()
        return copy.deepcopy(out)


class RunPodCollector(BrokerCollector):
    provider = 'runpod'
    scope = 'Authenticated RunPod account; pods and serverless workers, including stopped resources'

    def __init__(self, request=None, config=None):
        super().__init__(config)
        self.request = request

    def collect(self):
        if self.request is None:
            return self.broker_collect()
        rows, errors, account = [], [], 'authenticated-account'
        try:
            identity = self.request('POST', 'https://api.runpod.io/graphql', json={'query': 'query { myself { id } }'})
            if identity.get('errors') or not identity['data']['myself']['id']:
                raise ValueError()
            account = scalar(identity['data']['myself']['id'])
        except Exception:
            errors.append('RunPod identity read unavailable')
        try:
            pods = self.request('GET', 'https://rest.runpod.io/v1/pods', params={
                'includeMachine':'true', 'includeWorkers':'true', 'includeSavingsPlans':'true'})
            if not isinstance(pods, list):
                raise ValueError()
            if len(pods) > 10000:
                errors.append('RunPod row cap reached')
            for pod in pods[:10000]:
                try:
                    ident = pod['id']
                    if not isinstance(ident, str) or not ident:
                        raise ValueError()
                    hw = pod.get('machine') or {}
                    raw = scalar(pod.get('desiredStatus'))
                    status = {'RUNNING':'running', 'EXITED':'stopped', 'STOPPED':'stopped', 'CREATED':'starting'}.get(raw, 'unknown')
                    quote = money(pod.get('adjustedCostPerHr') if pod.get('adjustedCostPerHr') is not None else pod.get('costPerHr'))
                    rate = quote if status == 'running' else None
                    ssh = None
                    try:
                        host = str(ipaddress.ip_address(pod.get('publicIp')))
                        port = int((pod.get('portMappings') or {}).get('22'))
                        if status == 'running' and 0 < port < 65536:
                            ssh = 'ssh -p %s %s' % (port, host)
                    except (ValueError, TypeError):
                        pass
                    rows.append(machine('runpod', ident, scalar(pod.get('consumerUserId'), account),
                        scalar(hw.get('dataCenterId') or hw.get('location'), 'global'),
                        display_name=scalar(pod.get('name'), ident), status=status,
                        hardware='%s × %s; %s vCPU; %s GiB memory' % (scalar(pod.get('gpuCount'), '?'), scalar(hw.get('gpuTypeId')), scalar(pod.get('vcpuCount'), '?'), scalar(pod.get('memoryInGb'), '?')),
                        started_at=iso(pod.get('lastStartedAt')), hourly_rate=rate, currency='credits',
                        rate_basis='reported' if rate is not None else 'unknown',
                        rate_note='Running quote %s credits/hour; stopped storage and independent volumes unknown; not full invoice' % (quote or 'unknown'),
                        state_detail='Desired status: ' + raw + '; not proof of readiness',
                        resource_kind='serverless_worker' if pod.get('endpointId') or pod.get('aiApiId') else 'pod',
                        console_url='https://www.console.runpod.io/pods', ssh=ssh))
                except Exception:
                    errors.append('RunPod row schema unavailable')
        except Exception:
            errors.append('RunPod pod list unavailable')
        return result(self.provider, rows, self.scope, errors)


def build_collector(config=None):
    return RunPodCollector(config=config).collect


if __name__ == '__main__':
    token = os.environ.get('RUNPOD_API_KEY')
    if not token:
        output = result('runpod', [], RunPodCollector.scope, ['Credential unavailable'], True)
    else:
        output = RunPodCollector(ReadHTTP(token, {('GET','https://rest.runpod.io/v1/pods'), ('POST','https://api.runpod.io/graphql')})).collect()
    print(json.dumps(output))
