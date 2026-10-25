from plugins.runners.providers.base import Credential, HTTPResponse
from plugins.runners.providers.github_actions import GitHubActions


def test_budget_and_retry_after_survive_restart_without_tokens():
    now = [1000]
    def make(transport):
        return GitHubActions(transport=transport, credential_resolver=lambda ref: Credential('budget-example','fictional-secret'), clock=lambda:now[0], jitter=lambda:0, pace_requests=True)
    from test_collector import F
    original=make(lambda *args, **kwargs: HTTPResponse(429, {'retry-after':'300', 'x-ratelimit-remaining':'0', 'x-ratelimit-reset':'1400'}))
    assert original.enumerate(F['Scope']).error=='rate_limited'
    state=original.export_state()
    assert 'fictional-secret' not in str(state)
    calls=[]
    restored=make(lambda *args,**kwargs:calls.append(args))
    restored.restore_state(state)
    now[0]=1100
    assert restored.enumerate(F['Scope']).error=='rate_limited'
    assert not calls
