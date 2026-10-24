from plugins.runners.providers.github_actions import GitHubActions
from plugins.runners.providers.base import Credential, HTTPResponse
from test_collector import F


def test_cycle_request_budget_bounds_pagination_and_scopes():
    calls=[]
    def transport(*a,**kw):
        calls.append(a)
        return HTTPResponse(200,{},dict(total_count=0,runners=[]))
    g=GitHubActions(transport=transport,credential_resolver=lambda _:Credential('one','fake'),max_cycle_requests=2)
    g.begin_cycle()
    assert g.enumerate(F['Scope']).complete
    assert g.enumerate(F['Scope']).complete
    assert g.enumerate(F['Scope']).error=='cycle_budget'
    assert len(calls)==2
    g.begin_cycle()
    assert g.enumerate(F['Scope']).complete


def test_budget_paces_using_real_headers_not_assumed_conditional_free():
    clock=[1000]; calls=[]
    def transport(*a,**kw):
        calls.append(a)
        return HTTPResponse(200,{'X-RateLimit-Remaining':'100','X-RateLimit-Reset':'2000'},dict(total_count=0,runners=[]))
    g=GitHubActions(transport=transport,credential_resolver=lambda _:Credential('one','fake'),clock=lambda:clock[0],pace_requests=True)
    assert g.enumerate(F['Scope']).complete
    assert g.enumerate(F['Scope']).error=='budget_deferred'
    clock[0]+=10
    assert g.enumerate(F['Scope']).complete
    assert len(calls)==2
