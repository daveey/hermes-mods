"""Fictional, offline provider acceptance tests; never resolve real credentials."""
from copy import deepcopy
import pytest

from plugins.runners.providers.base import Credential, HTTPResponse
from plugins.runners.providers.github_actions import GitHubActions


def scope(kind='repository', name='example/widget', ref='cred_' + '1'*16):
    return dict(id='scope_' + '1'*16, provider='github_actions', endpoint='https://api.github.com',
                kind=kind, name=name, credential_ref=ref, permissions=[])


def runner(i=1, busy=False):
    return dict(id=i, name='fictional-worker', os='linux', status='online', busy=busy,
                labels=[dict(name='self-hosted')])


def response(rows=(), total=None, status=200, headers=None, key='runners'):
    return HTTPResponse(status, headers or {}, {'total_count': len(rows) if total is None else total, key: list(rows)})


class Harness:
    def __init__(self, replies, **options):
        self.calls, self.replies, self.now = [], list(replies), 1000.0
        self.resolutions = []
        def resolve(ref):
            self.resolutions.append(ref)
            return Credential(ref, 'fictional-test-token')
        self.api = GitHubActions(transport=self.send, credential_resolver=resolve,
                                 clock=lambda: self.now, jitter=lambda: .5, **options)
    def send(self, method, url, **kwargs):
        self.calls.append((method, url, deepcopy(kwargs)))
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_pagination_parity_and_contract_rows():
    from plugins.runners.models import validate
    h = Harness([response([runner(i) for i in range(1, 101)], 101), response([runner(101)], 101)])
    r = h.api.enumerate(scope())
    assert r.complete and r.error is None and r.total_count == len(r.rows) == 101
    assert r.observed_at == 1000
    assert all(validate('Runner', row) for row in r.rows)
    assert len(h.calls) == 2 and 'page=2' in h.calls[1][1]
    assert all(c[2]['allow_redirects'] is False for c in h.calls)
    assert all(row['host_id'] is None and row['readiness'] == 'not_checked' for row in r.rows)


@pytest.mark.parametrize('kind,name,path', [('repository','example/widget','repos/example/widget'),
    ('organization','example','orgs/example'), ('enterprise','example','enterprises/example')])
def test_explicit_scope_endpoint(kind,name,path):
    h=Harness([response()]); assert h.api.enumerate(scope(kind,name)).complete
    assert h.calls[0][1] == 'https://api.github.com/'+path+'/actions/runners?per_page=100&page=1'


@pytest.mark.parametrize('status', [401,403,404,429,500,502,503,302])
def test_http_fail_closed_without_secret_errors(status):
    h=Harness([HTTPResponse(status, {'Location':'https://evil.invalid'}, {'message':'fictional-test-token'})])
    r=h.api.enumerate(scope())
    assert not r.complete and not r.rows and r.error and r.total_count is None
    assert 'fictional-test-token' not in repr(r) and len(h.calls)==1


def test_network_failure_is_not_offline():
    h=Harness([OSError('fictional-test-token')]); r=h.api.enumerate(scope())
    assert not r.complete and r.error=='network_error' and not r.rows


@pytest.mark.parametrize('second', [response([runner(1)],101),response([],101),response([runner(101)],102),response(status=500)])
def test_partial_scope_keeps_coverage_never_claims_complete(second):
    h=Harness([response([runner(i) for i in range(1,101)],101),second])
    r=h.api.enumerate(scope())
    assert not r.complete and r.error and len(r.rows)==100 and r.total_count==101


def test_etag_per_page_and_mutation_isolation():
    h=Harness([response([runner(i) for i in range(1,101)],101,headers={'ETag':'"one"'}),
               response([runner(101)],101,headers={'ETag':'"two"'}),
               response(status=304),response(status=304)])
    first=h.api.enumerate(scope()); first.rows[0]['display_name']='changed'
    second=h.api.enumerate(scope())
    assert second.complete and second.rows[0]['display_name']=='fictional-worker'
    assert [c[2]['headers'].get('If-None-Match') for c in h.calls]==[None,None,'"one"','"two"']


def test_unbacked_304_fails_and_cache_not_reused_across_credentials():
    h=Harness([response([runner()],headers={'ETag':'"one"'}),response(status=304)])
    assert h.api.enumerate(scope()).complete
    assert not h.api.enumerate(scope(ref='cred_'+'2'*16)).complete
    assert 'If-None-Match' not in h.calls[-1][2]['headers']


def test_budget_from_actual_headers_including_304_and_shared_scopes():
    h=Harness([response([],headers={'ETag':'"empty"','X-RateLimit-Remaining':'1','X-RateLimit-Reset':'1100'}),
               response(status=304,headers={'X-RateLimit-Remaining':'0','X-RateLimit-Reset':'1100'}), response()])
    assert h.api.enumerate(scope()).complete
    assert h.api.enumerate(scope()).complete
    assert h.api.enumerate(scope(name='example/other')).error=='rate_limited'
    assert len(h.calls)==2
    h.now=1101
    assert h.api.enumerate(scope(name='example/other')).complete


def test_retry_after_backoff_jitter_no_sleep_or_retry_storm():
    h=Harness([response(status=429,headers={'Retry-After':'10'}),response(status=503),response()],backoff_base=2)
    assert not h.api.enumerate(scope()).complete
    h.now=1009; assert h.api.enumerate(scope()).error=='backoff'; assert len(h.calls)==1
    h.now=1011; assert not h.api.enumerate(scope()).complete
    h.now=1014; assert h.api.enumerate(scope()).error=='backoff'; assert len(h.calls)==2
    h.now=1018; assert h.api.enumerate(scope()).complete


@pytest.mark.parametrize('change', [{'endpoint':'https://evil.invalid'}, {'name':'../evil'}, {'name':'example/..'}, {'name':'example/x?x=y'}])
def test_no_arbitrary_urls(change):
    h=Harness([]); s=scope(); s.update(change)
    assert h.api.enumerate(s).error=='invalid_scope' and not h.calls and not h.resolutions


def test_bounded_enrichment_only_allowlist_and_busy_recent():
    h=Harness([response([dict(id=11)],key='workflow_runs'),
               response([dict(id=21,runner_id=1,status='in_progress',conclusion=None),
                         dict(id=22,runner_id=2,status='completed',conclusion='success')],key='jobs')])
    rows=[runner(1,True),runner(2),runner(3)]
    r=h.api.enrich_jobs(scope(), rows, allowed_repositories=['example/widget'], recent_runner_ids={'2'})
    assert r.complete and {row['runner_id'] for row in r.rows}=={'1','2'} and len(h.calls)==2
    assert all('/repos/example/widget/actions/' in c[1] for c in h.calls)
    assert h.api.enrich_jobs(scope(),rows,allowed_repositories=['example/widget']).error=='enrichment_cooldown'


def test_enrichment_no_candidates_no_calls_and_hard_budget():
    h=Harness([])
    assert h.api.enrich_jobs(scope(),[runner()],allowed_repositories=['example/widget']).complete
    assert not h.calls
    h=Harness([response([dict(id=11),dict(id=12)],key='workflow_runs'),response([],key='jobs')],max_job_requests=2)
    r=h.api.enrich_jobs(scope(),[runner(busy=True)],allowed_repositories=['example/widget'])
    assert not r.complete and r.error=='request_budget' and len(h.calls)==2


def test_no_runners_times_scopes_amplification():
    h=Harness([response([runner(i) for i in range(1,101)]) for _ in range(5)])
    for i in range(5):
        assert h.api.enumerate(scope(name=f'example/repo{i}')).complete
    assert len(h.calls)==5


@pytest.mark.parametrize('body', [None, {}, {'total_count': True, 'runners': []},
    {'total_count': -1, 'runners': []}, {'total_count': 1, 'runners': [dict(id=True)]},
    {'total_count': 0, 'runners': [runner()]}])
def test_invalid_payload_fails_closed(body):
    h=Harness([HTTPResponse(200,{},body)])
    r=h.api.enumerate(scope())
    assert not r.complete and r.error and not r.rows


def test_page_budget_is_partial_and_bounded():
    h=Harness([response([runner(i) for i in range(1,101)],101)],max_pages=1)
    r=h.api.enumerate(scope())
    assert r.error=='page_budget' and not r.complete and len(r.rows)==100 and len(h.calls)==1


def test_credential_aliases_share_budget_but_rotation_cannot_reuse_etag():
    h=Harness([response([],headers={'ETag':'"one"','X-RateLimit-Remaining':'0','X-RateLimit-Reset':'1100'}),
               response(status=304)])
    h.api.credential_resolver=lambda ref: Credential('installation-fictional', 'fictional-first')
    assert h.api.enumerate(scope()).complete
    assert h.api.enumerate(scope(ref='cred_'+'2'*16)).error=='rate_limited'
    h.now=1101
    h.api.credential_resolver=lambda ref: Credential('installation-fictional', 'fictional-second')
    assert h.api.enumerate(scope()).error=='unbacked_304'
    assert 'If-None-Match' not in h.calls[-1][2]['headers']


def test_http_date_retry_after_and_header_case():
    h=Harness([response(status=503,headers={'retry-after':'Thu, 01 Jan 1970 00:20:00 GMT'}),response()])
    assert h.api.enumerate(scope()).error=='server_error'
    h.now=1199; assert h.api.enumerate(scope()).error=='backoff'
    h.now=1200; assert h.api.enumerate(scope()).complete


def test_permission_failure_invalidates_etags():
    h=Harness([response(headers={'ETag':'"one"'}),response(status=403),response(status=304)])
    assert h.api.enumerate(scope()).complete
    assert h.api.enumerate(scope()).error=='forbidden'
    h.now+=10
    assert h.api.enumerate(scope()).error=='unbacked_304'
    assert 'If-None-Match' not in h.calls[-1][2]['headers']


@pytest.mark.parametrize('repos', [['foreign/widget'],['example/..'],['https://evil.invalid/x']])
def test_enrichment_allowlist_never_widens_scope(repos):
    h=Harness([])
    r=h.api.enrich_jobs(scope(),[runner(busy=True)],allowed_repositories=repos)
    assert r.error=='invalid_allowlist' and not h.calls and not h.resolutions


def test_enrichment_truncation_is_explicit_and_no_runner_fanout():
    h=Harness([response([dict(id=11)],total=1000,key='workflow_runs'),
               response([],key='jobs')],max_runs=1)
    r=h.api.enrich_jobs(scope(),[runner(i,True) for i in range(1,1001)],allowed_repositories=['example/widget'])
    assert not r.complete and r.error=='enrichment_truncated' and len(h.calls)==2


def test_enrichment_run_count_mismatch_is_not_complete():
    h=Harness([response([dict(id=11)],total=0,key='workflow_runs')])
    r=h.api.enrich_jobs(scope(),[runner(busy=True)],allowed_repositories=['example/widget'])
    assert not r.complete and r.error=='count_mismatch' and len(h.calls)==1


def test_resolver_failure_sanitized():
    h=Harness([])
    def fail(ref): raise RuntimeError('fictional-test-token')
    h.api.credential_resolver=fail
    r=h.api.enumerate(scope())
    assert r.error=='credential_unavailable' and 'fictional-test-token' not in repr(r) and not h.calls
