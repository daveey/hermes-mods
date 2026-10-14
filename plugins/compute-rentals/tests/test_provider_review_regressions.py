"""Offline provider review regressions: synthetic credentials only."""
import sys
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@contextmanager
def server(handler):
    http = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:%s' % http.server_port
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


class QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass


class CacheReviewTests(unittest.TestCase):
    def test_failed_type_enrichment_retries_and_stays_partial_until_recovered(self):
        from providers.ec2 import EC2Collector
        attempts = []
        def aws(profile, region, service, operation, args):
            if operation == 'get-caller-identity': return {'Account': 'a'}
            if operation == 'describe-regions': return {'Regions': [{'RegionName': 'r'}]}
            if operation == 'describe-instances': return {'Reservations': [{'Instances': [{'InstanceId': 'i', 'InstanceType': 'm', 'State': {'Name': 'running'}}]}]}
            if operation == 'describe-instance-types':
                attempts.append(1)
                if len(attempts) < 3: raise RuntimeError('synthetic-secret')
                return {'InstanceTypes': [{'VCpuInfo': {'DefaultVCpus': 8}, 'MemoryInfo': {'SizeInMiB': 1024}}]}
            raise AssertionError(operation)
        collector = EC2Collector(aws=aws, profiles=['p'], cache_seconds=0)
        self.assertEqual(collector.collect()['status'], 'partial')
        self.assertEqual(collector.collect()['status'], 'partial')
        recovered = collector.collect()
        self.assertEqual(recovered['status'], 'ok')
        self.assertIn('8 vCPU', recovered['machines'][0]['hardware'])
        self.assertEqual(collector.collect()['status'], 'ok')
        self.assertEqual(len(attempts), 3)


class BillingReviewTests(unittest.TestCase):
    def test_marketplace_allowlisted_note_explains_creation_timestamp(self):
        from providers.sfcompute import MarketplaceCollector
        row = MarketplaceCollector(get=lambda *args: {'data': [{'id': 'i', 'created_at': 1700000000}], 'has_more': False}).collect()['machines'][0]
        self.assertIn('creation, not guaranteed last boot', row.get('state_detail', '') + row.get('rate_note', ''))


class TransportReviewTests(unittest.TestCase):
    def test_transport_is_local_without_mutating_global_urllib(self):
        import urllib.request
        from providers import common
        self.assertIsNot(common.urllib.request, urllib.request)
        self.assertIsNot(common.urllib.request.urlopen, urllib.request.urlopen)

    def test_actual_http_retry_after_and_redaction(self):
        from providers.common import HttpGet
        from unittest.mock import patch
        received = []
        class RateLimited(QuietHandler):
            def do_GET(self):
                received.append(self.headers.get('Authorization'))
                self.send_response(429)
                self.send_header('Retry-After', '0')
                self.end_headers()
                self.wfile.write(b'synthetic-secret-response')
        with server(RateLimited) as origin, patch('providers.common.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, '^HTTP 429$'):
                HttpGet(origin, 'synthetic-test-token')('/read')
        self.assertEqual(received, ['Bearer synthetic-test-token'] * 3)

    def test_actual_http_transport_rejects_oversized_body(self):
        from providers.common import HttpGet
        class Oversized(QuietHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                try:
                    self.wfile.write(b'"' + b'x' * (8 * 1024 * 1024) + b'"')
                except (BrokenPipeError, ConnectionResetError):
                    pass
        with server(Oversized) as origin:
            with self.assertRaisesRegex(RuntimeError, '^HTTP read failed$'):
                HttpGet(origin, 'synthetic-test-token')('/')

    def test_command_rejects_oversized_output(self):
        from providers.common import command_json
        with self.assertRaisesRegex(RuntimeError, '^read command failed or timed out$'):
            command_json([sys.executable, '-c', 'print(chr(34) + "x" * (8 * 1024 * 1024) + chr(34))'])

    def test_ec2_command_rejects_oversized_output(self):
        from providers.ec2 import aws_read
        from unittest.mock import patch
        import subprocess
        popen = subprocess.Popen
        def synthetic_process(argv, **kwargs):
            self.assertEqual(argv[0], 'aws')
            return popen([sys.executable, '-c', 'print(chr(34) + "x" * (8 * 1024 * 1024) + chr(34))'], **kwargs)
        with patch('subprocess.Popen', side_effect=synthetic_process):
            with self.assertRaisesRegex(RuntimeError, '^AWS read failed or timed out$'):
                aws_read('synthetic', 'test', 'ec2', 'describe-instances', [])

    def test_command_timeout_and_stderr_are_sanitized(self):
        from providers.common import command_json
        with self.assertRaisesRegex(RuntimeError, '^read command failed or timed out$'):
            command_json([sys.executable, '-c', 'import sys,time; sys.stderr.write("synthetic-secret"); time.sleep(10)'], timeout=.05)
        self.assertEqual(command_json([sys.executable, '-c', 'import sys; sys.stderr.write("x" * (9 * 1024 * 1024)); print("{}")']), {})

    def test_actual_transport_rejects_cross_origin_redirect(self):
        from providers.common import HttpGet
        received = []
        class Destination(QuietHandler):
            def do_GET(self):
                received.append(self.headers.get('Authorization'))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')
        with server(Destination) as target:
            class Redirect(QuietHandler):
                def do_GET(self):
                    self.send_response(302)
                    self.send_header('Location', target + '/capture')
                    self.end_headers()
            with server(Redirect) as origin:
                with self.assertRaisesRegex(RuntimeError, '^HTTP 302$'):
                    HttpGet(origin, 'synthetic-test-token')('/read')
        self.assertEqual(received, [])
