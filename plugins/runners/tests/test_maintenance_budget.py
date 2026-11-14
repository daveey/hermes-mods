"""Count actual transport attempts, not vends or logical scopes."""
import pytest
from plugins.runners.providers.base import Credential, HTTPResponse
from plugins.runners.providers.github_actions import _Failure
from plugins.runners.providers.maintenance import MaintenanceGitHub


def harness(transport=None):
    calls, durable = [], []
    def wire(*args, **kwargs):
        calls.append(args[1])
        if transport:
            return transport(*args, **kwargs)
        return HTTPResponse(200, {}, {'id': 1, 'full_name': 'example/repo'})
    api = MaintenanceGitHub(transport=wire, credential_resolver=lambda _: Credential('shared', 'token'), clock=lambda: 1000)
    api.state_sink = lambda state: durable.append(state)
    api.begin_cycle()
    return api, calls, durable


def test_maintenance_bounds_wire_attempts_and_pays_shared_debt_without_headers():
    api, calls, durable = harness()
    for i in range(32):
        api._get(Credential('shared', 'token'), f'/repos/example/r{i}', metadata=True)
    with pytest.raises(_Failure):
        api._get(Credential('shared', 'rotated-token'), '/repos/example/extra', metadata=True)
    assert len(calls) == 32
    assert api.export_state()['shared']['next_at'] == 1384
    assert durable[-1]['shared']['next_at'] == 1384


def test_maintenance_does_not_borrow_over_existing_collector_debt():
    api, calls, _ = harness()
    api.restore_state({'shared': {'next_at': 1031}})
    with pytest.raises(_Failure, match='pacing'):
        api._get(Credential('shared', 'token'), '/repos/example/repo', metadata=True)
    assert not calls


def test_network_attempt_is_charged_before_io_and_survives_restart():
    seen = []
    def failed(*a, **kw):
        seen.append(api.export_state()['shared']['next_at'])
        raise OSError('offline')
    api, calls, durable = harness(failed)
    with pytest.raises(_Failure, match='network_error'):
        api._get(Credential('shared', 'token'), '/repos/example/repo', metadata=True)
    assert seen == [1012]
    assert durable[0]['shared']['next_at'] == 1012
    assert durable[0]['shared']['until'] > 1000
    other, _, _ = harness()
    other.restore_state(durable[-1])
    assert other.export_state()['shared']['next_at'] == 1012


def test_credential_isolation_and_fresh_authorization():
    api, calls, _ = harness(lambda *a, **kw: HTTPResponse(401, {}, {}))
    with pytest.raises(_Failure, match='unauthorized'):
        api._get(Credential('a', 'token'), '/repos/example/repo', metadata=True)
    with pytest.raises(_Failure, match='unauthorized'):
        api._get(Credential('b', 'token'), '/repos/example/repo', metadata=True)
    assert len(calls) == 2
    assert set(api.export_state()) == {'a', 'b'}
