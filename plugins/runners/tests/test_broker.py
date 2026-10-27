"""Broker server over a real Unix socket in tmp; fake ports, fictional ids."""
import json
import os
import socket
import stat
import threading

import pytest

from plugins.runners import operations as ops
from plugins.runners.broker import server
from plugins.runners.tests.test_operations import (CANARY, HOST, REPAIR, SIBLING, FakeGitHub, FakeProcesses,
                                                   FakeSystemd, policy, request)


@pytest.fixture
def broker(tmp_path):
    systemd = FakeSystemd()
    pol = policy(clients=[dict(uid=os.getuid())])
    eng = ops.Engine(pol, systemd=systemd, github=FakeGitHub(), processes=FakeProcesses(),
                     locks=ops.FlockLocks(tmp_path / 'locks'))
    path = tmp_path / 'manage.sock'
    srv = server.ManagementServer(str(path), eng, socket_mode=0o660)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield path, systemd, srv
    srv.shutdown()
    thread.join(5)


def raw(path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(10)
        s.connect(str(path))
        s.sendall(payload)
        s.shutdown(socket.SHUT_WR)
        data = b''
        while chunk := s.recv(65536):
            data += chunk
    return json.loads(data)


def test_socket_is_private_to_owner_and_clients_group(broker):
    path, _, _ = broker
    assert stat.S_IMODE(path.stat().st_mode) == 0o660


def test_round_trip_uses_kernel_peer_credentials(broker):
    path, systemd, _ = broker
    client = ops.BrokerClient(str(path))
    result = client.request(request())
    assert result['ok'] is True, result
    assert systemd.started == ['gh-runner-canary@1.service']


def test_peer_uid_from_kernel_not_payload(tmp_path):
    eng = ops.Engine(policy(clients=[dict(uid=os.getuid() + 1)]), systemd=FakeSystemd(), github=FakeGitHub(),
                     processes=FakeProcesses(), locks=ops.FlockLocks(tmp_path / 'locks'))
    path = tmp_path / 'm.sock'
    srv = server.ManagementServer(str(path), eng)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        body = request('diagnose', peer_uid=os.getuid() + 1)
        assert raw(path, json.dumps(body).encode())['refusal'] in ('peer_not_authorized', 'unknown_field')
        assert raw(path, json.dumps(request('diagnose')).encode())['refusal'] == 'peer_not_authorized'
    finally:
        srv.shutdown()
        t.join(5)


def test_malformed_and_oversize_requests_refused(broker):
    path, systemd, _ = broker
    assert raw(path, b'not json')['refusal'] == 'invalid_request'
    assert raw(path, b'[1,2]')['refusal'] == 'invalid_request'
    assert raw(path, b'{"x":"' + b'a' * (server.MAX_REQUEST + 10) + b'"}')['refusal'] == 'request_too_large'
    assert systemd.started == []


def test_management_api_is_separate_from_claim_api(broker):
    path, systemd, _ = broker
    assert set(server.ROUTES) == {'management'}
    for api in ('claims', 'claim', 'admin', ''):
        body = request()
        body['api'] = api
        assert raw(path, json.dumps(body).encode())['refusal'] == 'unknown_api'
    assert systemd.started == []


def test_one_request_per_connection(broker):
    path, systemd, _ = broker
    two = json.dumps(request()).encode() + b'\n' + json.dumps(request(target=SIBLING, subject=SIBLING)).encode()
    assert raw(path, two)['refusal'] == 'invalid_request'
    assert systemd.started == []


def test_policy_file_must_be_root_owned_and_not_agent_writable(tmp_path):
    p = tmp_path / 'policy.json'
    p.write_text(json.dumps(dict(version=1, host_id=HOST, management_operations=False, clients=[dict(uid=1000)], targets=[])))
    os.chmod(p, 0o640)
    with pytest.raises(PermissionError):
        server.read_policy(p, require_root_owner=True)
    assert server.read_policy(p, require_root_owner=False)['management_operations'] is False
    os.chmod(p, 0o646)
    with pytest.raises(PermissionError):
        server.read_policy(p, require_root_owner=False)


# --- plugin-side management tool (repair chat entry point) --------------------

class RecordingClient:
    def __init__(self):
        self.sent = []

    def request(self, body):
        self.sent.append(body)
        return dict(ok=True, data=dict(operation=body['operation'], target_id=body['target_id']))


def config(flag=True):
    return dict(features=dict(observe=True, repair_sessions=True, management_operations=flag),
                bindings=[dict(runner_id='runner_' + '3' * 32, host_id=HOST, service_ref=CANARY),
                          dict(runner_id='runner_' + '4' * 32, host_id=HOST, service_ref=SIBLING)],
                hosts=[dict(id=HOST)])


def repair(state='active', entity='runner_' + '3' * 32):
    return dict(id=REPAIR, entity_id=entity, state=state)


def test_tool_refuses_when_plugin_flag_off_without_contacting_broker():
    client = RecordingClient()
    out = ops.operate_tool(dict(operation='service_start', target_id=CANARY, repair_id=REPAIR),
                           config=config(False), repair=repair(), client=client)
    assert out == dict(ok=False, refusal='management_operations_disabled', detail=out['detail'])
    assert client.sent == []


def test_tool_diagnose_allowed_with_flag_off_and_binds_repair_subject():
    client = RecordingClient()
    out = ops.operate_tool(dict(operation='diagnose', target_id=CANARY, repair_id=REPAIR),
                           config=config(False), repair=repair(), client=client)
    assert out['ok'] is True
    sent = client.sent[0]
    assert sent['repair'] == dict(id=REPAIR, subject_target_id=CANARY)
    assert sent['host_id'] == HOST and sent['api'] == 'management'


def test_tool_refuses_sibling_and_inactive_repair_and_extra_args():
    client = RecordingClient()
    assert ops.operate_tool(dict(operation='service_start', target_id=SIBLING, repair_id=REPAIR),
                            config=config(), repair=repair(), client=client)['refusal'] == 'sibling_target_refused'
    assert ops.operate_tool(dict(operation='service_start', target_id=CANARY, repair_id=REPAIR),
                            config=config(), repair=repair(state='closed'), client=client)['refusal'] == 'repair_not_active'
    assert ops.operate_tool(dict(operation='service_start', target_id=CANARY, repair_id=REPAIR, command='id'),
                            config=config(), repair=repair(), client=client)['refusal'] == 'unknown_field'
    assert ops.operate_tool(dict(operation='service_start', target_id=CANARY, repair_id='repair_' + '9' * 32),
                            config=config(), repair=repair(), client=client)['refusal'] == 'repair_mismatch'
    assert client.sent == []


def test_tool_reports_broker_unreachable_as_refusal(tmp_path):
    client = ops.BrokerClient(str(tmp_path / 'absent.sock'))
    out = ops.operate_tool(dict(operation='diagnose', target_id=CANARY, repair_id=REPAIR),
                           config=config(), repair=repair(), client=client)
    assert out['ok'] is False and out['refusal'] == 'broker_unavailable'
