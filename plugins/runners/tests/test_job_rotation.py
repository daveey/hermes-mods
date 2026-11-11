"""Bounded multi-cycle job coverage, with fictional HTTP responses."""
from test_github import Harness, response, runner, scope


def run_page(ids):
    return response([dict(id=i) for i in ids], key='workflow_runs')


def job_page():
    return response([dict(id=21, runner_id=1, status='in_progress', conclusion=None)], key='jobs')


def collect(h, s=None):
    h.now += 61
    h.api.begin_cycle()
    return h.api.enrich_jobs(s or scope(), [runner(busy=True)], allowed_repositories=['example/widget'])


def job_paths(h):
    return [url.split('/runs/')[1].split('/')[0] for _, url, _ in h.calls if '/jobs?' in url]


def test_request_budget_rotates_past_newest_runs_and_wraps():
    h = Harness([item for _ in range(4) for item in (run_page([14, 13, 12]), job_page())], max_job_requests=2)
    results = [collect(h) for _ in range(4)]
    assert job_paths(h) == ['14', '13', '12', '14']
    assert len(h.calls) == 8
    assert all(r.error == 'request_budget' and not r.complete and r.rows for r in results)


def test_max_runs_rotates_with_new_arrivals_and_remains_incomplete():
    h = Harness([run_page([14, 13, 12]), job_page(), run_page([15, 14, 13, 12]), job_page()], max_runs=1)
    assert collect(h).error == 'enrichment_truncated'
    assert collect(h).error == 'enrichment_truncated'
    assert job_paths(h) == ['14', '13']


def test_failed_job_read_does_not_advance_cursor():
    h = Harness([run_page([14, 13]), job_page(), run_page([14, 13]), response(status=503),
                 run_page([14, 13]), job_page()], max_runs=1)
    collect(h)
    assert collect(h).error == 'server_error'
    collect(h)
    assert job_paths(h) == ['14', '13', '13']


def test_shared_cycle_budget_still_fences_rotated_reads():
    h = Harness([run_page([14, 13]), job_page(), run_page([14, 13]), job_page()], max_cycle_requests=2)
    assert collect(h).error == 'cycle_budget'
    assert collect(h).error == 'cycle_budget'
    assert len(h.calls) == 4 and job_paths(h) == ['14', '13']


def test_cursor_is_scope_local_and_completed_anchor_keeps_pending_work():
    h = Harness([run_page([14, 13]), job_page(), run_page([14, 13]), job_page(),
                 run_page([15, 13, 12]), job_page()], max_runs=1)
    collect(h)
    other = scope(); other['id'] = 'scope_' + '2' * 16
    collect(h, other)
    collect(h)
    assert job_paths(h) == ['14', '14', '13']


def test_cursor_query_and_credential_isolation_and_restart():
    from plugins.runners.providers.base import Credential
    h = Harness([item for _ in range(4) for item in (run_page([14, 13]), job_page())], max_runs=1)
    collect(h)
    h.now += 61
    h.api.enrich_jobs(scope(), [runner()], allowed_repositories=['example/widget'], recent_runner_ids=['1'])
    h.api.credential_resolver = lambda ref: Credential('other', 'fictional-test-token')
    collect(h)
    restarted = Harness([run_page([14, 13]), job_page()], max_runs=1)
    collect(restarted)
    assert job_paths(h) == ['14', '14', '14']
    assert job_paths(restarted) == ['14']


def test_duplicate_outside_read_window_fails_closed():
    h = Harness([run_page([14, 13, 13])], max_runs=1)
    result = collect(h)
    assert result.error == 'duplicate_id' and not result.rows and len(h.calls) == 1

