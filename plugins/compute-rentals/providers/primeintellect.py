"""Prime Intellect credential-visible pods and personal/authorized-team sandboxes."""
import json
import os
from .common import machine, money, result
from .runpod import BrokerCollector, ReadHTTP, iso, scalar

BASE = 'https://api.primeintellect.ai/api/v1'
PATHS = ('/user/whoami', '/user/teams', '/pods/', '/sandbox')


def alias(obj, camel, snake):
    return obj.get(camel) if camel in obj else obj.get(snake)


class PrimeIntellectCollector(BrokerCollector):
    provider = 'primeintellect'
    scope = 'Credential-visible pods (API has no team selector); personal and authorized-team active sandboxes'

    def __init__(self, request=None, max_pages=10, config=None):
        super().__init__(config)
        self.request, self.max_pages = request, max_pages

    def collect(self):
        if self.request is None:
            return self.broker_collect()
        rows, errors, account = [], [], 'authenticated-account'
        def get(path, params):
            return self.request('GET', BASE + path, params=params)
        def pages(path, params, sandbox=False):
            seen, offset = set(), 0
            for page in range(1, self.max_pages + 1):
                query = dict(params, **({'page':page, 'per_page':100} if sandbox else {'offset':offset, 'limit':100}))
                data = get(path, query)
                items = data['sandboxes' if sandbox else 'data']
                if not isinstance(items, list) or len(items) > 10000:
                    raise ValueError()
                total = data.get('total' if sandbox else 'total_count')
                if not isinstance(total, int) or total < 0:
                    raise ValueError()
                new = []
                for item in items:
                    ident = item.get('id')
                    if not isinstance(ident, str) or not ident:
                        raise ValueError()
                    if ident not in seen:
                        seen.add(ident); new.append(item)
                yield new
                more = alias(data, 'hasNext', 'has_next') if sandbox else len(seen) < total
                if sandbox and not isinstance(more, bool):
                    raise ValueError()
                if not more:
                    if len(seen) != total:
                        errors.append('Prime pagination count mismatch')
                    return
                if not new:
                    errors.append('Prime pagination did not progress'); return
                offset += len(items)
            errors.append('Prime pagination cap reached')
        try:
            account = scalar(get('/user/whoami', {})['data']['id'])
        except Exception:
            errors.append('Prime identity unavailable')
        teams = []
        try:
            for batch in pages('/user/teams', {}):
                teams.extend(item['id'] for item in batch)
        except Exception:
            errors.append('Prime team visibility incomplete')
        try:
            for batch in pages('/pods/', {}):
                for item in batch:
                    raw = scalar(item.get('status'))
                    field = {'ACTIVE':'priceHr', 'STOPPED':'stoppedPriceHr', 'PROVISIONING':'provisioningPriceHr'}.get(raw)
                    value = item.get(field) if field else None
                    if field and value is None:
                        value = item.get('priceHr')
                    rate = money(value)
                    resources = item.get('resources') or {}
                    gpu = scalar(item.get('gpuName'))
                    count = '0' if gpu == 'CPU_NODE' else scalar(item.get('gpuCount'), '?')
                    rows.append(machine(self.provider, item['id'], scalar(item.get('teamId') or item.get('userId'), account), 'global',
                        display_name=scalar(item.get('name'), item['id']),
                        status={'ACTIVE':'running','STOPPED':'stopped','PROVISIONING':'starting','PENDING':'starting','ERROR':'error','TERMINATED':'terminated'}.get(raw,'unknown'),
                        hardware='%s × %s; %s vCPU; %s memory (provider units)' % (count,gpu,scalar(resources.get('vcpus'),'?'),scalar(resources.get('memory'),'?')),
                        hourly_rate=rate, currency='USD', rate_basis='reported' if rate is not None else 'unknown',
                        rate_note='Whole-pod state-specific reported price; additional network/storage not established',
                        state_detail=raw, resource_kind='pod', console_url='https://app.primeintellect.ai'))
        except Exception:
            errors.append('Prime pod inventory incomplete')
        sandbox_ids = set()
        for team in [None] + teams:
            try:
                params = {'is_active':'true'}
                if team is not None:
                    params['team_id'] = team
                else:
                    params['user_id'] = account
                for batch in pages('/sandbox', params, True):
                    for item in batch:
                        if item['id'] in sandbox_ids:
                            continue
                        sandbox_ids.add(item['id'])
                        raw = scalar(item.get('status'))
                        rows.append(machine(self.provider, item['id'], scalar(alias(item,'teamId','team_id') or alias(item,'userId','user_id'), team or account),
                            scalar(item.get('region'),'global'), display_name=scalar(item.get('name'),item['id']),
                            status={'running':'running','RUNNING':'running','pending':'starting','stopped':'stopped'}.get(raw,'unknown'),
                            hardware='%s vCPU; %s GiB memory; %s × %s' % (scalar(alias(item,'cpuCores','cpu_cores'),'?'),scalar(alias(item,'memoryGB','memory_gb'),'?'),scalar(alias(item,'gpuCount','gpu_count'),'?'),scalar(alias(item,'gpuType','gpu_type'))),
                            started_at=iso(alias(item,'startedAt','started_at')), currency='USD',
                            rate_note='Current sandbox tariff/usage applicability unknown; no fabricated rate or timeout expiry',
                            state_detail=raw, resource_kind='sandbox', console_url='https://app.primeintellect.ai'))
            except Exception:
                errors.append('Prime sandbox scope incomplete')
        # Pod and sandbox namespaces can overlap: retain both rather than false deduplication.
        out = result(self.provider, [], self.scope)
        out['machines'] = rows
        out['status'] = ('partial' if rows else 'error') if errors else 'ok'
        out['error'] = '; '.join(sorted(set(errors))) or None
        return out


def build_collector(config=None):
    return PrimeIntellectCollector(config=config).collect


if __name__ == '__main__':
    token = os.environ.get('PRIME_API_KEY')
    out = (PrimeIntellectCollector(ReadHTTP(token, {('GET', BASE + p) for p in PATHS})).collect()
           if token else result('primeintellect', [], PrimeIntellectCollector.scope, ['Credential unavailable'], True))
    print(json.dumps(out))
