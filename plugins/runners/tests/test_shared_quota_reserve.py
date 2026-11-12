"""A shared per-user GitHub bucket must keep headroom for every other consumer."""
from plugins.runners.providers.github_actions import GitHubActions
from plugins.runners.providers.base import Credential, HTTPResponse
from test_collector import F


def _adapter(remaining, calls, now, **kw):
    def transport(*args, **kwargs):
        calls.append(args)
        return HTTPResponse(200, {'x-ratelimit-remaining': str(remaining[0]), 'x-ratelimit-reset': '4600'},
                            {'total_count': 0, 'runners': []})
    return GitHubActions(transport=transport, credential_resolver=lambda _: Credential('shared', 'fake'),
                         clock=lambda: now[0], jitter=lambda: 0, pace_requests=True, pacing_window=30, **kw)


def test_reserve_stops_requests_before_shared_bucket_drains():
    calls, now, remaining = [], [1000], [2600]
    a = _adapter(remaining, calls, now, quota_reserve=2500)
    assert a.enumerate(F['Scope']).complete  # learns remaining=2600 from headers
    remaining[0] = 2500
    now[0] += 3600 / 100  # well past any pacing spacing
    assert a.enumerate(F['Scope']).complete  # header now reports 2500 = reserve
    now[0] += 60
    blocked = a.enumerate(F['Scope'])
    # Pacing already spreads nothing over the reserve ...
    assert not blocked.complete and blocked.error == 'budget_deferred'
    # ... and the reserve fence holds even without the pacing gate.
    a._budgets['shared'].next_at = 0
    blocked = a.enumerate(F['Scope'])
    assert not blocked.complete and blocked.error == 'rate_limited'
    assert blocked.retry_after_seconds == 4600 - now[0]
    assert len(calls) == 2


def test_hourly_cap_spaces_requests_even_with_a_full_bucket():
    calls, now, remaining = [], [1000], [4999]
    a = _adapter(remaining, calls, now, max_hourly_requests=300)
    for _ in range(30):
        a.begin_cycle()
        for _ in range(16):
            a.enumerate(F['Scope'])
        now[0] += 30
    # 15 minutes at <=300/h -> about 75 requests plus the 30 s pacing window, not 16 per tick.
    assert 70 <= len(calls) <= 80, len(calls)


def test_pacing_without_reserve_matches_previous_behaviour():
    calls, now, remaining = [], [1000], [100]
    a = _adapter(remaining, calls, now)
    a.begin_cycle()
    assert sum(a.enumerate(F['Scope']).complete for _ in range(10)) >= 1
    assert a.quota_reserve == 0 and a.max_hourly_requests is None


def test_invalid_options_are_refused():
    import pytest
    for kw in ({'quota_reserve': -1}, {'quota_reserve': 1.5}, {'max_hourly_requests': 0}):
        with pytest.raises(ValueError):
            _adapter([1], [], [0], **kw)
