"""Actual default push ceiling and composed oversized-model/catch-up custody.

Uses real CoreReaders, coordinator, transformer, hosted admission and ASGI body
writes. Broadcast is fixture-owned server transport, not live/native delivery.
"""
import base64
import json
import sys

import pytest
from test_coordinator_observation import observed
from test_core_readers import api, board
from test_reader_batch import graph
from test_source_coordinator_core import drain
from test_source_receipts import receipts, identity, read_log


def test_real_default_push_boundary_one_byte_below_and_above(observed):
    r, f, lane, c, remote, now = observed
    api = sys.modules[type(r).__module__]
    payload = {'epoch': 'fixture', 'bundles': {'S': {'padding': ''}}}
    overhead = len(json.dumps({'feed': api.FEED_ID, 'profile': api.LAUNCH_PROFILE, **payload}))
    for delta in (-1, 1):
        payload['bundles']['S']['padding'] = 'x' * (api.PUSH_MAX_BYTES - overhead + delta)
        assert len(json.dumps({'feed': api.FEED_ID, 'profile': api.LAUNCH_PROFILE, **payload})) == api.PUSH_MAX_BYTES + delta
        body = api.push_payload(payload)  # no lowered max_bytes
        assert bool(body['bundles']) is (delta < 0)
        if delta > 0:
            assert body['sessions'] == ['S']


@pytest.mark.parametrize('outcome', ['success', 'emit_failure', 'write_failure', 'no_catchup', 'superseded'])
def test_composed_single_oversize_invalidation_needs_authenticated_written_catchup(observed, monkeypatch, tmp_path, outcome):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from hermes_cli.plugin_host_http_auth import with_hosted_admission
    from hermes_cli import plugin_events
    r, f, lane, c, remote, now = observed
    api = sys.modules[type(r).__module__]
    broadcasts = []
    # This is a payload-custody probe, not a CPU/I/O cost probe. A profile
    # callback on every character of the real sanitizer magnifies a giant title
    # into minutes; explicitly report cost UNAVAILABLE instead of inventing zero.
    monkeypatch.setattr(sys.modules[type(lane).__module__].StageCost, 'start',
                        lambda cost: setattr(cost, 'reason', 'oversize_fixture_cost_unmeasured'))

    def broadcast(plugin, event, body):
        if outcome == 'emit_failure':
            raise OSError('fixture server broadcast failed')
        broadcasts.append(body)

    monkeypatch.setattr(plugin_events, 'broadcast_plugin_event', broadcast)
    f.emit = api._emit
    m = receipts()
    path = tmp_path / 'actual-ceiling.jsonl'
    log = m.ReceiptLog(path, identity=identity(), queue_bytes=64*1024*1024,
                       max_record_bytes=32*1024*1024)
    f.install_receipts(log)
    monkeypatch.setattr(sys.modules[api._FEED_KEY], 'PROCESS_FEED', f)
    monkeypatch.setattr(api, '_request_profile', lambda: api.LAUNCH_PROFILE)
    app = FastAPI()
    app.state.auth_required = True
    app.include_router(api.router)
    try:
        c.execute('UPDATE tasks SET title=? WHERE id=?', ('Oversized ' + 'x'*600000, remote)); c.commit()
        drain(lane)
        wire, custody = f.changes_packet(['S0'])
        first, = custody
        assert len(wire) > api.PUSH_MAX_BYTES, 'fixture did not exceed real default ceiling'
        admission = f.bundles['S0']['board_admission']
        original_age = admission.first_admitted_mono_ns
        assert log.flush()
        rows = read_log(path)
        if outcome == 'emit_failure':
            assert any(e['event'] == 'emit_error' for e in rows)
        else:
            assert broadcasts and all(not b['bundles'] and b['sessions'] for b in broadcasts)
            assert any(e['event'] == 'emit_success' and e['kind'] == 'invalidation_only' for e in rows)
        assert m.validate_records(rows)['authenticated_catchup'] == 0
        assert m.validate_records(rows)['fulfilled'] == 0
        if outcome == 'no_catchup':
            return
        if outcome == 'superseded':
            c.execute('UPDATE tasks SET title=? WHERE id=?', ('Successor ' + 'y'*600000, remote)); c.commit()
            drain(lane)
            newer_wire, newer = f.changes_packet(['S0'])
            assert newer_wire != wire and newer[0].publication != first.publication
            assert f.bundles['S0']['board_admission'].first_admitted_mono_ns == original_age
        serving = with_hosted_admission(app, True)
        if outcome == 'write_failure':
            original = serving
            async def failing(scope, receive, send):
                async def write(message):
                    if message['type'] == 'http.response.body':
                        raise OSError('fixture response body write failed')
                    await send(message)
                await original(scope, receive, write)
            serving = failing
        response = None
        with TestClient(serving) as client:
            if outcome == 'write_failure':
                with pytest.raises(OSError, match='fixture response body'):
                    client.post('/changes', json={'sessions': ['S0']})
            else:
                response = client.post('/changes', json={'sessions': ['S0']})
                assert response.status_code == 200 and len(response.content) > api.PUSH_MAX_BYTES
        assert log.flush()
        rows = read_log(path)
        result = m.validate_records(rows)
        assert result['valid'], result
        if outcome == 'write_failure':
            assert result['fulfilled'] == 0 and any(e['event'] == 'catchup_error' for e in rows)
        else:
            assert response is not None
            success, = [e for e in rows if e['event'] == 'catchup_success']
            assert base64.b64decode(success['payload_b64']) == response.content
            assert success['publications'][0]['session'] == 'S0'
            assert result['authenticated_catchup'] == 1 and result['fulfilled'] == 1
            if outcome == 'superseded':
                assert success['publications'][0]['publication_id'] != first.publication['publication_id']
                assert [first.publication[k] for k in ('feed_epoch', 'publication_id', 'session', 'model_revision')] in result['unmatched']
    finally:
        assert log.close()
