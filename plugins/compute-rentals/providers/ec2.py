"""Bounded AWS CLI inventory with explicit service pagination and 5-minute cache."""
import copy
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from .common import command_json, machine, money, now, result, state, timestamp


def aws_read(profile, region, service, operation, args):
    env = os.environ.copy()
    env.update(AWS_MAX_ATTEMPTS='3', AWS_RETRY_MODE='standard', AWS_PAGER='')
    try:
        return command_json(['aws', '--profile', profile, '--region', region, '--output', 'json',
                             '--no-cli-pager', '--cli-connect-timeout', '8', '--cli-read-timeout', '20',
                             service, operation, '--no-paginate', *args], timeout=70, env=env)
    except Exception:
        raise RuntimeError('AWS read failed or timed out') from None


class EC2Collector:
    def __init__(self, aws=None, profiles=None, cache_seconds=300, workers=4):
        self.aws = aws or aws_read
        # Configured via ec2.profiles in compute-rentals.yaml (default: $AWS_PROFILE or 'default').
        self.profiles = list(profiles or [os.environ.get('AWS_PROFILE') or 'default'])
        self.cache_seconds, self.workers = cache_seconds, workers
        self._cached, self._at = None, 0
        self._lock = threading.Lock()
        self._types = {}

    def _pages(self, profile, region, service, operation, args):
        seen, cursor = set(), None
        for _ in range(1000):
            page = self.aws(profile, region, service, operation, args + (['--next-token', cursor] if cursor else []))
            yield page
            cursor = page.get('NextToken')
            if not cursor:
                return
            if cursor in seen:
                raise RuntimeError('repeated cursor')
            seen.add(cursor)
        raise RuntimeError('page limit')

    def _rate(self, profile, region, item):
        platform = item.get('PlatformDetails')
        os_name = {'Linux/UNIX': 'Linux', 'Windows': 'Windows', 'Red Hat Enterprise Linux': 'RHEL', 'SUSE Linux': 'SUSE'}.get(platform)
        tenancy = {'default': 'Shared', 'dedicated': 'Dedicated', 'host': 'Host'}.get(item.get('Placement', {}).get('Tenancy'))
        if not os_name or not tenancy or tenancy == 'Host':
            return None
        if item.get('State', {}).get('Name') == 'stopped':
            return '0'
        instance_type = item.get('InstanceType')
        if item.get('InstanceLifecycle') == 'spot':
            az = item.get('Placement', {}).get('AvailabilityZone')
            product = {'Linux': 'Linux/UNIX', 'Windows': 'Windows', 'SUSE': 'SUSE Linux', 'RHEL': 'Red Hat Enterprise Linux'}.get(os_name)
            if not az or tenancy != 'Shared':
                return None
            instant = now()
            matches = []
            for page in self._pages(profile, region, 'ec2', 'describe-spot-price-history',
                                    ['--instance-types', instance_type, '--availability-zone', az, '--product-descriptions', product,
                                     '--start-time', instant, '--end-time', instant]):
                matches.extend(p for p in page.get('SpotPriceHistory', []) if p.get('InstanceType') == instance_type and p.get('AvailabilityZone') == az and p.get('ProductDescription') == product)
            return money(max(matches, key=lambda p: p.get('Timestamp', '')).get('SpotPrice')) if matches else None
        filters = dict(instanceType=instance_type, regionCode=region, operatingSystem=os_name,
                       tenancy=tenancy, preInstalledSw='NA', capacitystatus='Used')
        prices = set()
        for page in self._pages(profile, 'us-east-1', 'pricing', 'get-products',
                ['--service-code', 'AmazonEC2', '--filters', json.dumps([{'Type':'TERM_MATCH','Field':k,'Value':v} for k,v in filters.items()]), '--max-results', '100']):
            for encoded in page.get('PriceList', []):
                product = json.loads(encoded)
                for term in product.get('terms', {}).get('OnDemand', {}).values():
                    for dimension in term.get('priceDimensions', {}).values():
                        if dimension.get('unit') == 'Hrs':
                            price = money(dimension.get('pricePerUnit', {}).get('USD'))
                            if price is not None:
                                prices.add(price)
        return next(iter(prices)) if len(prices) == 1 else None

    def _region(self, profile, account, region):
        rows, errors = [], []
        try:
            for page in self._pages(profile, region, 'ec2', 'describe-instances', ['--max-results', '1000']):
                for reservation in page['Reservations']:
                    for item in reservation['Instances']:
                        current = item.get('State', {}).get('Name')
                        if current in ('terminated', 'shutting-down'):
                            continue
                        ident, kind = item['InstanceId'], item.get('InstanceType', 'unknown')
                        key = (profile, region, kind)
                        type_info = self._types.get(key)
                        if type_info is None:
                            try:
                                type_info = self.aws(profile, region, 'ec2', 'describe-instance-types', ['--instance-types', kind])['InstanceTypes']
                                if not isinstance(type_info, list) or not type_info:
                                    raise ValueError('Missing instance type')
                                self._types[key] = type_info
                            except Exception:
                                errors.append('Instance type enrichment failed: ' + region)
                                type_info = []
                        hardware = kind
                        if type_info:
                            info = type_info[0]
                            hardware += '; %s vCPU; %s MiB RAM' % (info.get('VCpuInfo', {}).get('DefaultVCpus', '?'), info.get('MemoryInfo', {}).get('SizeInMiB', '?'))
                            for gpu in info.get('GpuInfo', {}).get('Gpus', []):
                                hardware += '; %s × %s' % (gpu.get('Count', '?'), gpu.get('Name', '?'))
                        try:
                            rate = self._rate(profile, region, item)
                        except Exception:
                            rate = None
                            errors.append('Pricing unavailable: ' + region + '/' + ident)
                        rows.append(machine('ec2', ident, account, region,
                            display_name=next((t['Value'] for t in item.get('Tags', []) if t.get('Key')=='Name'), None),
                            status=state(current), hardware=hardware, started_at=timestamp(item.get('LaunchTime')),
                            hourly_rate=rate, rate_basis='estimated' if rate is not None else 'unknown',
                            rate_note=('Stopped compute only' if current == 'stopped' else 'Current exact-AZ spot compute' if item.get('InstanceLifecycle')=='spot' else 'Public on-demand compute list price') + '; excludes EBS, IPv4, network, CPU credits, discounts, tax',
                            console_url='https://%s.console.aws.amazon.com/ec2/home?region=%s#InstanceDetails:instanceId=%s' % (region, region, ident)))
        except Exception:
            errors.append('Region inventory incomplete: ' + profile + '/' + region)
        return rows, errors

    def collect(self):
        with self._lock:
            if self._cached is not None and time.monotonic() - self._at < self.cache_seconds:
                return copy.deepcopy(self._cached)
            rows, errors, scopes, tasks = [], [], [], []
            for profile in self.profiles:
                try:
                    account = self.aws(profile, 'us-east-1', 'sts', 'get-caller-identity', [])['Account']
                    regions = self.aws(profile, 'us-east-1', 'ec2', 'describe-regions', [])['Regions']
                    names = sorted({r['RegionName'] for r in regions})
                    scopes.append('%s (%s): %s' % (profile, account, ','.join(names)))
                    tasks.extend((profile, account, region) for region in names)
                except Exception:
                    errors.append('Account discovery failed: ' + profile)
            successful_regions = 0
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = [pool.submit(self._region, *task) for task in tasks]
                for future in as_completed(futures):
                    region_rows, region_errors = future.result()
                    if not region_errors:
                        successful_regions += 1
                    rows.extend(region_rows); errors.extend(region_errors)
            rows.sort(key=lambda m: (m['account'], m['region'], m['provider_id']))
            self._cached = result('ec2', rows, '; '.join(scopes) + '; other profiles not scanned; cache %g seconds' % self.cache_seconds, sorted(set(errors)))
            if errors and successful_regions:
                self._cached['status'] = 'partial'
            self._at = time.monotonic()
            return copy.deepcopy(self._cached)
