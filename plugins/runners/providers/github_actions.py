"""Bounded GitHub.com inventory and optional job reads, with no ambient I/O.

Call enumerate(scope) once per scope, not per runner. Transport is a callable
GET client (see base.py). Clock returns epoch seconds; jitter returns [0,1].
Backoff is nonblocking: errors schedule the next eligible call, never sleep or
retry within a poll. Keep one adapter per collector to retain credential budgets.
No Link/redirect/API-returned URL is followed. Enterprise is GitHub.com only.
Job results are advisory and separate from registration health; incomplete job
coverage must never imply stuck/idle/offline. The caller supplies the explicit
repository allowlist and recent runner IDs; neither is discovered or widened.
"""
from copy import deepcopy
from dataclasses import dataclass, asdict
from email.utils import parsedate_to_datetime
import hashlib
import math
import random
import re
import time
from threading import RLock

from .base import Credential, HTTPResponse, ProviderResult
from ..models import validate, runner_id


class _Failure(Exception):
    def __init__(self, code, retry_after_seconds=None):
        super().__init__(code)
        self.retry_after_seconds = retry_after_seconds


class BudgetPersistenceError(RuntimeError):
    """Fail closed when a request cannot be accounted for durably."""



@dataclass
class _Budget:
    remaining: int | None = None
    reset: float = 0
    until: float = 0
    failures: int = 0
    next_at: float = 0
    cause: str | None = None


class GitHubActions:
    ENDPOINT = 'https://api.github.com'

    def __init__(self, *, transport, credential_resolver, clock=time.time,
                 jitter=random.random, timeout=10, max_pages=100,
                 max_job_requests=10, max_repositories=10, max_runs=10,
                 enrichment_interval=60, backoff_base=2, backoff_cap=300,
                 max_cycle_requests=32, pace_requests=False, pacing_window=0):
        for value in (timeout, max_pages, max_job_requests, max_repositories,
                      max_runs, enrichment_interval, backoff_base, backoff_cap):
            if not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                raise ValueError('invalid_provider_options')
        for value in (max_pages, max_job_requests, max_repositories, max_runs):
            if type(value) is not int:
                raise ValueError('invalid_provider_options')
        self.transport, self.credential_resolver = transport, credential_resolver
        self.clock, self.jitter, self.timeout = clock, jitter, timeout
        self.max_pages, self.max_job_requests = max_pages, max_job_requests
        self.max_repositories, self.max_runs = max_repositories, max_runs
        self.enrichment_interval = enrichment_interval
        self.backoff_base, self.backoff_cap = backoff_base, backoff_cap
        if type(max_cycle_requests) is not int or max_cycle_requests < 1:
            raise ValueError('invalid_provider_options')
        if type(pacing_window) not in (int, float) or not math.isfinite(pacing_window) or not 0 <= pacing_window <= 30:
            raise ValueError('invalid_provider_options')
        self.pacing_window = pacing_window
        self.max_cycle_requests, self.pace_requests = max_cycle_requests, pace_requests
        self._cycle_remaining = None
        self._budgets, self._cache, self._enriched = {}, {}, {}
        self._lock = RLock()
        self.state_sink = None

    def _persist(self, state=None):
        if self.state_sink is not None:
            try:
                self.state_sink(self.export_state() if state is None else state)
            except Exception:
                raise BudgetPersistenceError('budget_persistence_failed') from None

    def begin_cycle(self):
        with self._lock:
            self._cycle_remaining = self.max_cycle_requests

    def export_state(self):
        """Persist only pacing/backoff, never tokens or authorized page caches."""
        with self._lock:
            return {key: asdict(budget) for key, budget in self._budgets.items()}

    def restore_state(self, state):
        with self._lock:
            restored = {}
            if type(state) is not dict:
                raise ValueError('invalid_budget_state')
            for key, value in state.items():
                if type(key) is not str or not key or len(key) > 256 or type(value) is not dict:
                    raise ValueError('invalid_budget_state')
                budget = _Budget(**value)
                if budget.cause not in (None, 'forbidden', 'not_found', 'rate_limited', 'unauthorized', 'server_error', 'network_error', 'unexpected_status'):
                    raise ValueError('invalid_budget_state')
                if budget.remaining is not None and (type(budget.remaining) is not int or budget.remaining < 0):
                    raise ValueError('invalid_budget_state')
                if type(budget.failures) is not int or budget.failures < 0:
                    raise ValueError('invalid_budget_state')
                if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0
                       for v in (budget.reset, budget.until, budget.next_at)):
                    raise ValueError('invalid_budget_state')
                restored[key] = budget
            self._budgets = restored

    @staticmethod
    def _name(name, parts):
        return (type(name) is str and len(name) <= 201 and len(name.split('/')) == parts
                and all(re.fullmatch(r'[A-Za-z0-9_.-]+', p) and p not in ('.', '..') for p in name.split('/')))

    def _scope(self, scope):
        try:
            scope = validate('Scope', scope)
            if not self._name(scope['name'], 2 if scope['kind'] == 'repository' else 1):
                raise ValueError()
            return scope
        except Exception:
            raise _Failure('invalid_scope') from None

    def _credential(self, scope):
        try:
            c = self.credential_resolver(scope['credential_ref'])
            if not isinstance(c, Credential) or not c.key or not isinstance(c.token, str) or not c.token or any(ord(x) < 33 or ord(x) > 126 for x in c.token):
                raise ValueError()
            return c
        except Exception:
            raise _Failure('credential_unavailable') from None

    def _delay(self, budget, headers):
        budget.failures += 1
        now = self.clock()
        delay = min(self.backoff_cap, self.backoff_base * 2 ** min(budget.failures - 1, 20))
        delay *= 1 + min(1, max(0, self.jitter()))
        retry = headers.get('retry-after')
        if retry is not None:
            try:
                seconds = float(retry)
            except (ValueError, TypeError):
                try:
                    seconds = parsedate_to_datetime(retry).timestamp() - now
                except (ValueError, TypeError, OverflowError):
                    seconds = 0
            if math.isfinite(seconds):
                delay = max(delay, seconds)
        budget.until = max(budget.until, now + delay)

    def _get(self, credential, path):
        try:
            result = self._request(credential, path)
        except BudgetPersistenceError:
            raise
        except Exception:
            self._persist()
            raise
        self._persist()
        return result

    def _request(self, credential, path):
        # Paths are generated internally only. Returned links are never used.
        now = self.clock()
        if self._cycle_remaining is not None:
            if self._cycle_remaining <= 0:
                raise _Failure('cycle_budget')
            self._cycle_remaining -= 1
        budget = self._budgets.setdefault(credential.key, _Budget())
        # Permissions are resource-local; quota belongs to the credential.
        prefix, action = path.split('/actions/', 1)
        resource = prefix + '/actions/' + action.split('?', 1)[0].split('/', 1)[0]
        permission_key = 'scope:' + hashlib.sha256((credential.key + '\0' + resource).encode()).hexdigest()
        permission = self._budgets.get(permission_key)
        if permission and now < permission.until:
            raise _Failure(permission.cause, math.ceil(permission.until - now))
        if budget.remaining is not None and budget.remaining <= 0:
            if not budget.reset or now < budget.reset:
                raise _Failure('rate_limited', math.ceil(budget.reset - now) if budget.reset > now else None)
            budget.remaining = None
        if now < budget.until:
            raise _Failure('backoff', math.ceil(budget.until - now))
        key = (credential.key, hashlib.sha256(credential.token.encode()).digest(), path)
        cached = self._cache.get(key)
        headers = {'Authorization': 'Bearer ' + credential.token,
                   'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
        if cached:
            headers['If-None-Match'] = cached[0]
        if budget.remaining is not None:
            budget.remaining -= 1  # Conditional requests are not presumed free.
        # Reserve before I/O. SIGKILL cannot erase consumed budget or immediately
        # repeat an unknown-outcome request. A completed response replaces this
        # conservative crash fence with its actual rate and Retry-After headers.
        reservation = self.export_state()
        reservation[credential.key]['until'] = max(
            budget.until, now + self.timeout + self.backoff_cap)
        self._persist(reservation)
        try:
            response = self.transport('GET', self.ENDPOINT + path, headers=headers,
                                      timeout=self.timeout, allow_redirects=False)
            if not isinstance(response, HTTPResponse):
                raise ValueError()
            h = {k.lower(): v for k, v in response.headers.items()}
        except Exception:
            self._delay(budget, {})
            raise _Failure('network_error') from None
        try:
            if 'x-ratelimit-remaining' in h:
                budget.remaining = max(0, int(h['x-ratelimit-remaining']))
            if 'x-ratelimit-reset' in h:
                reset = float(h['x-ratelimit-reset'])
                if not math.isfinite(reset):
                    raise ValueError()
                budget.reset = reset
        except (ValueError, TypeError, OverflowError):
            raise _Failure('invalid_rate_headers') from None
        if self.pace_requests and budget.remaining is not None and budget.remaining > 0:
            budget.next_at = max(now, budget.next_at) + max(0, budget.reset-now)/budget.remaining
        status = response.status
        if status not in (200, 304):
            code = {401:'unauthorized',403:'forbidden',404:'not_found',429:'rate_limited'}.get(status)
            message = response.body.get('message', '') if type(response.body) is dict else ''
            throttled = status == 403 and (
                'retry-after' in h or budget.remaining == 0 or
                (type(message) is str and any(marker in message.lower()
                 for marker in ('secondary rate limit', 'rate limit exceeded', 'abuse detection'))))
            if throttled:
                code = 'rate_limited'
            if status in (403, 404) and not throttled:
                permission = self._budgets.setdefault(permission_key, _Budget())
                permission.cause = code
                self._delay(permission, h)
            else:
                budget.cause = code or ('server_error' if isinstance(status, int) and status >= 500 else 'unexpected_status')
                self._delay(budget, h)
                if throttled and 'retry-after' not in h:
                    budget.until = max(budget.until, now + 60)
            # An authorization failure must invalidate cached authorization evidence.
            if status == 401:
                self._cache = {k: v for k, v in self._cache.items() if k[0] != credential.key}
            elif status in (403, 404) and not throttled:
                self._cache = {k: v for k, v in self._cache.items()
                               if k[0] != credential.key or not
                               (k[2].split('?', 1)[0] == resource or k[2].startswith(resource + '/'))}
            if status == 401 and hasattr(self.credential_resolver, 'invalidate'):
                self.credential_resolver.invalidate(key=credential.key)
            cooldown = permission if status in (403, 404) and not throttled else budget
            raise _Failure(code or ('server_error' if isinstance(status, int) and status >= 500 else 'unexpected_status'),
                           max(0, math.ceil(cooldown.until - self.clock())))
        if status == 304:
            if not cached:
                raise _Failure('unbacked_304')
            body = deepcopy(cached[1])
        else:
            body = deepcopy(response.body)
            etag = h.get('etag')
            if isinstance(etag, str) and 0 < len(etag) <= 512 and all(32 <= ord(c) < 127 for c in etag):
                self._cache[key] = (etag, deepcopy(body))
            else:
                self._cache.pop(key, None)
        self._budgets.pop(permission_key, None)
        budget.cause = None
        budget.failures = 0
        budget.until = 0
        return body

    @staticmethod
    def _page(body, key):
        if (type(body) is not dict or type(body.get('total_count')) is not int
                or body['total_count'] < 0 or type(body.get(key)) is not list
                or len(body[key]) > 100 or any(type(x) is not dict for x in body[key])):
            raise _Failure('invalid_payload')
        return body['total_count'], body[key]

    @staticmethod
    def _id(value):
        if type(value) is not int or not 0 < value < 10**20:
            raise _Failure('invalid_payload')
        return str(value)

    def _runner(self, scope, raw):
        rid = self._id(raw.get('id'))
        if raw.get('status') not in ('online', 'offline') or type(raw.get('busy')) is not bool:
            raise _Failure('invalid_payload')
        labels = raw.get('labels')
        if type(labels) is not list or any(type(x) is not dict or type(x.get('name')) is not str for x in labels):
            raise _Failure('invalid_payload')
        row = dict(id=runner_id('github_actions', self.ENDPOINT, scope['id'], rid),
                   provider='github_actions', endpoint=self.ENDPOINT, scope_id=scope['id'],
                   provider_runner_id=rid, host_id=None, display_name=raw.get('name'),
                   labels=list(dict.fromkeys(x['name'] for x in labels)),
                   observed_os={'osx':'macos', 'macos':'macos', 'linux':'linux',
                                'win':'windows', 'windows':'windows'}.get(
                                    raw['os'].lower() if type(raw.get('os')) is str else '', 'unknown'),
                   lifecycle='ephemeral' if raw.get('ephemeral') is True else 'unknown',
                   replacement_of=None, expected_expiry=None, registration=raw['status'],
                   activity='busy' if raw['busy'] else 'idle', readiness='not_checked',
                   freshness='fresh', repair='none', observation_ids=[])
        try:
            return validate('Runner', row)
        except Exception:
            raise _Failure('invalid_payload') from None

    def enumerate(self, scope):
        """Return ProviderResult of contract Runner dictionaries; no host inference."""
        with self._lock:
            rows, total, seen = [], None, set()
            try:
                s = self._scope(scope)
                c = self._credential(s)
                budget = self._budgets.get(c.key)
                if self.pace_requests and budget and self.clock() + self.pacing_window < budget.next_at:
                    raise _Failure('budget_deferred')
                prefix = {'repository':'repos','organization':'orgs','enterprise':'enterprises'}[s['kind']]
                for page in range(1, self.max_pages + 1):
                    body = self._get(c, f'/{prefix}/{s["name"]}/actions/runners?per_page=100&page={page}')
                    count, batch = self._page(body, 'runners')
                    if total is None:
                        total = count
                    if count != total:
                        raise _Failure('count_mismatch')
                    projected = [self._runner(s, x) for x in batch]
                    ids = [x['provider_runner_id'] for x in projected]
                    if len(set(ids)) != len(ids) or seen.intersection(ids):
                        raise _Failure('duplicate_id')
                    if len(rows) + len(batch) > total:
                        raise _Failure('count_mismatch')
                    rows.extend(projected)
                    seen.update(ids)
                    if len(rows) == total:
                        return ProviderResult(tuple(rows), True, None, self.clock(), total)
                    if len(batch) < 100:
                        raise _Failure('count_mismatch')
                raise _Failure('page_budget')
            except BudgetPersistenceError:
                raise
            except _Failure as exc:
                return ProviderResult(tuple(rows), False, str(exc), self.clock(), total, exc.retry_after_seconds)
            except Exception:
                return ProviderResult(tuple(rows), False, 'invalid_payload', self.clock(), total)

    def enrich_jobs(self, scope, runners, *, allowed_repositories=(), recent_runner_ids=()):
        """Bounded job evidence for raw GitHub or normalized runner rows.

        Returns rows {repository,run_id,job_id,runner_id,status,conclusion}; IDs
        are strings. total_count is None (filtered bounded query, not inventory).
        complete means all configured bounded queries finished without truncation.
        """
        with self._lock:
            rows = []
            try:
                s = self._scope(scope)
                repos = list(dict.fromkeys(allowed_repositories))
                if len(repos) > self.max_repositories or any(not self._name(r, 2) for r in repos):
                    raise _Failure('invalid_allowlist')
                # Repo/org scope cannot widen to unrelated repositories.
                if any((s['kind'] == 'repository' and r != s['name']) or
                       (s['kind'] == 'organization' and r.split('/')[0] != s['name']) for r in repos):
                    raise _Failure('invalid_allowlist')
                recent = {str(x) for x in recent_runner_ids}
                candidates = set()
                for r in runners:
                    rid = str(r.get('provider_runner_id', r.get('id')))
                    if re.fullmatch(r'[1-9][0-9]{0,19}', rid) and (r.get('busy') is True or r.get('activity') == 'busy' or rid in recent):
                        candidates.add(rid)
                if not candidates or not repos:
                    return ProviderResult((), True, None, self.clock(), None)
                c = self._credential(s)
                cooldown = (c.key, s['id'])
                if self.clock() < self._enriched.get(cooldown, 0):
                    raise _Failure('enrichment_cooldown')
                self._enriched[cooldown] = self.clock() + self.enrichment_interval
                requests = 0
                truncated = False
                def get(path):
                    nonlocal requests
                    if requests >= self.max_job_requests:
                        raise _Failure('request_budget')
                    requests += 1
                    return self._get(c, path)
                for repo in repos:
                    count, runs = self._page(get(f'/repos/{repo}/actions/runs?per_page=100&page=1'), 'workflow_runs')
                    if count < len(runs):
                        raise _Failure('count_mismatch')
                    truncated |= count > len(runs) or len(runs) > self.max_runs
                    seen_runs = set()
                    for run in runs[:self.max_runs]:
                        run_id = self._id(run.get('id'))
                        if run_id in seen_runs:
                            raise _Failure('duplicate_id')
                        seen_runs.add(run_id)
                        count, jobs = self._page(get(f'/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100&page=1'), 'jobs')
                        truncated |= count > len(jobs)
                        if count < len(jobs):
                            raise _Failure('count_mismatch')
                        seen_jobs = set()
                        for job in jobs:
                            jid = self._id(job.get('id'))
                            if jid in seen_jobs:
                                raise _Failure('duplicate_id')
                            seen_jobs.add(jid)
                            rid = str(job.get('runner_id'))
                            if rid not in candidates:
                                continue
                            status = job.get('status')
                            conclusion = job.get('conclusion')
                            if status not in ('queued','in_progress','completed','waiting','requested','pending') or conclusion not in (None,'success','failure','neutral','cancelled','skipped','timed_out','action_required','stale','startup_failure'):
                                raise _Failure('invalid_payload')
                            rows.append(dict(repository=repo,run_id=run_id,job_id=jid,runner_id=rid,
                                             status=status,conclusion=conclusion))
                return ProviderResult(tuple(rows), not truncated, 'enrichment_truncated' if truncated else None, self.clock(), None)
            except BudgetPersistenceError:
                raise
            except _Failure as exc:
                return ProviderResult(tuple(rows), False, str(exc), self.clock(), None, exc.retry_after_seconds)
            except Exception:
                return ProviderResult(tuple(rows), False, 'invalid_payload', self.clock(), None)
