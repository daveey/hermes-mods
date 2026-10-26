"""Observe-only collection. Instantiate only in the supervised collector process.

Store transactions commit observations, reducer state and the cached snapshot
atomically. API workers never instantiate Collector. Remote adapters are injected.
"""
from contextlib import AbstractContextManager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import json
import os
import time
import uuid

from .health import Sample, advance, Checks, Policy, disk_state
from .models import validate


def ident(prefix='obj'):
    return prefix + '_' + uuid.uuid4().hex


def iso(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat().replace('+00:00', 'Z')


class OwnerLock(AbstractContextManager):
    def __init__(self, path, config, identity):
        self.path, self.config, self.identity = path, config, identity
        self.fd = None

    def __enter__(self):
        c = self.config
        if tuple(self.identity) != (c['owner_gateway_id'], c['owner_profile'], c['owner_epoch']):
            raise RuntimeError('owner_conflict')
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            raise RuntimeError('collector_running') from None
        return self

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def readiness(config, row, host_results, now):
    """Only persisted authenticated host links may consume host evidence."""
    evidence=host_results.get(row.get('host_id'), {})
    binding=next((b for b in config['bindings'] if b['runner_id']==row['id'] and b['host_id']==row.get('host_id')),None)
    if not binding or not evidence or evidence['management']!='reachable':
        return Checks(), 'not_checked'
    at=evidence['at']; ttl=config['thresholds']['host_stale_seconds']
    if not 0<=now-at<=ttl:
        return Checks(at=at), 'not_checked'
    facts=evidence['facts']; statuses={}
    free=facts.get('disk_free_bytes')
    statuses['disk']=disk_state(free/1024**3,warn_gib=config['thresholds']['disk_warn_gib'],fail_gib=config['thresholds']['disk_fail_gib']) if free is not None else 'not_checked'
    service=facts.get('services',{}).get(binding['service_ref'],'unknown')
    statuses['service']='pass' if service=='running' else ('fail' if service in ('stopped','failed','missing') else 'not_checked')
    tools_at=facts.get('tools_observed_at',at)
    for tool,value in facts.get('tools',{}).items():
        statuses['tool:'+tool]=('pass' if value else 'fail') if tools_at is not None and 0<=now-tools_at<=900 else 'not_checked'
    checks=Checks(at=at,required=tuple(statuses),passed=tuple(k for k,v in statuses.items() if v=='pass'),failed=tuple(k for k,v in statuses.items() if v=='fail'),warned=tuple(k for k,v in statuses.items() if v=='warn'))
    state='fail' if checks.failed else ('warn' if checks.warned else ('pass' if set(checks.required)<=set(checks.passed) else 'not_checked'))
    return checks,state


def initialize(store):
    # Additive extension, independent of the foundation's schema version.
    with store.transaction():
        store.connection.execute('CREATE TABLE IF NOT EXISTS collector_cache (key TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        store.connection.execute('CREATE TABLE IF NOT EXISTS collector_refresh (id TEXT PRIMARY KEY, targets TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL)')
        store.connection.execute('CREATE TABLE IF NOT EXISTS collector_request_keys (key TEXT PRIMARY KEY, targets TEXT NOT NULL, collection_id TEXT NOT NULL, created REAL NOT NULL)')


class Collector:
    def __init__(self, store, config, adapter, *, clock=time.time, host_probe=None,
                 max_scopes=16, tick_budget=20, monotonic=time.monotonic,
                 job_repositories=None, max_job_scopes=2):
        self.store, self.config, self.adapter, self.clock = store, config, adapter, clock
        if type(max_scopes) is not int or max_scopes < 1 or tick_budget <= 0:
            raise ValueError('invalid_collection_budget')
        self.max_scopes, self.tick_budget, self.monotonic = max_scopes, tick_budget, monotonic
        self.host_probe = host_probe
        if type(max_job_scopes) is not int or not 1 <= max_job_scopes <= 10:
            raise ValueError('invalid_job_budget')
        self.job_repositories = deepcopy(job_repositories or {})
        self.max_job_scopes = max_job_scopes
        self.job_results = {}
        self.host_results, self.scope_results = {}, {}
        self.refresh_progress = {}
        self.next_scope_lane = 'active'
        initialize(store)
        budget = store.connection.execute("SELECT payload FROM collector_cache WHERE key='provider_budget'").fetchone()
        if budget and hasattr(adapter, 'restore_state'):
            adapter.restore_state(json.loads(budget[0]))
        cached = store.connection.execute("SELECT payload FROM collector_cache WHERE key='snapshot'").fetchone()
        if cached:
            cached = json.loads(cached[0])
            if cached.get('owner') not in (None, self.owner()):
                raise RuntimeError('owner_conflict')
            self.host_results = cached.get('host_results', {})
            self.scope_results = cached.get('scope_results', {})
            self.refresh_progress = cached.get('refresh_progress', {})
            self.job_results = cached.get('job_results', {})
            self.next_scope_lane = cached.get('next_scope_lane', 'active')
        if hasattr(adapter, 'state_sink'):
            adapter.state_sink = self._save_budget

    def _save_budget(self, state):
        with self.store.transaction():
            self.store.connection.execute(
                'INSERT INTO collector_cache VALUES (?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload',
                ('provider_budget', json.dumps(state)))

    def owner(self):
        return {k:self.config[k] for k in ('owner_gateway_id','owner_profile','owner_epoch','config_revision')}

    def _targets(self, targets):
        scopes, hosts = set(), set()
        if not targets:
            return {s['id'] for s in self.config['scopes']}, {h['id'] for h in self.config['hosts']}
        runners = {r['id']:r for r in self.store.list('Runner')}
        for target in targets:
            if target in runners:
                scopes.add(runners[target]['scope_id'])
                if runners[target]['host_id']: hosts.add(runners[target]['host_id'])
            for scope in self.config['scopes']:
                if target == scope['id']: scopes.add(target)
            for host in self.config['hosts']:
                if target == host['resource_id']: hosts.add(host['id'])
        return scopes, hosts

    def _hosts(self, now, force=()):
        from concurrent.futures import ThreadPoolExecutor
        if self.host_probe is None:
            return set()
        due = [h for h in self.config['hosts'] if h['id'] in force or now-self.host_results.get(h['id'], {}).get('at', -1e20)>=60]
        def collect(host):
            try:
                facts=self.host_probe(host)
                return host['id'], dict(at=self.clock(),management='reachable',facts=facts)
            except Exception:
                return host['id'], dict(at=self.clock(),management='unreachable',facts={})
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.host_results.update(pool.map(collect,due))
        return {h['id'] for h in due}

    def tick(self):
        # A failed commit must not advance the in-memory scheduler or refresh
        # progress. Provider request budgets deliberately are NOT rolled back:
        # those requests happened even when persistence failed.
        previous = deepcopy((self.host_results, self.scope_results, self.refresh_progress, self.job_results, self.next_scope_lane))
        try:
            return self._tick()
        except BaseException:
            self.host_results, self.scope_results, self.refresh_progress, self.job_results, self.next_scope_lane = previous
            raise
        finally:
            # Persist consumed rate budgets even when the observation transaction
            # failed. This does not persist credentials or ETag response caches.
            if hasattr(self.adapter, 'export_state') and not hasattr(self.adapter, 'state_sink'):
                self._save_budget(self.adapter.export_state())

    def _tick(self):
        if not self.config['features']['observe']:
            raise RuntimeError('feature_disabled')
        now, cycle = self.clock(), ident('collection')
        scopes = self.config['scopes']
        # Claim the current batch before I/O. Requests arriving during collection
        # remain pending for the NEXT tick rather than falsely completing.
        with self.store.transaction():
            claimed = list(self.store.connection.execute("SELECT id,targets,created,state FROM collector_refresh WHERE state IN ('pending','running') ORDER BY created"))
            self.store.connection.executemany("UPDATE collector_refresh SET state='running' WHERE id=?", ((r[0],) for r in claimed))
        wanted, forced_hosts = set(), set()
        for key, targets, created, status in claimed:
            ss, hh = self._targets(json.loads(targets))
            progress=self.refresh_progress.get(key,{'scopes':[],'hosts':[]})
            wanted.update(ss-set(progress['scopes']))
            forced_hosts.update(hh-set(progress['hosts']))
        # Bound local retries without treating them as actual provider service.
        due = [s for s in scopes if s['id'] in wanted or now-self.scope_results.get(s['id'],{}).get('attempted_at',-1e20)>=30]
        # Deferral is not service: keep its place in the fair queue even though
        # attempted_at still throttles local retries to once per poll interval.
        def served_at(scope):
            saved = self.scope_results.get(scope['id'], {})
            return saved.get('served_at', saved.get('attempted_at', -1e20)
                             if saved.get('error') not in ('budget_deferred', 'cycle_budget') else -1e20)
        due.sort(key=lambda s:(served_at(s), s['id']))
        populated = {r['scope_id'] for r in self.store.list('Runner')}
        # Refresh is a fair service lane, not an unbounded priority override.
        lane_order = ('active', 'discovery', 'refresh')
        lanes = {
            # Refresh must not move populated scopes out of their regular
            # lane: repeated refresh-all requests would erase active fairness.
            'active': [s for s in due if s['id'] in populated],
            'discovery': [s for s in due if s['id'] not in populated],
            'refresh': [s for s in due if s['id'] in wanted],
        }
        def following(lane):
            return lane_order[(lane_order.index(lane) + 1) % len(lane_order)]

        # Share bounded attempts between credential references before choosing
        # the oldest unserved scope within a reference. Otherwise a large
        # backed-off credential can hide a healthy one behind the batch limit.
        # Existing persisted attempts supply the restart-safe credential cursor.
        credential_attempts = {}
        for scope in scopes:
            ref = scope['credential_ref']
            attempted = self.scope_results.get(scope['id'], {}).get('attempted_at', -1e20)
            credential_attempts[ref] = max(credential_attempts.get(ref, -1e20), attempted)
        results=[]; deadline=self.monotonic()+self.tick_budget
        lane = self.next_scope_lane
        first_lane = None
        serviced = False
        if hasattr(self.adapter,'begin_cycle'): self.adapter.begin_cycle()
        for _ in range(min(len(due), self.max_scopes)):
            if self.monotonic()>=deadline: break
            while not lanes[lane]:
                lane = following(lane)
            if first_lane is None:
                first_lane = lane
            # min is stable: scopes of a shared credential retain served_at
            # ordering, even after other scope attempts were locally deferred.
            scope = min(lanes[lane], key=lambda s: credential_attempts[s['credential_ref']])
            for queue in lanes.values():
                if scope in queue:
                    queue.remove(scope)
            credential_attempts[scope['credential_ref']] = now
            result=self.adapter.enumerate(scope)
            results.append((scope,result))
            # Try another lane after a deferral, without granting it service.
            # Actual service advances the persisted cursor; local retries must
            # not overwrite that cursor when a shared budget has run out.
            lane = following(lane)
            if result.error not in ('budget_deferred', 'cycle_budget'):
                self.next_scope_lane = lane
                serviced = True
        if first_lane is not None and not serviced:
            # With a one-attempt batch, an indefinitely deferred first lane
            # must still yield a chance to independent credentials next tick.
            self.next_scope_lane = following(first_lane)
        # Advisory job reads use the SAME adapter/cycle budget and only explicit
        # private allowlists. Failures never alter registration/health evidence.
        candidates = [(s, r) for s, r in results if r.complete and self.job_repositories.get(s['id'])]
        candidates.sort(key=lambda pair: self.job_results.get(pair[0]['id'], {}).get('observed_at', 0))
        for scope, inventory in candidates[:self.max_job_scopes]:
            if self.monotonic() >= deadline:
                break
            recent = [row['provider_runner_id'] for row in self.store.list('Runner')
                      if row['scope_id'] == scope['id'] and row['activity'] == 'busy']
            jobs = self.adapter.enrich_jobs(scope, inventory.rows,
                allowed_repositories=self.job_repositories[scope['id']], recent_runner_ids=recent)
            # A cooldown is no new observation. Preserve original timestamps;
            # do not replace useful evidence with an empty advisory result.
            if jobs.error == 'enrichment_cooldown':
                continue
            self.job_results[scope['id']] = dict(rows=list(jobs.rows), complete=jobs.complete,
                error=jobs.error, observed_at=jobs.observed_at)
        probed=self._hosts(now, forced_hosts)
        for key, targets, created, status in claimed:
            progress=self.refresh_progress.setdefault(key,{'scopes':[],'hosts':[]})
            progress['scopes']=sorted(set(progress['scopes'])|{s['id'] for s,r in results})
            progress['hosts']=sorted(set(progress['hosts'])|probed)
        now=self.clock()
        ttl=self.config['thresholds']['inventory_stale_seconds']
        policy=Policy(inventory_ttl=ttl,host_ttl=self.config['thresholds']['host_stale_seconds'])
        with self.store.transaction():
            for host in self.config['hosts']:
                if host['id'] not in probed:
                    continue
                evidence = self.host_results[host['id']]
                oid = ident()
                self.store.put('Observation', dict(
                    id=oid, entity_id=host['resource_id'], dimension='management',
                    value=evidence['management'], reason_code='fixed_host_probe',
                    source_id=host['resource_id'], collected_at=iso(evidence['at']),
                    expires_at=iso(evidence['at']+policy.host_ttl),
                    evidence_summary='Bounded fixed SSH management probe',
                    error_class=None if evidence['management']=='reachable' else 'network'), at=now)
                evidence['observation_ids'] = [oid]
            old = self.store.list('Runner')
            identities = {(r['scope_id'], r['provider_runner_id']): r for r in old}
            coverage, provenance, errors = [], [], []
            for scope, result in results:
                sid = scope['id']
                self.scope_results[sid] = dict(attempted_at=now,
                    served_at=served_at(scope) if result.error in ('budget_deferred', 'cycle_budget') else now,
                    coverage=dict(scope_id=sid, state='complete' if result.complete else 'partial',
                                  enumerated_count=len(result.rows), provider_total_count=result.total_count,
                                  pagination_complete=result.complete, reason_code=result.error),
                    provenance=dict(source_id=sid, collection_id=cycle,
                                    observed_at=iso(result.observed_at), expires_at=iso(result.observed_at+ttl),
                                    state='fresh' if result.complete else 'unavailable', observation_ids=[]),
                    error=result.error, retry_after_seconds=result.retry_after_seconds,
                    complete=result.complete, observed_at=result.observed_at)
                seen = set()
                # Incomplete page sets cannot replace last-known evidence.
                for row in result.rows if result.complete else ():
                    row = deepcopy(row)
                    previous = identities.get((sid, row['provider_runner_id']))
                    row['host_id'] = None
                    if previous:
                        row['id'] = previous['id']
                        row['host_id'] = previous['host_id']
                    rid = row['id']; seen.add(rid)
                    oid = ident()
                    observation = dict(id=oid, entity_id=rid, dimension='registration', value=row['registration'],
                                       reason_code='provider_read', source_id=sid, collected_at=iso(result.observed_at),
                                       expires_at=iso(result.observed_at+ttl), evidence_summary='Complete scope enumeration', error_class=None)
                    row['observation_ids'] = [oid]
                    self.store.put('Observation', observation, at=now)
                    checks, row['readiness']=readiness(self.config,row,self.host_results,now)
                    if checks.at is not None:
                        check_id=ident()
                        self.store.put('Observation',dict(id=check_id,entity_id=rid,dimension='readiness',value=row['readiness'],reason_code='fixed_host_probe',source_id=sid,collected_at=iso(checks.at),expires_at=iso(checks.at+self.config['thresholds']['host_stale_seconds']),evidence_summary='; '.join(checks.failed+checks.warned) or 'Fixed disk, service and configured tool checks',error_class=None),at=now)
                        row['observation_ids'].append(check_id)
                    self.store.put('Runner', row, at=now)
                    policy=Policy(inventory_ttl=ttl,host_ttl=self.config['thresholds']['host_stale_seconds'])
                    state = advance(self.store.load_health(rid), Sample(cycle, result.observed_at, row['registration'], row['activity']), now=now, policy=policy, healthy=row['readiness']=='pass')
                    self.store.save_health(rid, state)
                for row in old:
                    if row['scope_id'] != sid or row['id'] in seen:
                        continue
                    state = advance(self.store.load_health(row['id']), Sample(cycle, result.observed_at, None, complete=result.complete, error=result.error), now=now, policy=policy, healthy=False)
                    self.store.save_health(row['id'], state)
            complete=True
            for scope in scopes:
                sid=scope['id']; saved=self.scope_results.get(sid)
                if saved is None:
                    complete=False
                    coverage.append(dict(scope_id=sid,state='unknown',enumerated_count=0,provider_total_count=None,pagination_complete=False,reason_code='deferred'))
                    continue
                coverage.append(deepcopy(saved['coverage']))
                p=deepcopy(saved['provenance'])
                fresh=0<=now-saved['observed_at']<=ttl
                if p['state']=='fresh' and not fresh: p['state']='stale'
                provenance.append(p)
                complete &= saved['complete'] and fresh
                if saved['error']:
                    errors.append(dict(source_id=sid,code=saved['error'],retry_after_seconds=saved.get('retry_after_seconds'),observed_at=iso(saved['attempted_at'])))
            rows = self.store.list('Runner')
            snapshot = dict(at=now, owner=self.owner(), scope_results=deepcopy(self.scope_results),
                            collection=dict(id=cycle,state='complete' if complete else 'partial',target_ids=[]),
                            host_results=deepcopy(self.host_results),
                            next_scope_lane=self.next_scope_lane,
                            job_results={sid:dict(deepcopy(result), rows=[deepcopy(row) for row in result['rows'] if row['repository'] in self.job_repositories[sid]]) for sid,result in self.job_results.items() if self.job_repositories.get(sid)},
                            job_monitoring_scopes=sorted(sid for sid,repos in self.job_repositories.items() if repos),
                            coverage=coverage, provenance=provenance, source_errors=errors,
                            runners=rows, states={r['id']:asdict(self.store.load_health(r['id'])) for r in rows})
            for key, targets, created, status in claimed:
                ss, hh=self._targets(json.loads(targets))
                progress=self.refresh_progress[key]
                attempted=ss<=set(progress['scopes']) and hh<=set(progress['hosts'])
                if not attempted:
                    # A missing probe implementation is a failed refresh, not an endless queue.
                    if hh and self.host_probe is None:
                        self.store.connection.execute("UPDATE collector_refresh SET state='partial' WHERE id=?",(key,))
                        self.refresh_progress.pop(key,None)
                    continue
                ok=all(self.scope_results[s]['complete'] for s in ss)
                ok &= all(self.host_results[h]['management']=='reachable' for h in hh)
                self.store.connection.execute('UPDATE collector_refresh SET state=? WHERE id=?',('complete' if ok else 'partial',key))
                self.refresh_progress.pop(key,None)
            snapshot['refresh_progress']=deepcopy(self.refresh_progress)
            self.store.connection.execute('INSERT INTO collector_cache VALUES (?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload', ('snapshot',json.dumps(snapshot)))
            self.store.connection.execute('DELETE FROM collector_request_keys WHERE created < ?', (now-86400,))
            self.store.connection.execute("DELETE FROM collector_refresh WHERE created < ? AND state NOT IN ('pending','running')",(now-86400,))
        return snapshot
