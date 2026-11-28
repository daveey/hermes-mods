"""Messages SSE at the real HTTP boundary, with a gated fake upstream."""
import http.client
import http.server
import json
import threading
from types import SimpleNamespace as NS

import pytest
from _load import load

pl = load('pool_llm')


class Pool:
    def __init__(self):
        self.index = 0
        self.accounts = [NS(id='a', label='first'), NS(id='b', label='second')]
        self.rotated = []
        self.selected_models = []

    def entries(self):
        return self.accounts

    def select(self, *, model=None):
        assert model == 'claude-test'
        self.selected_models.append(model)
        return self.accounts[self.index]

    def mark_exhausted_and_rotate(self, **kw):
        self.rotated.append(kw)
        self.index += 1

    def try_refresh_matching(self, **kw):
        return self.accounts[0]


class Stream:
    def __init__(self, events):
        self.events = iter(events)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        event = next(self.events)
        if isinstance(event, Exception):
            raise event
        return NS(model_dump=lambda **kw: event)

    def close(self):
        self.closed = True


@pytest.fixture
def endpoint(monkeypatch):
    ready = threading.Event()
    servers = []
    original = http.server.ThreadingHTTPServer
    def factory(address, handler):
        srv = original(('127.0.0.1', 0), handler)
        servers.append(srv)
        ready.set()
        return srv
    monkeypatch.setattr(http.server, 'ThreadingHTTPServer', factory)
    monkeypatch.setattr(pl, 'resolve_model', lambda *a: 'claude-test')
    pool = Pool()
    monkeypatch.setattr(pl, 'load_pool', lambda p: pool)
    calls, streams, clients = [], [], []
    plans = []
    def client(entry):
        def create(**body):
            calls.append((entry.id, body))
            stream = Stream(plans[len(calls) - 1])
            streams.append(stream)
            return stream
        obj = NS(messages=NS(create=create), closed=False)
        def close():
            obj.closed = True
        obj.close = close
        clients.append(obj)
        return obj
    monkeypatch.setattr(pl, 'anthropic_client', client)
    thread = threading.Thread(target=pl.serve, daemon=True)
    thread.start()
    assert ready.wait(5)
    connections = []
    def request(path='/v1/messages', **extra):
        conn = http.client.HTTPConnection('127.0.0.1', servers[0].server_port, timeout=5)
        connections.append(conn)
        conn.request('POST', path, json.dumps({'model': 'claude-test', 'max_tokens': 64,
            'messages': [{'role': 'user', 'content': 'hello'}], 'stream': True, **extra}),
            {'Content-Type': 'application/json', 'x-api-key': 'http-fixture-token'})
        return conn.getresponse()
    yield NS(request=request, plans=plans, calls=calls, streams=streams, pool=pool, clients=clients)
    for conn in connections:
        conn.close()
    servers[0].shutdown()
    servers[0].server_close()
    thread.join(5)


def event(response):
    kind = response.readline().decode().strip().removeprefix('event: ')
    data = json.loads(response.readline().decode().removeprefix('data: '))
    assert response.readline() == b'\n'
    assert kind == data['type']
    return data


START = {'type': 'message_start', 'message': {'id': 'msg_fixture'}}
STOP = {'type': 'message_stop'}


def test_incremental_order_and_tool_names(endpoint):
    release = threading.Event()
    finished = threading.Event()
    tool = {'type': 'content_block_start', 'index': 0, 'content_block': {
        'type': 'tool_use', 'id': 'call_1', 'name': 'mcp__weather', 'input': {}}}
    def upstream():
        yield START
        assert release.wait(5)
        yield tool
        yield STOP
        finished.set()
    endpoint.plans.append(upstream())
    try:
        r = endpoint.request(tools=[{'name': 'weather', 'input_schema': {'type': 'object'}}])
        assert r.status == 200
        assert r.getheader('content-type') == 'text/event-stream'
        assert r.getheader('content-length') is None
        assert json.loads(r.getheader('x-hermes-pool-route'))['account'] == 'first'
        assert event(r) == START
        assert not finished.is_set()
        release.set()
        assert event(r)['content_block']['name'] == 'weather'
        assert event(r) == STOP
        assert r.read() == b''
        assert endpoint.calls[0][1]['stream'] is True
        assert endpoint.calls[0][1]['tools'][0]['name'] == 'mcp__weather'
        assert endpoint.streams[0].closed and endpoint.clients[0].closed
    finally:
        release.set()


def test_quota_before_first_event_rotates(endpoint):
    endpoint.plans.extend([[pl.UpstreamError(429, 'quota')], [START, STOP]])
    r = endpoint.request()
    assert r.status == 200
    assert event(r) == START
    assert event(r) == STOP
    assert r.read() == b''
    assert [c[0] for c in endpoint.calls] == ['a', 'b']
    assert len(endpoint.pool.rotated) == 1
    assert all(s.closed for s in endpoint.streams)
    # Selection and the quota bench are scoped to the request's model, never the whole account.
    assert endpoint.pool.selected_models and set(endpoint.pool.selected_models) == {endpoint.calls[0][1]['model']}
    assert endpoint.pool.rotated[0]['model'] == endpoint.calls[0][1]['model']


@pytest.mark.parametrize('error', [pl.UpstreamError(429, 'quota'), ValueError('broken event')])
def test_error_after_first_never_rotates(endpoint, error):
    endpoint.plans.append([START, error])
    r = endpoint.request()
    assert r.status == 200
    assert event(r) == START
    assert event(r)['type'] == 'error'
    assert r.read() == b''
    assert len(endpoint.calls) == 1
    assert not endpoint.pool.rotated
    assert endpoint.streams[0].closed


def test_nonstream_unchanged(endpoint, monkeypatch):
    seen = []
    def complete(body, **kw):
        seen.append(body)
        return {'id': 'legacy', 'content': []}
    monkeypatch.setattr(pl, 'anthropic_messages', complete)
    r = endpoint.request(stream=False)
    assert r.status == 200
    assert json.loads(r.read()) == {'id': 'legacy', 'content': []}
    assert seen[0]['stream'] is False
    assert not endpoint.calls


@pytest.mark.parametrize('path', ['/v1/responses', '/v1/chat/completions'])
def test_other_stream_routes_rejected(endpoint, path):
    r = endpoint.request(path)
    assert r.status == 400
    assert '/v1/messages' in json.loads(r.read())['error']['message']
    assert not endpoint.calls


def test_auth_before_first_event_refreshes(endpoint):
    endpoint.plans.extend([[pl.UpstreamError(401, 'expired')], [START, STOP]])
    r = endpoint.request()
    assert r.status == 200
    assert event(r) == START
    assert event(r) == STOP
    r.read()
    assert [c[0] for c in endpoint.calls] == ['a', 'a']
    assert not endpoint.pool.rotated


def test_all_quota_errors_are_json_before_headers(endpoint):
    endpoint.plans.extend([[pl.UpstreamError(429, 'quota')], [pl.UpstreamError(429, 'quota')]])
    r = endpoint.request()
    assert r.status == 429
    assert r.getheader('content-type') == 'application/json'
    assert json.loads(r.read())['error']['type'] == 'rate_limit_error'
    assert all(s.closed for s in endpoint.streams)
    assert all(c.closed for c in endpoint.clients)


@pytest.mark.parametrize('plan,status,message', [([], 502, 'before its first event'),
    ([pl.UpstreamError(403, 'forbidden')], 403, 'forbidden')])
def test_pre_event_failure_closes_without_rotation(endpoint, plan, status, message):
    endpoint.plans.append(plan)
    r = endpoint.request()
    assert r.status == status
    assert message in json.loads(r.read())['error']['message']
    assert len(endpoint.calls) == 1
    assert not endpoint.pool.rotated
    assert endpoint.streams[0].closed and endpoint.clients[0].closed
