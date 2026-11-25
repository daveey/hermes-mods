"""Exact private receipt custody; no production logger or source activation."""
import asyncio
import base64
import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from test_coordinator_observation import observed, graph, api, board


def receipts():
    name = '_panel_source_receipts_test'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'dashboard/source_receipts.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def identity():
    return dict(boot_id='fixture-boot', pid=123, process_birth_ticks=321, runtime_sha='1' * 40,
        mods_sha='2' * 40, core_sha='3' * 40, api_generation='fixture-api', serving_gateway='fixture-gateway', profile='default')


def read_log(path):
    paths = [path] + sorted(path.parent.glob(path.name + '.*'))
    return [json.loads(line) for p in paths if p.is_file() for line in p.read_text().splitlines()]


class MemoryLog:
    def __init__(self):
        self.events = []
    def record(self, event, **data):
        self.events.append(dict(schema=receipts().SCHEMA, event=event, **identity(),
                                window_id='memory-window', record_id=f'memory-{len(self.events)}', **data))
        return True


def model_record(log):
    m = receipts()
    bundle = {'model': {'revision': 'model-revision', 'sections': {'running': []}}, 'computed_seq': 1}
    p = dict(feed_epoch='feed', publication_id='publication', session='S', model_revision='model-revision',
             admission={'id': 'admission', 'required_scopes': {'fixture-source': 'scope'}},
             source_vector={'fixture-source': 'version'})
    log.record('model_stored', publication=p, **m.payload_fields(m.encode(bundle)))
    return p, bundle


def test_log_is_explicit_bounded_append_only_and_private(tmp_path):
    m, path = receipts(), tmp_path / 'private' / 'receipts.jsonl'
    assert not path.exists()
    log = m.ReceiptLog(path, identity=identity(), segment_bytes=1)
    try:
        for i in range(4):
            assert log.record('source_received', feed_epoch='fixture', input=i)
        assert log.flush()
    finally:
        assert log.close()
    rows = read_log(path)
    assert [r['input'] for r in rows if r['event'] == 'source_received'] == list(range(4))
    assert len({r['record_id'] for r in rows}) == 5
    assert rows[-1]['event'] == 'window_end' and rows[-1]['dropped_records'] == 0
    assert m.validate_records(rows, require_closed=True)['valid']
    assert all(r['thread_birth'] and r['thread_id'] and r['wall_ns'] and r['monotonic_ns'] for r in rows)
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in path.parent.iterdir())
    assert not log.thread.is_alive()
    with pytest.raises(ValueError, match='identity'):
        log.record('source_received', pid=999)


def test_blocked_sink_never_holds_recorder_and_overflow_invalidates_window(tmp_path, monkeypatch):
    m = receipts()
    entered, release = threading.Event(), threading.Event()
    original = m.ReceiptLog._open
    def held(self, index):
        entered.set(); assert release.wait(5)
        return original(self, index)
    monkeypatch.setattr(m.ReceiptLog, '_open', held)
    log = m.ReceiptLog(tmp_path / 'bounded.jsonl', identity=identity(), queue_bytes=2000)
    try:
        assert log.record('source_received', input='first')
        assert entered.wait(2)
        # Main caller remains able to record despite actual writer I/O blocking.
        accepted = [log.record('source_received', input='x' * 500) for _ in range(4)]
        assert not all(accepted) and log.dropped_records > 0 and not log.valid
    finally:
        release.set()
        assert not log.close()
    assert not log.thread.is_alive()


def test_writer_io_error_is_not_success(tmp_path, monkeypatch):
    m = receipts()
    def refused(*a):
        raise OSError('fixture failure')
    monkeypatch.setattr(m.ReceiptLog, '_open', refused)
    log = m.ReceiptLog(tmp_path / 'unwritten.jsonl', identity=identity())
    assert log.record('source_received', input='lost')
    assert not log.flush()
    assert log.write_errors == ['OSError'] and log.dropped_records == 1
    assert not log.close()


@pytest.mark.parametrize('auth,fail', [(True, False), (True, True), (False, False), ('true', False)])
def test_response_custody_requires_actual_authenticated_write(auth, fail):
    m, log = receipts(), MemoryLog()
    p, bundle = model_record(log)
    body = m.encode({'epoch': 'feed', 'bundles': {'S': bundle}})
    response = m.completed_response(body, log=log, publications=[p], authenticated=auth, request_id='server-request')
    assert len(log.events) == 1, 'construction incorrectly acknowledged a response'
    sent = []
    async def send(message):
        if fail and message['type'] == 'http.response.body':
            raise OSError('actual send failed')
        sent.append(message)
    async def receive():
        return {'type': 'http.disconnect'}
    if fail:
        with pytest.raises(OSError, match='actual send'):
            asyncio.run(response({'type': 'http'}, receive, send))
    else:
        asyncio.run(response({'type': 'http'}, receive, send))
    success = [r for r in log.events if r['event'] == 'catchup_success']
    if auth is True and not fail:
        assert len(success) == 1
        assert base64.b64decode(success[0]['payload_b64']) == sent[-1]['body'] == body
        assert success[0]['write_completed_ns']
        assert m.validate_records(log.events) == {'valid': True, 'errors': [], 'stored': 1, 'fulfilled': 1, 'server_emitted': 0, 'authenticated_catchup': 1, 'unmatched': []}
    else:
        assert not success and log.events[-1]['event'] == 'catchup_error'
        assert m.validate_records(log.events)['fulfilled'] == 0


def test_invalidation_and_callback_never_fulfill_stored_admission():
    m, log = receipts(), MemoryLog()
    p, bundle = model_record(log)
    log.record('emit_success', kind='invalidation_only', publications=[],
        **m.payload_fields(m.encode({'epoch': 'feed', 'bundles': {}, 'sessions': ['S']})))
    assert m.validate_records(log.events)['fulfilled'] == 0
    log.record('emit_success', kind='model', publications=[p],
        **m.payload_fields(m.encode({'epoch': 'feed', 'bundles': {'S': bundle}})))
    assert m.validate_records(log.events)['fulfilled'] == 0
    log.record('catchup_success', kind='model', publications=[p], authenticated=True,
        request_id='request', response_id='response', write_completed_ns=1,
        **m.payload_fields(m.encode({'epoch': 'feed', 'bundles': {'S': bundle}})))
    assert m.validate_records(log.events)['fulfilled'] == 1


@pytest.mark.parametrize('mutation', ['vector', 'payload', 'auth', 'write', 'hash'])
def test_validator_rejects_inexact_or_unwritten_inclusion(mutation):
    m, log = receipts(), MemoryLog()
    p, bundle = model_record(log)
    p = json.loads(json.dumps(p))
    event = dict(kind='model', publications=[p], authenticated=True, request_id='request',
        response_id='response', write_completed_ns=1,
        **m.payload_fields(m.encode({'epoch': 'feed', 'bundles': {'S': bundle}})))
    if mutation == 'vector':
        p['source_vector'] = {'different': 'version'}
    elif mutation == 'payload':
        event.update(m.payload_fields(m.encode({'epoch': 'feed', 'bundles': {'S': {'model': {'revision': 'different'}}}})))
    elif mutation == 'auth':
        event['authenticated'] = 'true'
    elif mutation == 'write':
        event['write_completed_ns'] = None
    else:
        event['payload_sha256'] = '0' * 64
    log.record('catchup_success', **event)
    result = m.validate_records(log.events)
    assert not result['valid'] and result['fulfilled'] == 0


@pytest.mark.parametrize('event', ['emit_success', 'catchup_success'])
@pytest.mark.parametrize('target', ['bundle', 'vector', 'admission', 'identity'])
@pytest.mark.parametrize('before,after', [(True, 1), (False, 0), (1, True), (0, False), (1, 1.0)])
def test_exact_inclusion_preserves_json_scalar_types(event, target, before, after):
    m, log = receipts(), MemoryLog()
    p, bundle = model_record(log)
    if target == 'bundle':
        bundle['type_probe'] = before
    elif target in ('vector', 'admission'):
        p['source_vector' if target == 'vector' else 'admission']['type_probe'] = before
    else:
        log.events[0]['pid'] = before
    log.events[0].update(m.payload_fields(m.encode(bundle)))
    emitted_p, emitted_bundle = json.loads(m.encode(p)), json.loads(m.encode(bundle))
    data = dict(kind='model', publications=[emitted_p], authenticated=True,
        request_id='request', response_id='response', write_completed_ns=1,
        callback_started_ns=1, callback_completed_ns=2, observation='server_emission')
    # Positive control uses precisely the same JSON scalar types and custody.
    data.update(m.payload_fields(m.encode({'epoch': 'feed', 'bundles': {'S': emitted_bundle}})))
    log.record(event, **data)
    if target == 'identity':
        log.events[-1]['pid'] = before
    positive = m.validate_records(log.events)
    assert positive['valid'] and positive['fulfilled'] == 1
    log.events.pop()
    if target == 'bundle':
        emitted_bundle['type_probe'] = after
    elif target in ('vector', 'admission'):
        emitted_p['source_vector' if target == 'vector' else 'admission']['type_probe'] = after
    data.update(m.payload_fields(m.encode({'epoch': 'feed', 'bundles': {'S': emitted_bundle}})))
    log.record(event, **data)
    if target == 'identity':
        log.events[-1]['pid'] = after
    negative = m.validate_records(log.events)
    assert not negative['valid'] and negative['fulfilled'] == 0


@pytest.mark.parametrize('mode', ['hosted', 'loopback'])
def test_exact_scoped_route_inclusion_uses_core_admission_and_write(observed, monkeypatch, tmp_path, mode):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from hermes_cli.plugin_host_http_auth import with_hosted_admission
    r, f, lane, c, remote, now = observed
    m, path = receipts(), tmp_path / 'route-receipts.jsonl'
    log = m.ReceiptLog(path, identity=identity())
    f.install_receipts(log)
    owner_api = sys.modules[type(r).__module__]
    monkeypatch.setattr(sys.modules[owner_api._FEED_KEY], 'PROCESS_FEED', f)
    monkeypatch.setattr(owner_api, '_request_profile', lambda: owner_api.LAUNCH_PROFILE)
    app = FastAPI()
    app.state.auth_required = True
    app.include_router(owner_api.router)
    try:
        # Real core IPC admission projects the trusted boolean into request state.
        # Neither body nor fake caller headers can create this trusted marker.
        for auth in (False, 'true'):
            with TestClient(with_hosted_admission(app, auth)) as client:
                denied = client.post('/changes', json={'sessions': ['S0'], 'authenticated': True},
                                     headers={'X-Authenticated': 'true'})
            assert denied.status_code == 401
        headers = {}
        serving_app = with_hosted_admission(app, True)
        if mode == 'loopback':
            from hermes_cli import web_server
            monkeypatch.setattr(web_server, '_SESSION_TOKEN', 'fixture-route-token')
            app.state.auth_required = False
            serving_app = app
            headers = {'X-Hermes-Session-Token': 'fixture-route-token'}
            with TestClient(app) as client:
                assert client.post('/changes', json={'sessions': ['S0']}).status_code == 401
                assert client.post('/changes', json={'sessions': ['S0']},
                                   headers={'X-Hermes-Session-Token': 'wrong-fixture-token'}).status_code == 401
        with TestClient(serving_app) as client:
            response = client.post('/changes', json={'sessions': ['S0']}, headers=headers)
        assert response.status_code == 200 and set(response.json()['bundles']) == {'S0'}
        assert log.flush()
        rows = read_log(path)
        success, = [row for row in rows if row['event'] == 'catchup_success']
        assert base64.b64decode(success['payload_b64']) == response.content
        assert [p['session'] for p in success['publications']] == ['S0']
        assert success['publications'][0]['admission']['id'] == f.bundles['S0']['board_admission'].id
        result = m.validate_records(rows)
        assert result['valid'] and result['stored'] == 2 and result['fulfilled'] == 1
        assert result['unmatched'][0][2] == 'S1', 'unrequested chat was falsely acknowledged'
    finally:
        assert log.close()


@pytest.mark.parametrize('invalidation', [False, True])
def test_real_post_transform_emission_is_separate_from_authenticated_catchup(observed, monkeypatch, tmp_path, invalidation):
    from hermes_cli import plugin_events
    r, f, lane, c, remote, now = observed
    owner_api = sys.modules[type(r).__module__]
    calls = []
    monkeypatch.setattr(plugin_events, 'broadcast_plugin_event', lambda plugin, event, body: calls.append(body))
    if invalidation:
        original = owner_api.push_payload
        monkeypatch.setattr(owner_api, 'push_payload', lambda body: original(body, max_bytes=100))
    f.emit = owner_api._emit
    m, path = receipts(), tmp_path / 'emit-receipts.jsonl'
    log = m.ReceiptLog(path, identity=identity())
    try:
        f.install_receipts(log)
        c.execute("UPDATE tasks SET title='callback source write' WHERE id=?", (remote,)); c.commit()
        lane._capture_one(r, lane.external, ('callback-write',))
        lane.service()
        for worker in list(lane.active.values()):
            worker.join(3)
        lane.service()
        assert log.flush()
        rows = read_log(path)
        callbacks = [row for row in rows if row['event'] == 'emit_success']
        assert callbacks and len(callbacks) == len(calls)
        assert [json.loads(base64.b64decode(row['payload_b64'])) for row in callbacks] == calls
        assert all(row['kind'] == ('invalidation_only' if invalidation else 'model') for row in callbacks)
        result = m.validate_records(rows)
        assert result['valid'] and result['authenticated_catchup'] == 0
        assert (result['server_emitted'] == 0) is invalidation
        if invalidation:
            assert all(not body['bundles'] and body['sessions'] for body in calls)
    finally:
        assert log.close()


def test_wire_packet_custody_remains_exact_after_later_publication(observed, tmp_path):
    r, f, lane, c, remote, now = observed
    m, path = receipts(), tmp_path / 'race-receipts.jsonl'
    log = m.ReceiptLog(path, identity=identity())
    try:
        f.install_receipts(log)
        wire, custody = f.changes_packet(['S0'])
        first, = custody
        first_value = first.publication
        c.execute("UPDATE tasks SET title='later exact model' WHERE id=?", (remote,)); c.commit()
        lane._capture_one(r, lane.external, ('newer-semantic-write',))
        lane.service()
        for worker in list(lane.active.values()):
            worker.join(3)
        lane.service()
        newer_wire, newer_custody = f.changes_packet(['S0'])
        assert wire != newer_wire and first.publication == first_value
        assert first.publication != newer_custody[0].publication
        # The old request remains bound to the bytes it already took, never the
        # latest mutable bundles. It cannot claim inclusion of the newer vector.
        response = m.completed_response(wire, log=log, publications=[first.publication],
                                        authenticated=True, request_id='old-scoped-request')
        async def send(message):
            pass
        async def receive():
            return {'type': 'http.disconnect'}
        asyncio.run(response({'type': 'http'}, receive, send))
        assert log.flush()
        result = m.validate_records(read_log(path))
        assert result['valid'] and result['fulfilled'] == 1 and len(result['unmatched']) >= 2
    finally:
        assert log.close()


def test_payload_and_dataclass_keys_are_detached_without_id_normalization():
    from test_snapshot_store import load_snapshot
    m, s = receipts(), load_snapshot()
    source = s.SourceKey('gateway', 'profile', 'board', 'local', '/fixture/board.db')
    vector = {source: s.SourceVersion('epoch', 2, 3)}
    value = m.detached(vector)
    assert value['entries'][0][0] == dict(type='SourceKey', gateway='gateway', profile='profile',
        board='board', producer='local', canonical_path='/fixture/board.db')
    with pytest.raises(TypeError):
        m.payload_fields(bytearray(b'not immutable'))
    with pytest.raises(TypeError):
        m.detached(object())


@pytest.mark.parametrize('failure', ['replaced_body', 'cancelled'])
def test_response_rewrite_or_cancel_cannot_log_constructed_payload_as_written(failure):
    m, log = receipts(), MemoryLog()
    p, bundle = model_record(log)
    body = m.encode({'epoch': 'feed', 'bundles': {'S': bundle}})
    response = m.completed_response(body, log=log, publications=[p], authenticated=True, request_id='request')
    if failure == 'replaced_body':
        response.body = b'{"different":"actual ASGI bytes"}'
    async def send(message):
        if failure == 'cancelled':
            raise asyncio.CancelledError()
    async def receive():
        return {'type': 'http.disconnect'}
    if failure == 'cancelled':
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(response({'type': 'http'}, receive, send))
    else:
        asyncio.run(response({'type': 'http'}, receive, send))
    assert log.events[-1]['event'] == 'catchup_error'
    assert m.validate_records(log.events)['fulfilled'] == 0


def test_closed_window_accounting_and_cli_never_hide_unmatched(tmp_path):
    import subprocess
    m, path = receipts(), tmp_path / 'audit.jsonl'
    log = m.ReceiptLog(path, identity=identity())
    model_record(log)
    assert log.close()
    records = read_log(path)
    assert m.validate_records(records, require_closed=True)['valid']
    assert not m.validate_records(records[:-1], require_closed=True)['valid']
    records[-1]['dropped_records'] = 1
    assert not m.validate_records(records, require_closed=True)['valid']
    run = subprocess.run([sys.executable, m.__file__, '--require-fulfilled', str(path)],
                         capture_output=True, text=True, timeout=10)
    result = json.loads(run.stdout)
    assert run.returncode == 1 and result['valid'] and len(result['unmatched']) == 1
    assert result['acceptance'] == 'not_evaluated'


@pytest.mark.parametrize('mutation', ['window', 'runtime', 'duplicate', 'invalid_collection'])
def test_validator_rejects_cross_window_runtime_or_malformed_inclusion(mutation):
    m, log = receipts(), MemoryLog()
    p, bundle = model_record(log)
    log.record('catchup_success', kind='model', publications=[p], authenticated=True,
        request_id='request', response_id='response', write_completed_ns=1,
        **m.payload_fields(m.encode({'epoch': 'feed', 'bundles': {'S': bundle}})))
    record = log.events[-1]
    if mutation == 'window':
        record['window_id'] = 'foreign-window'
    elif mutation == 'runtime':
        record['pid'] += 1
    elif mutation == 'duplicate':
        record['record_id'] = log.events[0]['record_id']
    else:
        record['publications'] = {'not': 'a collection'}
    result = m.validate_records(log.events)
    assert not result['valid'] and result['fulfilled'] == 0
