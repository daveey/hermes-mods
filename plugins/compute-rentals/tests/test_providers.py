import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

class VastTests(unittest.TestCase):
    def test_resolves_authenticated_account_without_exposing_user_key(self):
        from providers.vast import VastCollector
        def get(path, params):
            if path == '/api/v0/users/current/':  # live API 301s without the slash; redirects are refused
                return {'id':41908, 'api_key':'hidden-key'}
            return {'instances':[{'id':1}], 'total_instances':1}
        out = VastCollector(get=get).collect()
        self.assertEqual(out['machines'][0]['account'], '41908')
        self.assertNotIn('hidden-key', str(out))

    def test_pages_safe_fields_and_stopped_storage(self):
        from providers.vast import VastCollector
        calls = []
        def get(path, params):
            calls.append((path, params))
            return {'success': True, 'total_instances': 2, 'next_token': 'next' if len(calls)==1 else None,
                    'instances': [{'id': len(calls), 'actual_status': 'loading', 'cur_state': 'stopped',
                                   'dph_total': 9, 'storage_total_cost': .02, 'jupyter_token': 'SECRET'}]}
        out = VastCollector(get=get, account='41908').collect()
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(len(out['machines']), 2)
        self.assertEqual(out['machines'][0]['status'], 'stopped')
        self.assertEqual(out['machines'][0]['hourly_rate'], '0.02')
        self.assertNotIn('SECRET', str(out))
        self.assertEqual(calls[1][1]['after_token'], 'next')
        self.assertNotIn('jupyter_token', calls[0][1]['select_cols'])

class MarketplaceTests(unittest.TestCase):
    def test_paging_sku_join_unknown_paid_rate(self):
        from providers.sfcompute import MarketplaceCollector
        calls = []
        def get(path, params):
            calls.append((path, params))
            if '/skus/' in path:
                return {'properties': {'region': {'value': 'region1'}, 'num_accelerators': {'value': 8}, 'accelerator': {'value': 'H100'}}}
            second = params.get('starting_after') == 'c1'
            return {'data': [{'id': 'i2' if second else 'i1', 'state': 'running', 'workspace': {'id':'w1'}, 'sku': {'id':'s1'}}], 'has_more': not second, 'cursor': None if second else 'c1'}
        out = MarketplaceCollector(get=get).collect()
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(len(out['machines']), 2)
        self.assertIsNone(out['machines'][0]['hourly_rate'])
        self.assertEqual(out['machines'][0]['region'], 'region1')
        self.assertEqual(sum('/skus/' in p for p, _ in calls), 1)
        self.assertNotIn('workspace', calls[0][1])

class EC2Tests(unittest.TestCase):
    def test_on_demand_paginates_exact_os_tenancy_filters(self):
        from providers.ec2 import EC2Collector
        import json
        calls = []
        def aws(profile, region, service, operation, args):
            self.assertEqual((region, service, operation), ('us-east-1','pricing','get-products'))
            filters = {f['Field']:f['Value'] for f in json.loads(args[args.index('--filters')+1])}
            self.assertEqual(filters, {'instanceType':'m7i.large','regionCode':'eu-west-1','operatingSystem':'Windows','tenancy':'Dedicated','preInstalledSw':'NA','capacitystatus':'Used'})
            calls.append(args)
            if len(calls) == 1: return {'PriceList':[], 'NextToken':'next'}
            self.assertEqual(args[-2:], ['--next-token','next'])
            return {'PriceList':[json.dumps({'terms':{'OnDemand':{'term':{'priceDimensions':{'dim':{'unit':'Hrs','pricePerUnit':{'USD':'0.25'}}}}}}})]}
        rate = EC2Collector(aws=aws)._rate('p','eu-west-1',{'InstanceType':'m7i.large','PlatformDetails':'Windows','Placement':{'Tenancy':'dedicated'},'State':{'Name':'running'}})
        self.assertEqual(rate, '0.25')
        self.assertEqual(len(calls), 2)

    def test_ec2_repeated_cursor_keeps_rows_partial(self):
        from providers.ec2 import EC2Collector
        def aws(profile, region, service, operation, args):
            if operation == 'get-caller-identity': return {'Account':'a'}
            if operation == 'describe-regions': return {'Regions':[{'RegionName':'r'}]}
            if operation == 'describe-instance-types': return {'InstanceTypes':[]}
            return {'Reservations':[{'Instances':[{'InstanceId':'i','State':{'Name':'stopped'}}]}], 'NextToken':'repeat'}
        out = EC2Collector(aws=aws, profiles=['p']).collect()
        self.assertEqual(out['status'], 'partial')
        self.assertEqual(len(out['machines']), 1)

    def test_empty_successful_region_plus_failed_region_is_partial(self):
        from providers.ec2 import EC2Collector
        def aws(profile, region, service, operation, args):
            if operation == 'get-caller-identity': return {'Account':'a'}
            if operation == 'describe-regions': return {'Regions':[{'RegionName':'good'}, {'RegionName':'bad'}]}
            if region == 'good': return {'Reservations':[]}
            raise RuntimeError()
        out = EC2Collector(aws=aws, profiles=['p']).collect()
        self.assertEqual(out['status'], 'partial')
        self.assertEqual(out['machines'], [])

    def test_regions_paging_exact_spot_and_cache(self):
        from providers.ec2 import EC2Collector
        calls = []
        def aws(profile, region, service, operation, args):
            calls.append((profile, region, service, operation, args))
            if operation == 'get-caller-identity': return {'Account':'account'}
            if operation == 'describe-regions': return {'Regions':[{'RegionName':'us-east-1'}, {'RegionName':'us-east-2'}]}
            if operation == 'describe-instances':
                if region == 'us-east-2': raise RuntimeError('SECRET')
                if '--next-token' in args: return {'Reservations':[{'Instances':[{'InstanceId':'gone','State':{'Name':'terminated'}}]}]}
                return {'NextToken':'p2','Reservations':[{'Instances':[{'InstanceId':'i1','State':{'Name':'running'},'InstanceType':'c7a.32xlarge','InstanceLifecycle':'spot','PlatformDetails':'Linux/UNIX','Placement':{'AvailabilityZone':'us-east-1a','Tenancy':'default'}}]}]}
            if operation == 'describe-instance-types': return {'InstanceTypes':[]}
            if operation == 'describe-spot-price-history':
                self.assertIn('us-east-1a', args)
                return {'SpotPriceHistory':[{'InstanceType':'c7a.32xlarge','AvailabilityZone':'us-east-1b','ProductDescription':'Linux/UNIX','SpotPrice':'9','Timestamp':'2026-01-01'}, {'InstanceType':'c7a.32xlarge','AvailabilityZone':'us-east-1a','ProductDescription':'Linux/UNIX','SpotPrice':'1.234','Timestamp':'2026-01-01'}]}
            raise AssertionError(operation)
        collector = EC2Collector(aws=aws, profiles=['test'])
        out = collector.collect()
        self.assertEqual(out['status'], 'partial')
        self.assertEqual(len(out['machines']), 1)
        self.assertEqual(out['machines'][0]['hourly_rate'], '1.234')
        self.assertNotIn('SECRET', str(out))
        before = len(calls)
        self.assertEqual(collector.collect(), out)
        self.assertEqual(len(calls), before)

class TransportTests(unittest.TestCase):
    def test_http_retry_after_and_error_body_redaction(self):
        from providers.common import HttpGet
        from unittest.mock import patch
        from urllib.error import HTTPError
        import io
        error = HTTPError('https://example.test/read', 429, 'secret-message', {'Retry-After':'2'}, io.BytesIO(b'secret-body'))
        with patch('providers.common.urllib.request.urlopen', side_effect=error) as call, patch('providers.common.time.sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, '^HTTP 429$'):
                HttpGet('https://example.test', 'private-token')('/read', {'limit':25})
        self.assertEqual(call.call_count, 3)
        request = call.call_args[0][0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.full_url, 'https://example.test/read?limit=25')
        self.assertEqual(request.get_header('Authorization'), 'Bearer private-token')
        self.assertIn(((2.0,), {}), sleep.call_args_list)

class NormalizationTests(unittest.TestCase):
    def test_missing_labels_and_regions_still_satisfy_core_identity(self):
        from providers.common import machine
        row = machine('p', 'id', 'account', '', display_name=None)
        self.assertEqual(row['region'], 'unknown')
        self.assertEqual(row['display_name'], 'id')

class FactoryTests(unittest.TestCase):
    def test_factory_returns_persistent_cached_collectors(self):
        import tempfile
        from providers import build_collectors
        from providers.config import load_config
        with tempfile.TemporaryDirectory() as empty:
            config = load_config(Path(empty) / 'absent.yaml')
            config['extra_providers_dir'] = empty
            collectors = build_collectors(config)
        self.assertEqual(set(collectors), {'vast', 'sfcompute', 'ec2', 'runpod', 'lambda', 'modal', 'primeintellect', 'slurm'})
        self.assertTrue(all(callable(c) for c in collectors.values()))

class EdgeTests(unittest.TestCase):
    def test_vast_repeated_cursor_count_mismatch_and_dedupe(self):
        from providers.vast import VastCollector
        out = VastCollector(get=lambda *args: {'instances':[{'id':1}], 'total_instances':3, 'next_token':'repeat'}).collect()
        self.assertEqual(out['status'], 'partial')
        self.assertEqual(len(out['machines']), 1)
        self.assertIn('count mismatch', out['error'])
        self.assertIn('Repeated', out['error'])
        self.assertIsNone(out['machines'][0]['hourly_rate'])

    def test_marketplace_missing_cursor_is_partial_not_success(self):
        from providers.sfcompute import MarketplaceCollector
        out = MarketplaceCollector(get=lambda *args: {'data':[{'id':'1','state':'running'}], 'has_more':True}).collect()
        self.assertEqual(out['status'], 'partial')
        self.assertIn('cursor', out['error'])

    def test_unknown_ec2_os_never_assumes_linux_or_spot_fallback(self):
        from providers.ec2 import EC2Collector
        def fail(*args): raise AssertionError('No pricing request expected')
        collector = EC2Collector(aws=fail)
        self.assertIsNone(collector._rate('p', 'r', {'PlatformDetails':'Windows with SQL Server Enterprise'}))

if __name__ == '__main__': unittest.main()
