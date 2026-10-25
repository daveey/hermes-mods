"""Request boundaries, not whole ticks, are the crash durability boundary."""
from copy import deepcopy
import json
import pytest
from plugins.runners.providers.base import Credential, HTTPResponse
from plugins.runners.providers.github_actions import GitHubActions
from plugins.runners.collector import Collector
from plugins.runners.store import Store
from test_collector import F, config


def adapter(transport):
    return GitHubActions(transport=transport,
        credential_resolver=lambda ref: Credential('shared-budget', 'fictional-secret'),
        clock=lambda: 1000, jitter=lambda: 0)


def test_inflight_reservation_and_retry_after_are_durable_before_tick_commit(tmp_path):
    path = tmp_path / 'state.sqlite'
    observed = []
    with Store(path) as store:
        def transport(*args, **kwargs):
            # An independent connection sees committed state while HTTP is in flight.
            with Store(path) as reader:
                state = json.loads(reader.connection.execute(
                    "SELECT payload FROM collector_cache WHERE key='provider_budget'").fetchone()[0])
            observed.append(state)
            return HTTPResponse(429, {'retry-after': '600'})
        original = adapter(transport)
        Collector(store, config(), original, clock=lambda: 1000)
        assert original.enumerate(F['Scope']).error == 'rate_limited'
        saved = json.loads(store.connection.execute(
            "SELECT payload FROM collector_cache WHERE key='provider_budget'").fetchone()[0])
    assert observed[0]['shared-budget']['until'] >= 1310
    assert saved['shared-budget']['until'] >= 1600
    calls = []
    restarted = adapter(lambda *a, **kw: calls.append(a))
    restarted.restore_state(saved)
    assert restarted.enumerate(F['Scope']).error == 'backoff'
    assert calls == []
    assert 'fictional-secret' not in json.dumps(saved)


def test_failed_budget_commit_prevents_network(tmp_path):
    calls = []
    original = adapter(lambda *a, **kw: calls.append(a))
    with Store(tmp_path / 'state.sqlite') as store:
        Collector(store, config(), original, clock=lambda: 1000)
        store.connection.execute('DROP TABLE collector_cache')
        with pytest.raises(RuntimeError, match='budget_persistence_failed'):
            original.enumerate(F['Scope'])
    assert calls == []


def test_interrupted_tick_keeps_inflight_fence(tmp_path):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()
    original = adapter(interrupted)
    with Store(tmp_path / 'state.sqlite') as store:
        collector = Collector(store, config(), original, clock=lambda: 1000)
        with pytest.raises(KeyboardInterrupt):
            collector.tick()
        state = json.loads(store.connection.execute(
            "SELECT payload FROM collector_cache WHERE key='provider_budget'").fetchone()[0])
    assert state['shared-budget']['until'] >= 1310


def test_success_clears_only_inflight_fence_not_header_pacing(tmp_path):
    original = adapter(lambda *a, **kw: HTTPResponse(200,
        {'x-ratelimit-remaining': '10', 'x-ratelimit-reset': '1100'},
        {'total_count': 0, 'runners': []}))
    original.pace_requests = True
    with Store(tmp_path / 'state.sqlite') as store:
        Collector(store, config(), original, clock=lambda: 1000)
        assert original.enumerate(F['Scope']).complete
        state = json.loads(store.connection.execute(
            "SELECT payload FROM collector_cache WHERE key='provider_budget'").fetchone()[0])
    assert state['shared-budget']['until'] == 0
    assert state['shared-budget']['remaining'] == 10
    assert state['shared-budget']['next_at'] == 1010
