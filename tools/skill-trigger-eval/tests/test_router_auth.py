"""The router uses the exported pool token, never the historical placeholder."""
import io
import json
from pathlib import Path
import sys
import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import router


def test_router_sends_exported_token(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'router-fixture-token')
    def open_request(req, **kw):
        assert req.get_header('X-api-key') == 'router-fixture-token'
        response = io.BytesIO(json.dumps({'content': []}).encode())
        response.headers = {}
        return response
    monkeypatch.setattr(router.urllib.request, 'urlopen', open_request)
    assert router.call('http://127.0.0.1:8791', {}) == ({'content': []}, '')


def test_router_missing_token_fails_before_network(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.setattr(router.urllib.request, 'urlopen', lambda *a, **kw: pytest.fail('network'))
    with pytest.raises(RuntimeError, match='hermes-pool-llm env'):
        router.call('http://127.0.0.1:8791', {})


def test_router_401_no_retry_or_key_echo(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'router-fixture-token')
    def unauthorized(*a, **kw):
        raise urllib.error.HTTPError('http://127.0.0.1:8791', 401, 'Unauthorized', {},
                                     io.BytesIO(b'router-fixture-token'))
    monkeypatch.setattr(router.urllib.request, 'urlopen', unauthorized)
    monkeypatch.setattr(router.time, 'sleep', lambda *a: pytest.fail('must not retry 401'))
    with pytest.raises(RuntimeError, match='hermes-pool-llm env') as exc:
        router.call('http://127.0.0.1:8791', {})
    assert 'router-fixture-token' not in str(exc.value)
