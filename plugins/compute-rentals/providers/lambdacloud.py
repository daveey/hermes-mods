"""Lambda Cloud inventory (provider id `lambda`). Only the fixed instance-list read is permitted.

The module is `lambdacloud` because `lambda` is a Python keyword; the provider id, and so the
`{provider}` substitution in `broker.command` (e.g. scope `user/lambda`), stays `lambda`.
"""
import ipaddress
import json
import os
from .common import machine, money, result
from .runpod import BrokerCollector, ReadHTTP, iso, scalar

API = 'https://cloud.lambda.ai/api/v1'
INSTANCES = API + '/instances'
STATUS = {'active': 'running', 'booting': 'starting', 'unhealthy': 'error', 'terminating': 'stopped',
          'terminated': 'stopped', 'preempted': 'stopped'}
MAX_ROWS = 10000
MAX_PAGES = 200


class LambdaCollector(BrokerCollector):
    provider = 'lambda'
    module = 'lambdacloud'
    scope = 'Authenticated Lambda Cloud account; on-demand instances (1-Click Clusters not listed)'

    def __init__(self, request=None, config=None):
        super().__init__(config)
        self.request = request

    def instances(self, errors):
        rows, token, pages = [], None, 0
        while True:
            params = {'page_size': '100'}
            if token:
                params['page_token'] = token
            page = self.request('GET', INSTANCES, params=params)
            data = page.get('data') if isinstance(page, dict) else None
            if not isinstance(data, list):
                raise ValueError()
            rows.extend(data)
            pages += 1
            token = page.get('page_token')
            if not token or len(rows) >= MAX_ROWS:
                break
            if pages >= MAX_PAGES:
                errors.append('Lambda page cap reached')
                break
        if len(rows) > MAX_ROWS:
            errors.append('Lambda row cap reached')
        return rows[:MAX_ROWS]

    def collect(self):
        if self.request is None:
            return self.broker_collect()
        rows, errors = [], []
        try:
            for inst in self.instances(errors):
                try:
                    ident = inst['id']
                    if not isinstance(ident, str) or not ident:
                        raise ValueError()
                    raw = scalar(inst.get('status'))
                    status = STATUS.get(raw, 'unknown')
                    itype = inst.get('instance_type') or {}
                    specs = itype.get('specs') or {}
                    cents = itype.get('price_cents_per_hour')
                    quote = money(cents / 100) if isinstance(cents, (int, float)) and not isinstance(cents, bool) else None
                    rate = quote if status in ('running', 'starting', 'error') else None
                    ssh = None
                    try:
                        host = str(ipaddress.ip_address(inst.get('ip')))
                        if status == 'running':
                            ssh = 'ssh ubuntu@%s' % host
                    except (ValueError, TypeError):
                        pass
                    region = inst.get('region') or {}
                    rows.append(machine('lambda', ident, 'authenticated-account',
                        scalar(region.get('name') if isinstance(region, dict) else None, 'unknown'),
                        display_name=scalar(inst.get('name'), ident), status=status,
                        hardware='%s × %s; %s vCPU; %s GiB memory' % (
                            scalar(specs.get('gpus'), '?'),
                            scalar(itype.get('gpu_description') or itype.get('name')),
                            scalar(specs.get('vcpus'), '?'), scalar(specs.get('memory_gib'), '?')),
                        started_at=iso(inst.get('first_healthy')), hourly_rate=rate, currency='USD',
                        rate_basis='reported' if rate is not None else 'unknown',
                        rate_note='Instance type list price %s USD/hour (%s); filesystems billed separately; '
                                  'not full invoice' % (quote or 'unknown', scalar(itype.get('name'))),
                        state_detail='Lambda status: ' + raw, resource_kind='instance',
                        console_url='https://cloud.lambda.ai/instances', ssh=ssh))
                except Exception:
                    errors.append('Lambda row schema unavailable')
        except Exception:
            errors.append('Lambda instance list unavailable')
        return result(self.provider, rows, self.scope, errors)


def build_collector(config=None):
    return LambdaCollector(config=config).collect


if __name__ == '__main__':
    token = os.environ.get('LAMBDA_API_KEY')
    if not token:
        output = result('lambda', [], LambdaCollector.scope, ['Credential unavailable'], True)
    else:
        output = LambdaCollector(ReadHTTP(token, {('GET', INSTANCES)})).collect()
    print(json.dumps(output))
