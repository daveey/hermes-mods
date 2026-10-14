import json
import os
import re
from pathlib import Path
from .common import HttpGet, machine, money, result, state, timestamp

COLUMNS = ['id', 'label', 'geolocation', 'country_code', 'actual_status', 'cur_state', 'intended_status',
           'next_state', 'gpu_name', 'num_gpus', 'cpu_name', 'cpu_cores_effective', 'start_date',
           'dph_total', 'storage_total_cost', 'ssh_host', 'ssh_port']


class VastCollector:
    def __init__(self, get=None, account='authenticated-key'):
        self.get, self.account = get, account

    def collect(self):
        get = self.get
        if get is None:
            token = os.getenv('VAST_API_KEY')
            if not token:
                try:
                    token = Path('~/.config/vastai/vast_api_key').expanduser().read_text().strip()
                except OSError:
                    pass
            if not token:
                return result('vast', [], 'authenticated Vast account', ['Vast credential unavailable'], True)
            get = HttpGet('https://console.vast.ai', token, interval=1.1)
        rows, errors, seen, ids = [], [], set(), set()
        account = self.account
        if account == 'authenticated-key':
            try:
                user_id = get('/api/v0/users/current/', {}).get('id')
                if user_id is not None:
                    account = str(user_id)
            except Exception:
                errors.append('Account identity unavailable')
        params = {'limit': 25, 'select_cols': json.dumps(COLUMNS), 'order_by': '[{"col":"id","dir":"asc"}]'}
        total = None
        try:
            for _ in range(1000):
                page = get('/api/v1/instances/', dict(params))
                if page.get('success') is False or not isinstance(page.get('instances'), list):
                    raise ValueError()
                total = page.get('total_instances', total)
                for item in page['instances']:
                    ident = str(item['id'])
                    ids.add(ident)
                    current = item.get('cur_state') or item.get('actual_status')
                    if current in ('terminated', 'destroyed', 'deleted'):
                        continue
                    stopped = current in ('stopped', 'stopping')
                    rate = money(item.get('storage_total_cost') if stopped else item.get('dph_total'))
                    host, port = item.get('ssh_host'), str(item.get('ssh_port', ''))
                    ssh = ('ssh -p %s root@%s' % (port, host)) if current == 'running' and host and re.fullmatch(r'[a-zA-Z0-9.-]+', host) and port.isdigit() else None
                    rows.append(machine('vast', ident, account, item.get('geolocation') or item.get('country_code') or '',
                        display_name=item.get('label'), status=state(current),
                        hardware='%s × %s; CPU %s (%s allocated cores)' % (item.get('num_gpus', '?'), item.get('gpu_name', '?'), item.get('cpu_name', '?'), item.get('cpu_cores_effective', '?')),
                        started_at=timestamp(item.get('start_date')), hourly_rate=rate, rate_basis='reported' if rate is not None else 'unknown',
                        rate_note='Stopped storage only' if stopped else 'Advertised GPU + disk hourly; discounts and transfer excluded',
                        state_detail={k: item.get(k) for k in ('actual_status', 'cur_state', 'intended_status', 'next_state')},
                        console_url='https://cloud.vast.ai/instances/', ssh=ssh))
                cursor = page.get('next_token')
                if not cursor:
                    break
                if cursor in seen:
                    errors.append('Repeated pagination cursor'); break
                seen.add(cursor)
                params['after_token'] = cursor
            else:
                errors.append('Pagination limit exceeded')
            if total is not None and len(ids) != total:
                errors.append('Declared count mismatch: %s received, %s declared' % (len(ids), total))
        except Exception:
            errors.append('Vast inventory read failed')
        return result('vast', rows, 'Authenticated Vast account; contracts including stopped storage', errors)
