"""Lambda Cloud provider. `fixtures/lambda_instances.json` is a captured real GET /instances
response (2026-10, API v1) with ids, IPs, hostname, workspace, key names and Jupyter token scrubbed."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'lambda_instances.json').read_text())
INSTANCES = 'https://cloud.lambda.ai/api/v1/instances'


def fake(*pages):
    calls = []
    def request(method, url, **kw):
        calls.append((method, url, kw))
        return copy.deepcopy(pages[len(calls) - 1])
    return request, calls


def test_real_instances_response_maps_to_machine_row():
    from providers.lambdacloud import LambdaCollector
    request, calls = fake(FIXTURE)
    out = LambdaCollector(request=request).collect()
    assert out['provider'] == 'lambda' and out['status'] == 'ok' and out['error'] is None
    (row,) = out['machines']
    assert row['provider_id'] == '0920582c7ff041399e34823a0be62549'
    assert row['display_name'] == 'my-gpu-box' and row['region'] == 'us-east-1'
    assert row['status'] == 'running' and row['resource_kind'] == 'instance'
    assert row['hardware'] == '1 × A10 (24 GB PCIe); 30 vCPU; 200 GiB memory'
    assert row['hourly_rate'] == '1.29' and row['currency'] == 'USD' and row['rate_basis'] == 'reported'
    assert row['ssh'] == 'ssh ubuntu@198.51.100.7'
    assert row['console_url'] == 'https://cloud.lambda.ai/instances'
    assert row['started_at'] == '2026-10-20T18:56:05.398225+00:00'
    assert 'REDACTED' not in json.dumps(out)  # Jupyter token never copied into the row
    assert calls == [('GET', INSTANCES, {'params': {'page_size': '100'}})]


@pytest.mark.parametrize('raw,status,billed', [
    ('booting', 'starting', True), ('unhealthy', 'error', True), ('terminating', 'stopped', False),
    ('terminated', 'stopped', False), ('preempted', 'stopped', False), ('weird', 'unknown', False)])
def test_status_mapping_rate_and_ssh(raw, status, billed):
    from providers.lambdacloud import LambdaCollector
    page = copy.deepcopy(FIXTURE)
    page['data'][0]['status'] = raw
    (row,) = LambdaCollector(request=fake(page)[0]).collect()['machines']
    assert row['status'] == status
    assert row['hourly_rate'] == ('1.29' if billed else None)
    assert row['ssh'] is None  # only an active instance gets an ssh command
    assert '1.29' in row['rate_note']


def test_booting_without_ip_and_missing_price():
    from providers.lambdacloud import LambdaCollector
    page = copy.deepcopy(FIXTURE)
    inst = page['data'][0]
    inst.update(status='booting', ip=None, first_healthy=None, name=None)
    del inst['instance_type']['price_cents_per_hour']
    (row,) = LambdaCollector(request=fake(page)[0]).collect()['machines']
    assert row['ssh'] is None and row['started_at'] is None and row['display_name'] == inst['id']
    assert row['hourly_rate'] is None and row['rate_basis'] == 'unknown'


def test_pagination_follows_page_token():
    from providers.lambdacloud import LambdaCollector
    first, second = copy.deepcopy(FIXTURE), copy.deepcopy(FIXTURE)
    first['page_token'] = 'next-page'
    second['data'][0]['id'] = 'second'
    request, calls = fake(first, second)
    out = LambdaCollector(request=request).collect()
    assert [m['provider_id'] for m in out['machines']] == ['0920582c7ff041399e34823a0be62549', 'second']
    assert calls[1][2]['params'] == {'page_size': '100', 'page_token': 'next-page'}


def test_bad_rows_and_failed_list_are_sanitized():
    from providers.lambdacloud import LambdaCollector
    page = copy.deepcopy(FIXTURE)
    page['data'].append({'id': None, 'status': 'active'})
    out = LambdaCollector(request=fake(page)[0]).collect()
    assert out['status'] == 'partial' and len(out['machines']) == 1
    assert out['error'] == 'Lambda row schema unavailable'
    def boom(*a, **kw):
        raise RuntimeError('body with secret-test')
    out = LambdaCollector(request=boom).collect()
    assert out['status'] == 'error' and out['machines'] == []
    assert 'secret-test' not in json.dumps(out)


def test_worker_module_differs_from_provider_id_and_broker_scope(monkeypatch, tmp_path):
    import providers.runpod as shared
    from providers.lambdacloud import LambdaCollector
    calls = []
    def run(argv, **kw):
        calls.append((argv, kw))
        return SimpleNamespace(returncode=0, stdout=json.dumps({'provider': 'lambda', 'machines': [], 'status': 'ok'}))
    monkeypatch.setattr(shared.subprocess, 'run', run)
    cfg = tmp_path / 'cr.json'
    cfg.write_text(json.dumps({'broker': {'command': ['token-broker', 'exec', '--scope', 'user/{provider}', '--']}}))
    monkeypatch.setenv('COMPUTE_RENTALS_CONFIG', str(cfg))
    collector = LambdaCollector()
    assert collector.collect() == collector.collect() and len(calls) == 1
    argv, kw = calls[0]
    assert argv[:4] == ['token-broker', 'exec', '--scope', 'user/lambda']
    assert argv[-3:] == ['-m', 'providers.lambdacloud', '--worker'] and kw['timeout'] == 70


def test_worker_without_key_is_unconfigured(monkeypatch):
    import runpy
    import io
    import contextlib
    monkeypatch.delenv('LAMBDA_API_KEY', raising=False)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        runpy.run_module('providers.lambdacloud', run_name='__main__')
    out = json.loads(buf.getvalue())
    assert out['provider'] == 'lambda' and out['status'] == 'unconfigured'


def test_allowlist_rejects_launch_and_terminate_before_network(monkeypatch):
    from providers.runpod import ReadHTTP
    import httpx
    def forbidden(*a, **kw):
        raise AssertionError('Network client must not be created')
    monkeypatch.setattr(httpx, 'Client', forbidden)
    request = ReadHTTP('secret-test', {('GET', INSTANCES)})
    for url in ('https://cloud.lambda.ai/api/v1/instance-operations/launch',
                'https://cloud.lambda.ai/api/v1/instance-operations/terminate'):
        with pytest.raises(ValueError):
            request('POST', url, json={'instance_ids': ['x']})
    with pytest.raises(ValueError):
        request('GET', INSTANCES, json={'x': 1})
