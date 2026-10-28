"""Workflow dispatch domain: fake GitHub/claims/inventory ports and fictional ids only.

No test reaches GitHub, token-broker, a real claim broker or builds anything locally.
"""
import hashlib
import io
import json
import threading
import zipfile

import pytest

from plugins.runners import jobs as J
from plugins.runners.models import runner_id as provider_runner_key

GATEWAY = '00000000-0000-4000-8000-0000000000aa'
SCOPE = 'scope_' + 'a' * 32
PROVIDER = 'provider_' + 'b' * 32
WF = 'workflow_' + 'c' * 32
WF_REF = 'wfref_' + 'd' * 32
TRUSTED = 'tref_' + 'e' * 32
SCHEMA_REF = 'schema_' + 'f' * 32
CLAIM_REF = 'claimauth_' + '1' * 32
INPUT = 'input_' + '2' * 32
CRED = 'cred_' + '3' * 32
SHA = '4' * 40
WSHA = '5' * 40
PRINCIPAL = 'oidc:example'
REPO = 'example/app'
NOW = 1_800_000_000.0


def config(**features):
    return dict(owner_gateway_id=GATEWAY, owner_profile='default', owner_epoch=1, config_revision=3,
                features=dict(dict(observe=True, repair_sessions=False, management_operations=False, dispatch=True,
                                   workspace_jobs=False), **features),
                scopes=[dict(id=SCOPE, provider='github_actions', endpoint='https://api.github.com', kind='repository',
                             name=REPO)],
                providers=[dict(id=PROVIDER, kind='github_actions')],
                allowed_workflows=[dict(id=WF, scope_id=SCOPE, workflow_ref=WF_REF, trusted_ref_ids=[TRUSTED],
                                        required_capability_ids=[], input_schema_ref=SCHEMA_REF,
                                        input_definitions=[dict(input_id=INPUT, label='Configuration', value_type='string',
                                                                required=False, allowed_values=['debug', 'release'])],
                                        dispatch_enabled=True, claim_authority_ref=CLAIM_REF)])


def runtime(**overrides):
    w = dict(repository=REPO, workflow_path='.github/workflows/canary-macos.yml', ref='refs/heads/main',
             credential_ref=CRED, runner_labels=['self-hosted', 'macOS', 'example-mac'], offline_policy='reject',
             canary=True, artifact_name_prefix='canary-', workflow_ref=WF_REF,
             trusted_refs={TRUSTED: 'refs/heads/main'}, inputs={INPUT: 'configuration'})
    w.update(overrides)
    return J.load_dispatch_runtime(dict(claims_socket='/run/example/intent.sock',
                                        claims_health_url='http://192.0.2.1:8467/v1/health',
                                        rate=dict(per_principal_per_hour=5), workflows={WF: w}))


def runner(n=1, **overrides):
    row = dict(id='runner_' + str(n) * 32, scope_id=SCOPE, labels=['self-hosted', 'macOS', 'ARM64', 'example-mac'],
               registration='online', freshness='fresh', activity='idle')
    row.update(overrides)
    return row


class FakeClaims:
    def __init__(self):
        self.healthy, self.intents, self.claim, self.proof, self.fail_register = True, {}, {}, [], False
        self.expiry, self.revoked, self.fail_revoke = {}, [], False
        self.now = lambda: NOW

    def health(self):
        if self.healthy is None:
            raise OSError('down')
        return self.healthy

    def register(self, **intent):
        if self.fail_register:
            raise OSError('down')
        self.intents[intent['request_id']] = intent
        self.expiry.setdefault(intent['request_id'], self.now() + 86400)
        return dict(ok=True)

    def status(self, request_id):
        intent = None
        if request_id in self.intents:
            intent = dict(self.intents[request_id], expires_at=self.expiry[request_id])
        return dict(ok=True, claim=self.claim.get(request_id), intent=intent)

    def revoke(self, request_id):
        if self.fail_revoke:
            raise OSError('down')
        if request_id in self.claim:
            return dict(ok=False, refusal='claim_exists')
        self.revoked.append(request_id)
        self.expiry[request_id] = self.now() - 1
        return dict(ok=True, revoked=True)

    def reachability(self):
        return dict(ok=True, workflows=self.proof)


class FakeGitHub:
    def __init__(self, domain_box):
        self.box = domain_box
        self.dispatches, self.runs, self.jobs, self.artifact_list, self.blobs = [], {}, {}, {}, {}
        self.attempts = {}
        self.mode, self.reachable, self.return_run_id = 'ok', 'reachable', True
        self.seen_state = []

    def source_reachable(self, cred, repo, ref, sha):
        assert cred == CRED and repo == REPO and ref == 'refs/heads/main'
        return self.reachable

    def dispatch(self, cred, repo, path, ref, inputs):
        domain, claims = self.box
        # Intent is durable and registered with the broker before GitHub sees anything.
        rid = inputs['request_id']
        assert rid in claims.intents and claims.intents[rid]['source_sha'] == inputs['source_sha']
        with domain._db() as db:
            self.seen_state.append(db.execute("SELECT state FROM jobs_domain_requests WHERE payload LIKE ?",
                                              ('%' + rid + '%',)).fetchone()[0])
        self.dispatches.append(dict(repo=repo, path=path, ref=ref, inputs=dict(inputs)))
        if self.mode == 'timeout':
            raise J.DispatchUnknown()
        if self.mode == 'ratelimit':
            raise J.DispatchRejected('provider_rate_limited', 30)
        if self.mode == '422':
            raise J.DispatchRejected('provider_rejected_422')
        run_id = 9000 + len(self.dispatches)
        self.runs[str(run_id)] = dict(id=run_id, run_attempt=1, display_title='canary [%s]' % rid, head_sha=WSHA,
                                      status='queued', conclusion=None)
        return dict(run_id=run_id if self.return_run_id else None)

    def find_runs(self, cred, repo, path, request_id):
        return [dict(id=r['id'], display_title=r['display_title']) for r in self.runs.values()]

    def run(self, cred, repo, run_id):
        return dict(self.runs[str(run_id)])

    def run_attempt(self, cred, repo, run_id, attempt):
        return dict(self.attempts[(str(run_id), int(attempt))])

    def run_jobs(self, cred, repo, run_id, attempt):
        return self.jobs.get((str(run_id), int(attempt)), [])

    def artifacts(self, cred, repo, run_id):
        return self.artifact_list.get(str(run_id), [])

    def download_artifact(self, cred, repo, artifact_id, max_bytes):
        return self.blobs[artifact_id]


class Env:
    def __init__(self, tmp_path, cfg=None, rt=None, runners=None, freshness='fresh'):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.clock = lambda: self.now
        self.now = NOW
        self.claims = FakeClaims()
        self.claims.now = self.clock
        self.box = [None, self.claims]
        self.github = FakeGitHub(self.box)
        self.inventory_rows = [runner()] if runners is None else runners
        self.freshness = freshness
        self.domain = J.JobDomain(db_path=tmp_path / 'state.sqlite', config=cfg or config(), runtime=rt or runtime(),
                                  github=self.github, claims=self.claims,
                                  inventory=lambda: dict(freshness=self.freshness, runners=self.inventory_rows),
                                  artifact_root=tmp_path / 'artifacts', clock=self.clock)
        self.box[0] = self.domain
        self.claims.proof = [dict(repository=REPO, workflow_path='.github/workflows/canary-macos.yml',
                                  last_claim_at=NOW - 3600)]

    def request(self, key='idem_' + '6' * 32, sha=SHA, inputs=None, owner=None):
        return dict(workflow_id=WF, source_sha=sha, inputs=inputs if inputs is not None else [],
                    idempotency_key=key, expected_owner=owner or self.domain.owner())


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def code_of(fn):
    with pytest.raises(J.JobError) as exc:
        fn()
    return exc.value.code, exc.value.reason_code


# ---------------------------------------------------------------- allowlist / gates

def test_workflows_lists_only_allowlisted_resolved_entries(tmp_path):
    e = Env(tmp_path)
    data = e.domain.workflows()['data']
    assert [w['id'] for w in data] == [WF] and data[0]['dispatch_enabled'] is True
    off = Env(tmp_path / 'off', cfg=config(dispatch=False))
    assert off.domain.workflows()['data'][0]['dispatch_enabled'] is False
    assert 'repository' not in json.dumps(data) and REPO not in json.dumps(data)


def test_dispatch_disabled_and_canary_mode_is_canary_only(tmp_path):
    e = Env(tmp_path, cfg=config(dispatch=False))
    assert code_of(lambda: e.domain.submit(e.request(), principal=PRINCIPAL)) == ('feature_disabled', 'dispatch_disabled')
    assert e.github.dispatches == []
    canary = e.domain.submit(e.request(), principal=PRINCIPAL, canary=True)
    assert canary['data']['status'] == 'queued' and len(e.github.dispatches) == 1
    plain = Env(tmp_path / 'plain', cfg=config(dispatch=False), rt=runtime(canary=False))
    assert code_of(lambda: plain.domain.submit(plain.request(), principal=PRINCIPAL, canary=True))[1] == 'canary_mode_requires_canary_workflow'


def test_unlisted_workflow_and_owner_mismatch(env):
    bad = dict(env.request(), workflow_id='workflow_' + '9' * 32)
    assert code_of(lambda: env.domain.submit(bad, principal=PRINCIPAL)) == ('not_found', 'workflow_not_allowlisted')
    stale_owner = dict(env.domain.owner(), config_revision=2)
    assert code_of(lambda: env.domain.submit(env.request(owner=stale_owner), principal=PRINCIPAL))[0] == 'owner_conflict'
    assert code_of(lambda: env.domain.submit(env.request(), principal=''))[0] == 'forbidden'
    assert env.github.dispatches == []


def test_broker_unreachable_keeps_dispatch_disabled(env):
    env.claims.healthy = None
    assert code_of(lambda: env.domain.submit(env.request(), principal=PRINCIPAL)) == ('dependency_unavailable', 'claim_broker_unreachable')
    env.claims.healthy = False
    assert code_of(lambda: env.domain.submit(env.request(), principal=PRINCIPAL))[1] == 'claim_broker_unreachable'
    assert env.github.dispatches == [] and env.claims.intents == {}


def test_runner_reachability_must_be_proven_except_for_canary(env):
    env.claims.proof = []
    assert code_of(lambda: env.domain.submit(env.request(), principal=PRINCIPAL))[1] == 'runner_claim_reachability_unverified'
    env.claims.proof = [dict(repository=REPO, workflow_path='.github/workflows/canary-macos.yml', last_claim_at=NOW - 30 * 86400)]
    assert code_of(lambda: env.domain.submit(env.request(), principal=PRINCIPAL))[1] == 'runner_claim_reachability_unverified'
    assert env.github.dispatches == []
    assert env.domain.submit(env.request(), principal=PRINCIPAL, canary=True)['data']['status'] == 'queued'


def test_all_eligible_runners_offline_rejects_without_fallback(tmp_path):
    e = Env(tmp_path, runners=[runner(registration='offline'), runner(2, labels=['self-hosted', 'Linux']),
                               runner(3, scope_id='scope_' + '8' * 32)])
    assert code_of(lambda: e.domain.submit(e.request(), principal=PRINCIPAL)) == ('dependency_unavailable', 'no_eligible_runner_online')
    assert e.github.dispatches == [] and e.claims.intents == {}


def test_offline_queue_policy_dispatches_with_explicit_reason(tmp_path):
    e = Env(tmp_path, rt=runtime(offline_policy='queue'), runners=[runner(registration='offline')])
    result = e.domain.submit(e.request(), principal=PRINCIPAL)
    assert result['data']['status'] == 'queued'
    assert [s['code'] for s in result['meta']['source_errors']] == ['no_eligible_runner_online']


def test_stale_inventory_is_not_eligibility(tmp_path):
    e = Env(tmp_path, freshness='stale')
    assert code_of(lambda: e.domain.submit(e.request(), principal=PRINCIPAL)) == ('stale_evidence', 'runner_inventory_stale')


@pytest.mark.parametrize('verdict', ['source_not_reachable_from_trusted_ref', 'ref_not_protected'])
def test_untrusted_source_is_rejected(env, verdict):
    env.github.reachable = verdict
    assert code_of(lambda: env.domain.submit(env.request(), principal=PRINCIPAL)) == ('unsafe_target', verdict)
    assert env.github.dispatches == []


# ---------------------------------------------------------------- inputs / idempotency / rate limits

def test_intent_persisted_and_registered_before_dispatch_with_request_id_input(env):
    result = env.domain.submit(env.request(inputs=[dict(input_id=INPUT, value='release')]), principal=PRINCIPAL)
    sent = env.github.dispatches[0]
    assert env.github.seen_state == ['dispatching']
    assert sent['inputs'] == dict(configuration='release', request_id=env.domain.details(result['data']['id'])['job']['request_id'],
                                  source_sha=SHA)
    assert sent['ref'] == 'refs/heads/main' and sent['path'].endswith('canary-macos.yml')
    assert result['data']['provider_run_id'] == '9001'


@pytest.mark.parametrize('inputs,reason', [
    ([dict(input_id=INPUT, value='debug'), dict(input_id=INPUT, value='release')], 'duplicate_input'),
    ([dict(input_id='input_' + '7' * 32, value='x')], 'unknown_input'),
    ([dict(input_id=INPUT, value='other')], 'input_not_allowed'),
    ([dict(input_id=INPUT, value=True)], 'input_type_mismatch'),
])
def test_input_validation(env, inputs, reason):
    assert code_of(lambda: env.domain.submit(env.request(inputs=inputs), principal=PRINCIPAL)) == ('invalid_request', reason)
    assert env.github.dispatches == []


def test_unsafe_free_text_input_is_rejected(tmp_path):
    cfg = config()
    cfg['allowed_workflows'][0]['input_definitions'][0]['allowed_values'] = []
    e = Env(tmp_path, cfg=cfg)
    for value in ('$(id)', 'a b', 'x;rm', '`x`', '../../etc'):
        assert code_of(lambda: e.domain.submit(e.request(inputs=[dict(input_id=INPUT, value=value)]),
                                               principal=PRINCIPAL)) == ('invalid_request', 'input_unsafe')
    assert e.github.dispatches == []


def test_duplicate_submission_is_idempotent_and_conflicting_reuse_refused(env):
    first = env.domain.submit(env.request(), principal=PRINCIPAL)
    again = env.domain.submit(env.request(), principal=PRINCIPAL)
    assert again['data']['id'] == first['data']['id'] and len(env.github.dispatches) == 1
    assert code_of(lambda: env.domain.submit(env.request(sha='6' * 40), principal=PRINCIPAL)) == ('idempotency_conflict', 'idempotency_key_reused')
    # Same key from another principal is a different request scope.
    other = env.domain.submit(env.request(), principal='oidc:someone-else')
    assert other['data']['id'] != first['data']['id'] and len(env.github.dispatches) == 2


def test_concurrent_duplicate_submissions_dispatch_once(env):
    results, barrier = [], threading.Barrier(8)

    def go():
        barrier.wait()
        try:
            results.append(env.domain.submit(env.request(), principal=PRINCIPAL)['data']['id'])
        except J.JobError as exc:
            results.append(exc.reason_code)
    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(env.github.dispatches) == 1
    assert len({r for r in results if r.startswith('job_')}) == 1


def test_principal_rate_limit(env):
    for i in range(5):
        env.domain.submit(env.request(key='idem_%032x' % (i + 1)), principal=PRINCIPAL)
    exc = pytest.raises(J.JobError, env.domain.submit, env.request(key='idem_%032x' % 99), principal=PRINCIPAL).value
    assert (exc.code, exc.reason_code, exc.status) == ('rate_limited', 'dispatch_rate_limited', 429) and exc.retry_after > 0
    env.now += 3601
    assert env.domain.submit(env.request(key='idem_%032x' % 99), principal=PRINCIPAL)['data']['status'] == 'queued'


def test_provider_rate_limit_is_a_definite_rejection(env):
    env.github.mode = 'ratelimit'
    exc = pytest.raises(J.JobError, env.domain.submit, env.request(), principal=PRINCIPAL).value
    assert (exc.code, exc.reason_code, exc.retry_after) == ('rate_limited', 'provider_rate_limited', 30)
    # Replaying the same key does not re-dispatch.
    assert pytest.raises(J.JobError, env.domain.submit, env.request(), principal=PRINCIPAL).value.reason_code == 'provider_rate_limited'
    assert len(env.github.dispatches) == 1


def test_intent_registration_failure_never_dispatches(env):
    env.claims.fail_register = True
    assert code_of(lambda: env.domain.submit(env.request(), principal=PRINCIPAL)) == ('dependency_unavailable', 'claim_intent_unregistered')
    assert env.github.dispatches == []


# ---------------------------------------------------------------- unknown outcome / reconciliation

def test_timeout_is_unknown_outcome_reconciled_by_request_id_never_resubmitted(env):
    env.github.mode = 'timeout'
    result = env.domain.submit(env.request(), principal=PRINCIPAL)
    job_id = result['data']['id']
    assert result['data']['status'] == 'unknown_outcome' and len(env.github.dispatches) == 1
    assert env.domain.submit(env.request(), principal=PRINCIPAL)['data']['status'] == 'unknown_outcome'
    assert env.domain.reconcile(job_id)['data']['status'] == 'unknown_outcome'
    assert len(env.github.dispatches) == 1
    # GitHub actually accepted it: the run appears with the request ID in its run-name.
    rid = env.domain.details(job_id)['job']['request_id']
    env.github.runs['777'] = dict(id=777, run_attempt=1, display_title='canary [%s]' % rid, head_sha=WSHA,
                                  status='in_progress', conclusion=None)
    env.github.runs['778'] = dict(id=778, run_attempt=1, display_title='canary [request_%s]' % ('0' * 32), head_sha=WSHA,
                                  status='in_progress', conclusion=None)
    tracked = env.domain.reconcile(job_id)['data']
    assert (tracked['provider_run_id'], tracked['status']) == ('777', 'running')
    assert len(env.github.dispatches) == 1


def test_duplicate_runs_bind_to_the_claim_holder(env):
    env.github.mode = 'timeout'
    job_id = env.domain.submit(env.request(), principal=PRINCIPAL)['data']['id']
    rid = env.domain.details(job_id)['job']['request_id']
    for run_id in (801, 802):
        env.github.runs[str(run_id)] = dict(id=run_id, run_attempt=1, display_title='x [%s]' % rid, head_sha=WSHA,
                                            status='in_progress', conclusion=None)
    assert env.domain.reconcile(job_id)['data']['provider_run_id'] is None  # ambiguous: no guess
    env.claims.claim[rid] = dict(run_id='802', run_attempt='1', claim_id='claim_x')
    assert env.domain.reconcile(job_id)['data']['provider_run_id'] == '802'
    assert env.domain.details(job_id)['duplicate_run_ids'] == ['801']


def test_exact_run_attempt_job_and_runner_tracking(env):
    job_id = env.domain.submit(env.request(), principal=PRINCIPAL)['data']['id']
    env.github.runs['9001'].update(status='in_progress', run_attempt=1)
    env.github.jobs[('9001', 1)] = [dict(id=55501, runner_id=23, runner_name='example-mini')]
    data = env.domain.reconcile(job_id)['data']
    assert data['provider_job_id'] == '55501' and data['attempt'] == 1
    assert data['assigned_runner_id'] == provider_runner_key('github_actions', 'https://api.github.com', SCOPE, '23')
    assert data['workflow_ref_sha'] == WSHA and data['source_sha'] == SHA and WSHA != SHA


def test_run_with_foreign_request_id_is_refused(env):
    job_id = env.domain.submit(env.request(), principal=PRINCIPAL)['data']['id']
    env.github.runs['9001']['display_title'] = 'latest run [request_%s]' % ('0' * 32)
    assert code_of(lambda: env.domain.reconcile(job_id))[1] == 'run_request_mismatch'


def test_run_title_not_yet_rendered_is_retryable_not_a_conflict(env):
    # Live G canary: one early reconcile of a just-dispatched run returned a title without the
    # request token. Until the run is complete that is "not yet known", not a foreign run.
    job_id = env.domain.submit(env.request(), principal=PRINCIPAL)['data']['id']
    rid = env.domain.details(job_id)['job']['request_id']
    env.github.runs['9001']['display_title'] = 'runners canary'
    with pytest.raises(J.JobError) as exc:
        env.domain.reconcile(job_id)
    assert exc.value.error() == dict(code='dependency_unavailable', retryable=True, reason_code='run_title_pending')
    assert env.domain.details(job_id)['job']['status'] != 'concluded'
    env.github.runs['9001'].update(display_title='runners canary [%s]' % rid, status='in_progress')
    assert env.domain.reconcile(job_id)['data']['provider_run_id'] == '9001'


def test_completed_run_without_request_token_is_still_a_conflict(env):
    job_id = env.domain.submit(env.request(), principal=PRINCIPAL)['data']['id']
    env.github.runs['9001'].update(display_title='runners canary', status='completed', conclusion='success')
    assert code_of(lambda: env.domain.reconcile(job_id)) == ('owner_conflict', 'run_request_mismatch')


def test_in_progress_run_with_foreign_request_id_is_a_conflict(env):
    job_id = env.domain.submit(env.request(), principal=PRINCIPAL)['data']['id']
    env.github.runs['9001'].update(display_title='x [request_%s]' % ('0' * 32), status='queued')
    assert code_of(lambda: env.domain.reconcile(job_id)) == ('owner_conflict', 'run_request_mismatch')


def test_retry_attempts_and_crashed_winner_need_a_new_request(env):
    job_id = env.domain.submit(env.request(), principal=PRINCIPAL)['data']['id']
    old_rid = env.domain.details(job_id)['job']['request_id']
    env.claims.claim[old_rid] = dict(run_id='9001', run_attempt='1', claim_id='claim_a')
    # The winning attempt crashed (failure); someone re-ran it in GitHub (attempt 2 cannot claim).
    env.github.runs['9001'].update(status='completed', conclusion='failure', run_attempt=2)
    env.github.attempts[('9001', 1)] = dict(env.github.runs['9001'], run_attempt=1, conclusion='failure')
    env.github.jobs[('9001', 1)] = [dict(id=1, runner_id=23, runner_name='example-mini')]
    data = env.domain.reconcile(job_id)['data']
    # Exact attempt: the one holding the claim, not the latest re-run.
    assert (data['attempt'], data['status'], data['conclusion']) == (1, 'concluded', 'failure')
    assert env.domain._load(job_id)['refused_attempts'] == [2]
    assert env.domain.details(job_id)['claim']['run_attempt'] == '1'
    retried = env.domain.retry(job_id, principal=PRINCIPAL, idempotency_key='idem_' + '7' * 32)
    new = env.domain.details(retried['data']['id'])
    assert new['job']['request_id'] != old_rid and len(env.github.dispatches) == 2
    assert env.github.dispatches[1]['inputs']['request_id'] == new['job']['request_id']


def test_rerun_of_successful_attempt_keeps_winning_attempt_and_artifact(env):
    job_id = concluded(env)
    env.github.runs['9001'].update(run_attempt=2, conclusion='failure')  # attempt 2 refused at claim
    env.github.attempts[('9001', 1)] = dict(env.github.runs['9001'], run_attempt=1, conclusion='success')
    data = env.domain.reconcile(job_id)['data']
    assert (data['attempt'], data['conclusion']) == (1, 'success')
    assert data['artifacts'][0]['state'] == 'available' and data['artifacts'][0]['attempt'] == 1


def test_unclaimed_concluded_run_settles_only_after_intent_expiry(env):
    job_id = env.domain.submit(env.request(), principal=PRINCIPAL)['data']['id']
    rid = env.domain.details(job_id)['job']['request_id']
    env.github.runs['9001'].update(status='completed', conclusion='failure')
    env.domain.reconcile(job_id)
    assert env.domain.details(job_id)['artifact_details'] == [dict(state='none', reason='no_execution_claim')]
    assert not env.domain._load(job_id).get('artifacts_checked')  # a GitHub re-run could still claim
    env.claims.expiry[rid] = env.now - 1
    env.domain.reconcile(job_id)
    assert env.domain._load(job_id).get('artifacts_checked') is True


def test_rerun_that_claims_after_preclaim_failure_is_collected(env):
    job_id = env.domain.submit(env.request(), principal=PRINCIPAL)['data']['id']
    job = env.domain.details(job_id)['job']
    env.github.runs['9001'].update(status='completed', conclusion='failure')  # attempt 1 failed before claiming
    env.domain.reconcile(job_id)
    # GitHub re-run: attempt 2 claims, builds and uploads.
    env.github.runs['9001'].update(run_attempt=2, conclusion='success')
    env.claims.claim[job['request_id']] = dict(run_id='9001', run_attempt='2', claim_id='claim_b')
    env.github.jobs[('9001', 2)] = [dict(id=2, runner_id=23, runner_name='example-mini')]
    blob = provenance_zip(dict(job, provider_run_id='9001', attempt=2))
    env.github.blobs[42] = blob
    env.github.artifact_list['9001'] = [dict(id=42, name='canary-' + job['request_id'], size_in_bytes=len(blob),
                                             expired=False, digest='sha256:' + hashlib.sha256(blob).hexdigest())]
    data = env.domain.reconcile(job_id)['data']
    assert (data['attempt'], data['conclusion'], data['artifacts'][0]['state']) == (2, 'success', 'available')


def test_retry_revokes_the_old_unclaimed_intent_first(env):
    job_id = env.domain.submit(env.request(), principal=PRINCIPAL)['data']['id']
    old = env.domain.details(job_id)['job']['request_id']
    env.github.runs['9001'].update(status='completed', conclusion='failure')
    env.domain.reconcile(job_id)
    env.claims.fail_revoke = True
    assert code_of(lambda: env.domain.retry(job_id, principal=PRINCIPAL, idempotency_key='idem_' + '7' * 32)) == (
        'dependency_unavailable', 'old_intent_not_revoked')
    assert len(env.github.dispatches) == 1
    env.claims.fail_revoke = False
    env.domain.retry(job_id, principal=PRINCIPAL, idempotency_key='idem_' + '7' * 32)
    assert env.claims.revoked == [old] and len(env.github.dispatches) == 2


def test_unknown_outcome_retry_waits_for_reconcile_window(env):
    env.github.mode = 'timeout'
    job_id = env.domain.submit(env.request(), principal=PRINCIPAL)['data']['id']
    env.github.mode = 'ok'
    assert code_of(lambda: env.domain.retry(job_id, principal=PRINCIPAL, idempotency_key='idem_' + '7' * 32)) == (
        'unknown_outcome', 'outcome_still_unknown_reconcile_first')
    env.now += J.UNKNOWN_RETRY_WINDOW_SECONDS + 1
    retried = env.domain.retry(job_id, principal=PRINCIPAL, idempotency_key='idem_' + '7' * 32)
    assert retried['data']['id'] != job_id and len(env.github.dispatches) == 2


def test_reconcile_leaves_fresh_dispatching_record_to_submitter(env):
    calls = []
    original = env.github.dispatch

    def racing_dispatch(*args):
        # A concurrent GET reconciles while the submitter is mid-dispatch.
        rid = args[-1]['request_id']
        with env.domain._db() as db:
            jid = db.execute('SELECT id FROM jobs_domain_requests WHERE payload LIKE ?', ('%' + rid + '%',)).fetchone()[0]
        calls.append(env.domain.reconcile(jid)['data']['status'])
        return original(*args)
    env.github.dispatch = racing_dispatch
    result = env.domain.submit(env.request(), principal=PRINCIPAL)
    assert calls == ['queued'] and result['data']['provider_run_id'] == '9001'
    assert env.domain.details(result['data']['id'])['state'] == 'dispatched'


def test_retry_refused_while_active(env):
    job_id = env.domain.submit(env.request(), principal=PRINCIPAL)['data']['id']
    assert code_of(lambda: env.domain.retry(job_id, principal=PRINCIPAL, idempotency_key='idem_' + '7' * 32))[1] == 'job_still_active'


def test_background_lookup_is_cached_and_singleflight(env):
    job_id = env.domain.submit(env.request(), principal=PRINCIPAL)['data']['id']
    started = []
    env.domain.get(job_id, background=started.append)
    env.domain.get(job_id, background=started.append)
    assert len(started) == 1
    started[0]()
    env.domain.get(job_id, background=started.append)
    assert len(started) == 2


# ---------------------------------------------------------------- artifacts

def make_zip(entries, *, symlink=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, data in entries.items():
            z.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0o120777 << 16)
            z.writestr(info, '/etc/passwd')
    return buf.getvalue()


def provenance_zip(job, extra=None, **overrides):
    binary = b'\xcf\xfa\xed\xfe example binary'
    manifest = dict(request_id=job['request_id'], source_sha=job['source_sha'], run_id=job['provider_run_id'],
                    run_attempt=str(job['attempt']), workflow_sha=WSHA, exit_status=0, runner_name='example-mini',
                    toolchain='Swift version 6.0 (example)', files={'bin/canary': hashlib.sha256(binary).hexdigest()})
    manifest.update(overrides)
    return make_zip(dict({'bin/canary': binary, 'provenance.json': json.dumps(manifest)}, **(extra or {})))


def concluded(env, blob=None, expired=False, digest=None):
    job_id = env.domain.submit(env.request(), principal=PRINCIPAL)['data']['id']
    env.github.runs['9001'].update(status='completed', conclusion='success')
    env.github.jobs[('9001', 1)] = [dict(id=1, runner_id=23, runner_name='example-mini')]
    job = dict(env.domain.details(job_id)['job'], provider_run_id='9001', attempt=1)
    env.claims.claim[job['request_id']] = dict(run_id='9001', run_attempt='1', claim_id='claim_a')
    blob = provenance_zip(job) if blob is None else (blob(job) if callable(blob) else blob)
    env.github.blobs[42] = blob
    env.github.artifact_list['9001'] = [dict(id=42, name='canary-' + job['request_id'], size_in_bytes=len(blob),
                                             expired=expired, digest=digest or 'sha256:' + hashlib.sha256(blob).hexdigest())]
    return job_id


def test_artifact_provenance_checksum_and_safe_extraction(env, tmp_path):
    job_id = concluded(env)
    data = env.domain.reconcile(job_id)['data']
    art = data['artifacts'][0]
    assert art['state'] == 'available' and art['source_sha'] == SHA and art['provider_run_id'] == '9001'
    detail = env.domain.details(job_id)['artifact_details'][0]
    assert detail['state'] == 'verified' and detail['exit_status'] == 0 and 'Swift' in detail['toolchain']
    assert (tmp_path / 'artifacts' / job_id).exists()


def test_artifact_digest_mismatch_is_rejected(env):
    job_id = concluded(env, digest='sha256:' + '0' * 64)
    env.domain.reconcile(job_id)
    detail = env.domain.details(job_id)['artifact_details'][0]
    assert detail == dict(id=detail['id'], state='rejected', reason='artifact_digest_mismatch')


def test_expired_artifact_is_reported_never_rebuilt(env):
    job_id = concluded(env, expired=True)
    data = env.domain.reconcile(job_id)['data']
    assert data['artifacts'][0]['state'] == 'expired'
    assert env.domain.details(job_id)['artifact_details'][0]['reason'] == 'artifact_expired_not_rebuilt'
    assert len(env.github.dispatches) == 1


@pytest.mark.parametrize('build,reason', [
    (lambda job: make_zip({'../escape': 'x', 'provenance.json': '{}'}), 'artifact_unsafe_path'),
    (lambda job: make_zip({'/abs/path': 'x'}), 'artifact_unsafe_path'),
    (lambda job: make_zip({'a\\..\\b': 'x'}), 'artifact_unsafe_path'),
    (lambda job: make_zip({'ok': 'x'}, symlink='link'), 'artifact_unsafe_type'),
    (lambda job: make_zip({'f%d' % i: 'x' for i in range(65)}), 'artifact_too_many_files'),
    (lambda job: b'not a zip', 'artifact_not_zip'),
    (lambda job: provenance_zip(job, source_sha='9' * 40), 'provenance_mismatch'),
    (lambda job: provenance_zip(job, request_id='request_' + '0' * 32), 'provenance_mismatch'),
    (lambda job: provenance_zip(job, run_attempt='2'), 'provenance_mismatch'),
    (lambda job: provenance_zip(job, files={'bin/canary': '0' * 64}), 'artifact_checksum_mismatch'),
    (lambda job: make_zip({'bin/canary': 'x'}), 'provenance_missing'),
    (lambda job: make_zip({'a': 'x', 'a/b': 'y'}), 'artifact_path_conflict'),
    (lambda job: make_zip({'a/': '', 'a': 'y'}), 'artifact_duplicate_name'),
    (lambda job: provenance_zip(job, extra={'extra.sh': 'x'}), 'artifact_unlisted_or_missing_file'),
])
def test_unsafe_or_mismatched_artifacts_are_rejected(env, build, reason):
    job_id = concluded(env, blob=build)
    env.domain.reconcile(job_id)
    assert env.domain.details(job_id)['artifact_details'][0]['reason'] == reason


def test_local_extraction_failure_is_retried_and_final_download_errors_are_not(env, monkeypatch):
    job_id = concluded(env)
    real = J.safe_extract

    def disk_full(*a, **k):
        raise J.ArtifactLocalError('artifact_extract_failed')
    monkeypatch.setattr(J, 'safe_extract', disk_full)
    env.domain.reconcile(job_id)
    assert env.domain.details(job_id)['artifact_details'][0]['state'] == 'pending'
    monkeypatch.setattr(J, 'safe_extract', real)
    assert env.domain.reconcile(job_id)['data']['artifacts'][0]['state'] == 'available'

    other = Env(env.domain.artifact_root.parent / 'final')
    job2 = concluded(other)

    def refused(*a):
        raise J.ArtifactError('artifact_redirect_refused')
    other.github.download_artifact = refused
    other.domain.reconcile(job2)
    assert other.domain.details(job2)['artifact_details'][0] == dict(
        id=other.domain.details(job2)['artifact_details'][0]['id'], state='rejected', reason='artifact_redirect_refused')
    assert other.domain._load(job2)['artifacts_checked'] is True


def test_transient_artifact_download_is_retried_not_final(env):
    job_id = concluded(env)
    blobs, env.github.blobs = env.github.blobs, {}
    original = env.github.download_artifact

    def failing(*a):
        raise J.ArtifactError('artifact_download_failed')
    env.github.download_artifact = failing
    env.domain.reconcile(job_id)
    assert env.domain.details(job_id)['artifact_details'][0]['state'] == 'pending'
    assert env.domain._load(job_id).get('artifacts_checked') is None
    env.github.blobs, env.github.download_artifact = blobs, original
    data = env.domain.reconcile(job_id)['data']
    assert data['artifacts'][0]['state'] == 'available'


def test_safe_extract_is_atomic_and_replaces_previous_destination(tmp_path):
    dest = tmp_path / 'job' / 'artifact'
    J.safe_extract(make_zip({'a.txt': 'one'}), dest)
    with pytest.raises(J.ArtifactError):
        J.safe_extract(make_zip({'../x': 'bad'}), dest)
    assert (dest / 'a.txt').read_text() == 'one'  # refusal leaves the previous verified copy intact
    J.safe_extract(make_zip({'b.txt': 'two'}), dest)
    assert sorted(p.name for p in dest.iterdir()) == ['b.txt']
    assert [p.name for p in (tmp_path / 'job').iterdir()] == ['artifact']  # no staging leftovers


def test_safe_extract_detects_size_lies(tmp_path):
    blob = bytearray(make_zip({'big': b'A' * 5000}))
    # Patch the central directory's declared uncompressed size down to 10 bytes.
    idx = blob.rfind(b'PK\x01\x02')
    blob[idx + 24:idx + 28] = (10).to_bytes(4, 'little')
    with pytest.raises(J.ArtifactError):
        J.safe_extract(bytes(blob), tmp_path / 'out')


def test_safe_extract_bounds_total_size(tmp_path):
    limits = dict(J.ARTIFACT_LIMITS, total_bytes=100, file_bytes=80)
    with pytest.raises(J.ArtifactError) as exc:
        J.safe_extract(make_zip({'a': b'x' * 60, 'b': b'y' * 60}), tmp_path / 'o', limits=limits)
    assert str(exc.value) == 'artifact_too_large'


# ---------------------------------------------------------------- runtime config and production port

def test_runtime_config_rejects_unsafe_values():
    for bad in (dict(repository='../x'), dict(workflow_path='x.yml'), dict(ref='refs/tags/v1'),
                dict(offline_policy='local'), dict(inputs={INPUT: 'request_id'}), dict(trusted_refs={TRUSTED: 'refs/heads/dev'})):
        with pytest.raises(J.JobError):
            runtime(**bad)
    with pytest.raises(J.JobError):
        J.load_dispatch_runtime(dict(claims_socket='/x', claims_health_url='https://example.com/v1/health', workflows={}))


def test_artifact_redirect_must_stay_on_allowlisted_storage():
    class Port(J.GitHubDispatchPort):
        def __init__(self, location):
            super().__init__(lambda ref: None)
            self.location = location

        def _request(self, ref, method, path, body=None, *, host='api.github.com', auth=True, max_bytes=0):
            if host == 'api.github.com':
                return 302, {'location': self.location}, b''
            return 200, {}, b'zip'
    for location in ('http://pipelines.actions.githubusercontent.com/x', 'https://evil.example.com/x',
                     'https://blob.core.windows.net.evil.example/x'):
        with pytest.raises(J.ArtifactError):
            Port(location).download_artifact(CRED, REPO, 1, 100)
    assert Port('https://productionresultssa1.blob.core.windows.net/a?sig=x').download_artifact(CRED, REPO, 1, 100) == b'zip'


def test_dispatch_port_classifies_outcomes():
    class Port(J.GitHubDispatchPort):
        def __init__(self, result):
            super().__init__(lambda ref: None)
            self.result = result

        def _request(self, *a, **k):
            if isinstance(self.result, Exception):
                raise self.result
            return self.result
    assert Port((200, {}, b'{"workflow_run_id": 12}')).dispatch(CRED, REPO, '.github/workflows/x.yml', 'refs/heads/main', {}) == dict(run_id=12)
    assert Port((204, {}, b'')).dispatch(CRED, REPO, '.github/workflows/x.yml', 'refs/heads/main', {}) == dict(run_id=None)
    for unknown in (TimeoutError(), ConnectionResetError(), (502, {}, b'')):
        with pytest.raises(J.DispatchUnknown):
            Port(unknown).dispatch(CRED, REPO, '.github/workflows/x.yml', 'refs/heads/main', {})
    with pytest.raises(J.DispatchRejected) as exc:
        Port((429, {'retry-after': '60'}, b'')).dispatch(CRED, REPO, '.github/workflows/x.yml', 'refs/heads/main', {})
    assert (exc.value.reason_code, exc.value.retry_after) == ('provider_rate_limited', 60)
    with pytest.raises(J.DispatchRejected):
        Port((422, {}, b'')).dispatch(CRED, REPO, '.github/workflows/x.yml', 'refs/heads/main', {})


# ---------------------------------------------------------------- REST boundary

def _api():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('jobs_api', Path(__file__).parents[1] / 'dashboard/plugin_api.py')
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    return api


def test_rest_routes_principal_and_error_mapping(env, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    api = _api()
    backend = SimpleNamespace(config=config())
    monkeypatch.setattr(api, 'service', lambda: backend)
    monkeypatch.setattr(api, 'active_profile', lambda: 'default')
    monkeypatch.setattr(api, 'jobs_domain', lambda b: env.domain)
    started = []
    monkeypatch.setattr(api, '_start_background', started.append)
    app = FastAPI()

    @app.middleware('http')
    async def authenticated(request, call_next):
        if request.headers.get('x-fixture-user'):
            request.state.session = SimpleNamespace(provider='oidc', user_id=request.headers['x-fixture-user'])
        return await call_next(request)
    app.dependency_overrides[api.authorize] = lambda: None
    app.include_router(api.router)
    with TestClient(app) as client:
        assert [w['id'] for w in client.get('/v1/workflows').json()['data']] == [WF]
        # Loopback/service tokens are not login principals: dispatch is refused.
        assert client.post('/v1/jobs', json=env.request()).status_code == 403
        assert env.github.dispatches == []
        ok = client.post('/v1/jobs', json=env.request(), headers={'x-fixture-user': 'example'})
        assert ok.status_code == 202 and ok.json()['data']['status'] == 'queued'
        job_id = ok.json()['data']['id']
        got = client.get('/v1/jobs/' + job_id)
        assert got.status_code == 200 and got.json()['data']['request_id'] == env.domain.details(job_id)['job']['request_id']
        assert len(started) == 1
        bad = client.post('/v1/jobs', json=dict(env.request(key='idem_' + '9' * 32), source_sha='z'), headers={'x-fixture-user': 'example'})
        assert bad.status_code == 422
        env.claims.healthy = False
        down = client.post('/v1/jobs', json=env.request(key='idem_' + '8' * 32), headers={'x-fixture-user': 'example'})
        assert down.status_code == 503 and down.json()['detail'] == dict(code='dependency_unavailable', retryable=True,
                                                                          reason_code='claim_broker_unreachable')
        assert client.get('/v1/jobs/job_' + '0' * 32).status_code == 404
    assert len(env.github.dispatches) == 1


def test_tools_never_expose_canary_mode():
    import importlib
    from pathlib import Path
    root = Path(importlib.import_module('plugins.runners').__file__).parent
    source = (root / '__init__.py').read_text()
    assert 'canary=' not in source and 'principal=principal)' in source
    api_source = (root / 'dashboard/plugin_api.py').read_text()
    assert 'canary=' not in api_source
