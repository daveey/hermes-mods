"""Global, durable observe-only attempt cap; synthetic offline inputs."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_github import Harness, scope, response, runner
from plugins.runners.providers.github_actions import BudgetPersistenceError


def capped(replies, cap=2):
    h = Harness(replies, hourly_request_cap=cap)
    h.saved = []
    h.api.state_sink = lambda state: h.saved.append(deepcopy(state))
    return h


def test_global_cap_counts_different_credentials_and_restart():
    h = capped([response(), response()])
    assert h.api.enumerate(scope()).complete
    assert h.api.enumerate(scope(ref='cred_'+'2'*16)).complete
    assert not h.api.enumerate(scope(ref='cred_'+'3'*16)).complete
    assert len(h.calls) == 2
    h2 = capped([response()])
    h2.api.restore_state(h.saved[-1])
    assert not h2.api.enumerate(scope(ref='cred_'+'4'*16)).complete
    assert not h2.calls
    h2.now += 3600
    assert h2.api.enumerate(scope()).complete
    assert len(h2.calls) == 1


def test_pagination_precharged_before_wire_and_partial_preserved():
    h = capped([response([runner(i) for i in range(1, 101)], 101)], cap=1)
    original = h.api.transport
    def send(*args, **kwargs):
        assert h.saved[-1]['@global-hourly-attempts']['timestamps'] == [1000.0]
        return original(*args, **kwargs)
    h.api.transport = send
    result = h.api.enumerate(scope())
    assert not result.complete and len(result.rows) == 100
    assert len(h.calls) == 1


def test_network_failure_is_charged():
    h = capped([TimeoutError()], cap=1)
    assert not h.api.enumerate(scope()).complete
    h.now += 500
    assert not h.api.enumerate(scope()).complete
    assert len(h.calls) == 1


def test_concurrent_enumeration_cannot_overspend():
    h = capped([response() for _ in range(3)], cap=3)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: h.api.enumerate(scope()), range(30)))
    assert len(h.calls) == 3


def test_persistence_failure_and_missing_sink_fail_closed():
    h = Harness([response()], hourly_request_cap=1)
    with pytest.raises(BudgetPersistenceError): h.api.enumerate(scope())
    assert not h.calls
    def broken(_): raise OSError('disk unavailable')
    h.api.state_sink = broken
    with pytest.raises(BudgetPersistenceError): h.api.enumerate(scope())
    assert not h.calls


@pytest.mark.parametrize('value', [None, {}, {'timestamps':[float('nan')]}, {'timestamps':[-1]}, {'timestamps':[True]}])
def test_invalid_state_rejected(value):
    h = capped([])
    with pytest.raises(ValueError): h.api.restore_state({'@global-hourly-attempts': value})


def test_old_state_supported_and_no_secrets_persisted():
    h = capped([response()])
    h.api.restore_state({})
    assert h.api.enumerate(scope()).complete
    assert 'fictional-test-token' not in repr(h.saved)


def test_reserved_credential_key_rejected_without_network():
    from plugins.runners.providers.base import Credential
    h = capped([response()])
    h.api.credential_resolver = lambda _: Credential('@global-hourly-attempts', 'token')
    assert not h.api.enumerate(scope()).complete
    assert not h.calls


def test_maintenance_and_collector_share_global_attempts():
    from plugins.runners.providers.base import Credential, HTTPResponse
    from plugins.runners.providers.maintenance import MaintenanceGitHub
    from plugins.runners.providers.github_actions import _Failure
    h = capped([response()], cap=2)
    assert h.api.enumerate(scope()).complete
    calls = []
    maintenance = MaintenanceGitHub(
        transport=lambda *a, **kw: calls.append(a) or HTTPResponse(200, {}, {}),
        credential_resolver=lambda _: Credential('maintenance', 'token'),
        hourly_request_cap=2, clock=lambda: 1001)
    maintenance.restore_state(h.saved[-1])
    maintenance.state_sink = lambda state: h.saved.append(deepcopy(state))
    maintenance.begin_cycle()
    maintenance._get(Credential('maintenance', 'token'), '/repos/example/repo', metadata=True)
    with pytest.raises(_Failure, match='cycle_budget'):
        maintenance._get(Credential('other', 'token'), '/repos/example/next', metadata=True)
    assert len(calls) == 1
    restarted = capped([response()], cap=2)
    restarted.api.restore_state(h.saved[-1])
    assert not restarted.api.enumerate(scope()).complete
    assert not restarted.calls


def test_redirect_hop_is_separately_charged():
    from plugins.runners.providers.base import HTTPResponse
    h = capped([HTTPResponse(301, {'Location': 'https://api.github.com/repositories/42/actions/runners'}, {})], cap=1)
    assert not h.api.enumerate(scope()).complete
    assert len(h.calls) == 1
    assert len(h.saved[-1]['@global-hourly-attempts']['timestamps']) == 1
