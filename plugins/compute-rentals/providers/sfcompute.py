import os
from urllib.parse import quote
from .common import HttpGet, machine, result, state, timestamp


class MarketplaceCollector:
    def __init__(self, get=None):
        self.get = get

    def collect(self):
        get = self.get
        if get is None:
            token = os.getenv('SF_API_TOKEN') or os.getenv('SF_API_KEY')
            if not token:
                return result('sfcompute', [], 'All workspaces accessible to marketplace token', ['SF marketplace credential unavailable'], True)
            get = HttpGet('https://api.sfcompute.com', token, interval=.15)
        rows, errors, seen, skus = [], [], set(), {}
        params = {'limit': 200}
        try:
            for _ in range(1000):
                page = get('/v2/instances', dict(params))
                for item in page['data']:
                    if item.get('state') == 'terminated':
                        continue
                    sku = (item.get('sku') or {}).get('id')
                    if sku and sku not in skus:
                        try:
                            skus[sku] = get('/v2/skus/' + quote(sku, safe=''), {})
                        except Exception:
                            skus[sku] = {}; errors.append('SKU enrichment unavailable')
                    props = skus.get(sku, {}).get('properties') or {}
                    def prop(name):
                        return (props.get(name) or {}).get('value')
                    rows.append(machine('sfcompute', item['id'], (item.get('workspace') or {}).get('id', 'unknown'), prop('region') or '',
                        display_name=item.get('name'), status=state(item.get('state')),
                        hardware='%s × %s; %s vCPU' % (prop('num_accelerators') or '?', prop('accelerator') or sku or '?', prop('num_vcpus') or '?'),
                        started_at=timestamp(item.get('created_at')), start_note='Instance creation, not guaranteed last boot',
                        state_detail='Instance creation, not guaranteed last boot',
                        rate_note='Prepaid pool allocation; per-instance effective paid rate unavailable; pool schedule does not guarantee instance expiry',
                        console_url='https://sfcompute.com/dashboard'))
                if not page['has_more']:
                    break
                cursor = page.get('cursor')
                if not cursor or cursor in seen:
                    errors.append('Missing or repeated pagination cursor'); break
                seen.add(cursor)
                params['starting_after'] = cursor
            else:
                errors.append('Pagination limit exceeded')
        except Exception:
            errors.append('SF marketplace inventory read failed')
        return result('sfcompute', rows, 'All workspaces accessible to marketplace token; current instances, not allocation purchases', errors)
