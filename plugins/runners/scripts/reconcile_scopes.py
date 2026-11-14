"""Guarded repository reconciliation (dry-run default); stop the collector first.

Renames retain stable IDs. Deletions require two real metadata 404s, complete
same-credential org listings, and a completed intervening bounded collector sweep.
Discovery is opt-in per organization. All maintenance shares the collector lock
and persisted provider budgets. No execution allowlist is changed here.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from plugins.runners.canonical import canonical_repository, repoint_scopes, CanonicalError
from plugins.runners.config import ConfigFile, normalize
from plugins.runners.collector import Collector, OwnerLock
from plugins.runners.store import Store
from plugins.runners.scripts.run_collector import load_runtime
from plugins.runners.providers.github_actions import GitHubActions, _Failure
from plugins.runners.providers.runtime import BrokerCredential, BrokerVendGitHub
from plugins.runners.scripts.maintenance_schedule import MaintenanceSchedule, REVISIT
from plugins.runners.providers.maintenance import MaintenanceGitHub

INTERVAL = 3600
MAX_PAGES = 100
MAX_REMOVALS = 25


def organization_repositories(owner, get, *, owner_type='orgs'):
    """Bounded complete listing; never follow provider-supplied URLs."""
    if owner_type not in ('orgs', 'users'):
        raise CanonicalError('invalid_owner_type')
    rows, ids, names = [], set(), set()
    for page in range(1, MAX_PAGES + 1):
        path = f'/{owner_type}/{owner}/repos?per_page=100&page={page}'
        response = get(path)
        if response.status != 200 or type(response.body) is not list or len(response.body) > 100:
            raise CanonicalError('invalid_org_listing')
        for row in response.body:
            if (type(row) is not dict or type(row.get('id')) is not int or row['id'] <= 0
                    or not GitHubActions._name(row.get('full_name'), 2)
                    or row['full_name'].split('/')[0].lower() != owner.lower()
                    or row['id'] in ids or row['full_name'].lower() in names):
                raise CanonicalError('invalid_org_listing')
            ids.add(row['id']); names.add(row['full_name'].lower()); rows.append(row)
        link = {k.lower(): v for k, v in response.headers.items()}.get('link')
        next_page = False
        if link is not None:
            relations = {}
            if not isinstance(link, str) or not link:
                raise CanonicalError('invalid_org_pagination')
            for part in link.split(','):
                route = '(?:' + owner_type + '/' + re.escape(owner) + '|' + (
                    'organizations' if owner_type == 'orgs' else 'user') + r'/[1-9][0-9]*)'
                match = re.fullmatch(r'\s*<https://api\.github\.com/' + route +
                    r'/repos\?per_page=100&page=([1-9][0-9]*)>;\s*rel="(next|last|prev|first)"\s*', part)
                if not match or match[2] in relations or int(match[1]) > MAX_PAGES:
                    raise CanonicalError('invalid_org_pagination')
                target = int(match[1])
                relations[match[2]] = target
                if ((match[2] == 'next' and target != page + 1)
                        or (match[2] == 'last' and target < page)
                        or (match[2] == 'prev' and target != page - 1)
                        or (match[2] == 'first' and target != 1)):
                    raise CanonicalError('invalid_org_pagination')
                next_page |= match[2] == 'next'
            if relations.get('last', page) > page and not next_page:
                raise CanonicalError('invalid_org_pagination')
        # A full page always needs another locally constructed request, even if
        # Link is absent. Short pages with next links are followed as well.
        if len(response.body) < 100 and not next_page:
            return rows
    raise CanonicalError('org_listing_page_limit')


def _owner(config):
    return {k: config[k] for k in ('owner_gateway_id', 'owner_profile', 'owner_epoch', 'config_revision')}


def _intervening_sweep(config, snapshot, old, first, now):
    """Require service of every configured scope since the first confirmation.

    `collection.state` also demands successful reads inside the inventory TTL;
    a deleted scope (or a large bounded rotation) can never satisfy that flag.
    Missing/budget/auth failures cannot substitute for a completed sweep. A
    not_found collector result may be cached; only the two fresh metadata 404s
    and complete owner listings in reconcile() authorize a deletion.
    """
    if not isinstance(snapshot, dict):
        return False
    collection = snapshot.get('collection', {})
    at = snapshot.get('at')
    if (not isinstance(collection, dict) or not collection.get('id')
            or collection['id'] == old.get('collection_id')
            or snapshot.get('owner') != _owner(config)
            or type(at) not in (int, float) or not math.isfinite(at)
            or not first < at < now):
        return False
    rows = snapshot.get('scope_results')
    if not isinstance(rows, dict):
        return False
    for scope in config['scopes']:
        row = rows.get(scope['id'])
        if not isinstance(row, dict):
            return False
        served = row.get('served_at')
        if (type(served) not in (int, float) or not math.isfinite(served)
                or not first < served <= at):
            return False
        if not (row.get('error') == 'not_found'
                or (row.get('error') is None and row.get('complete') is True)):
            return False
    return True


def reconcile(config, runtime, transport, resolver, *, pause=time.sleep, adapter=None,
              evidence=None, snapshot=None, now=None, interval=INTERVAL, discover_org=(), schedule=None, apply_due=True):
    if type(interval) not in (int, float) or not math.isfinite(interval) or interval <= 0:
        raise ValueError('invalid_confirmation_interval')
    now = time.time() if now is None else now
    for owner, ref in discover_org:
        if not GitHubActions._name(owner, 1) or not isinstance(ref, str) or not ref:
            raise ValueError('invalid_discovery_organization')
    names, changes, errors, pending, additions, removals = {}, [], [], [], [], []
    evidence = {} if evidence is None else evidence
    previous = deepcopy(evidence)
    if schedule is None:
        evidence.clear()
    scheduler = MaintenanceSchedule(config, runtime, schedule, now) if schedule is not None else None
    adapter = adapter or GitHubActions(transport=transport, credential_resolver=resolver, max_cycle_requests=2048)
    adapter.begin_cycle()
    referenced = set(runtime.get('job_repositories', {})) | {w['scope_id'] for w in config['allowed_workflows']}
    listings = {}
    canonical_names = set()

    def get(credential, path):
        if scheduler is None:
            pause(0.5)
        return adapter._get(credential, path, metadata=True)

    def listing(owner, credential, ref, *, deletion=False):
        key = (owner.lower(), ref, credential.key, deletion)
        if key not in listings:
            try:
                fetch = lambda p: get(credential, p)
                first_path = f'/orgs/{owner}/repos?per_page=100&page=1'
                first = fetch(first_path)
                if deletion and first.status == 404:
                    account = fetch('/users/' + owner)
                    if account.status != 200 or type(account.body) is not dict or account.body.get('type') != 'User':
                        raise CanonicalError('unverified_repository_owner')
                    listings[key] = organization_repositories(owner, fetch, owner_type='users')
                else:
                    listings[key] = organization_repositories(owner, lambda p: first if p == first_path else fetch(p))
            except (CanonicalError, _Failure) as exc:
                listings[key] = exc
        result = listings[key]
        if isinstance(result, Exception):
            raise result
        return result

    def failure(scope_id, exc):
        if scheduler is None and isinstance(exc, _Failure) and str(exc) in ('rate_limited', 'backoff', 'cycle_budget', 'unauthorized'):
            raise RuntimeError(str(exc)) from None
        errors.append({'id': scope_id, 'error': str(exc)})

    def reconcile_scope(scope):
        if scope['kind'] != 'repository':
            return
        before_remaining = adapter._cycle_remaining
        try:
            if not GitHubActions._name(scope['name'], 2):
                raise CanonicalError('invalid_repository')
            credential = adapter._credential(scope)
            response = get(credential, '/repos/' + scope['name'])
            if response.status != 404:
                evidence.pop(scope['id'], None)
                # Canonical validation still controls the one numeric 301 hop.
                name = canonical_repository(scope['name'], lambda p: response if p == '/repos/' + scope['name'] else get(credential, p))
                canonical_names.add(name.lower())
                if name != scope['name']:
                    if scope['id'] in referenced:
                        raise CanonicalError('referenced_scope_requires_coordinated_rename')
                    names[scope['id']] = name
                    changes.append({'id': scope['id'], 'old': scope['name'], 'new': name})
                return
            if scope['id'] in referenced:
                raise CanonicalError('referenced_scope_requires_coordinated_removal')
            owner = scope['name'].split('/')[0]
            rows = listing(owner, credential, scope['credential_ref'], deletion=True)
            if scope['name'].lower() in {r['full_name'].lower() for r in rows}:
                raise CanonicalError('repository_present_in_org_listing')
            identity = dict(owner=_owner(config), scope=deepcopy(scope), credential_key=credential.key)
            old = previous.get(scope['id'], {})
            first = old.get('at')
            valid = (old.get('identity') == identity and type(first) in (float, int)
                     and math.isfinite(first) and 0 <= now - first <= max(86400, interval * 4))
            if not valid:
                old = dict(identity=identity, at=now,
                           collection_id=(snapshot or {}).get('collection', {}).get('id'))
                first = now
            evidence[scope['id']] = old
            intervened = _intervening_sweep(config, snapshot, old, first, now)
            row = dict(id=scope['id'], name=scope['name'])
            if valid and now - first >= interval and intervened:
                removals.append(row)
            else:
                pending.append(dict(row, reason='confirmation_interval_or_bounded_sweep'))
        except (_Failure, CanonicalError) as exc:
            if not (scheduler and isinstance(exc, _Failure) and (
                    adapter._cycle_remaining == before_remaining or str(exc) in
                    ('cycle_budget', 'pacing', 'backoff', 'rate_limited'))):
                evidence.pop(scope['id'], None)
            failure(scope['id'], exc)

    selected_discovery = []
    if scheduler is None:
        for scope in config['scopes']:
            reconcile_scope(scope)
        selected_discovery = discover_org
    else:
        jobs = []
        live_ids = {scope['id'] for scope in config['scopes']}
        for scope_id in set(evidence) - live_ids:
            del evidence[scope_id]
        for scope in config['scopes']:
            if scope['kind'] != 'repository':
                continue
            key = scheduler.key('repository', scope)
            old = previous.get(scope['id'], {})
            first = old.get('at')
            priority = apply_due and (scheduler.pending(key) or (
                type(first) in (int, float) and now - first >= interval
                and _intervening_sweep(config, snapshot, old, first, now)))
            if scheduler.due(key, priority=priority):
                jobs.append((not priority, scheduler.last(key), key, 'repository', scope))
        for owner, ref in discover_org:
            key = scheduler.key('discovery', dict(name=owner, credential_ref=ref))
            priority = apply_due and scheduler.pending(key)
            if scheduler.due(key, priority=priority):
                jobs.append((not priority, scheduler.last(key), key, 'discovery', (owner, ref)))
        attempted = 0
        blocked = set()
        for _, _, key, kind, item in sorted(jobs):
            if adapter._cycle_remaining <= 0:
                break
            ref = item['credential_ref'] if kind == 'repository' else item[1]
            try:
                credential_key = adapter._credential(dict(credential_ref=ref)).key
            except _Failure:
                credential_key = 'ref:' + ref
            if credential_key in blocked:
                continue
            before = adapter._cycle_remaining
            change_start = len(changes) + len(removals)
            error_start = len(errors)
            if kind == 'repository':
                reconcile_scope(item)
            else:
                owner, ref = item
                try:
                    credential = adapter._credential(dict(credential_ref=ref))
                    listing(owner, credential, ref)
                    selected_discovery.append(item)
                except (_Failure, CanonicalError) as exc:
                    failure(owner, exc)
            # Admission failures remain due and preserve existing evidence.
            interrupted = any(row['error'] in ('cycle_budget', 'pacing') for row in errors[error_start:])
            if any(row['error'] in ('pacing', 'backoff', 'rate_limited', 'unauthorized',
                                     'network_error', 'credential_unavailable') for row in errors[error_start:]):
                blocked.add(credential_key)
            if adapter._cycle_remaining != before:
                attempted += 1
                # A listing larger than this cycle must not monopolize every
                # future cycle. Retain only its advisory hint; no partial list
                # can authorize discovery or deletion.
                hint = scheduler.pending(key) if interrupted else len(changes) + len(removals) > change_start
                if kind == 'discovery' and item in selected_discovery:
                    # A hint to repeat the complete listing, never authorization.
                    known = {s['name'].lower() for s in config['scopes'] if s['kind'] == 'repository'}
                    hint = any(r['full_name'].lower() not in known for r in
                               listings[(item[0].lower(), ref, credential_key, False)])
                scheduler.attempted(key, pending=hint, interrupted=interrupted)
        scheduler.prune()

    candidate = repoint_scopes(config, names)
    existing = canonical_names | {s['name'].lower() for s in candidate['scopes'] if s['kind'] == 'repository'}
    for owner, ref in selected_discovery:
        if not GitHubActions._name(owner, 1) or not isinstance(ref, str) or not ref:
            raise ValueError('invalid_discovery_organization')
        try:
            credential = adapter._credential(dict(credential_ref=ref))
            for repo in listing(owner, credential, ref):
                name = repo['full_name']
                if name.lower() in existing:
                    continue
                new_id = 'scope_' + hashlib.sha256(('repository:' + name).encode()).hexdigest()[:32]
                if new_id in {s['id'] for s in candidate['scopes']}:
                    # A previous repository with this name may retain its ID
                    # after a rename. Never recycle that existing identity.
                    new_id = 'scope_' + hashlib.sha256(('repository:' + name + ':' + str(repo['id'])).encode()).hexdigest()[:32]
                scope = dict(id=new_id,
                             provider='github_actions', endpoint='https://api.github.com', kind='repository',
                             name=name, credential_ref=ref, permissions=[dict(action=action, state='not_checked', observation_id=None)
                                 for action in ('read_inventory', 'read_jobs', 'dispatch', 'manage')])
                candidate['scopes'].append(scope)
                additions.append(dict(id=scope['id'], name=name)); existing.add(name.lower())
        except (_Failure, CanonicalError) as exc:
            failure(owner, exc)
    if len(removals) > MAX_REMOVALS:
        errors.append(dict(error='removal_cap_exceeded', count=len(removals)))
        pending.extend(dict(row, reason='removal_cap_exceeded') for row in removals)
        removals = []
    removed = {r['id'] for r in removals}
    candidate['scopes'] = [s for s in candidate['scopes'] if s['id'] not in removed]
    report = dict(changes=changes, additions=additions, removals=removals, pending=pending, errors=errors)
    if scheduler:
        report['maintenance'] = dict(due=len(jobs), attempted=attempted,
                                     deferred=len(jobs) - attempted, apply_due=apply_due)
    return normalize(candidate), report


def _save_evidence(path, evidence):
    fd, name = tempfile.mkstemp(prefix='.scope-confirmations-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(evidence, stream, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirmation-interval', type=float, default=INTERVAL)
    parser.add_argument('--discover-org', action='append', default=[], metavar='OWNER=CREDENTIAL_REF')
    args = parser.parse_args(argv)
    discovery = []
    for entry in args.discover_org:
        owner, separator, ref = entry.partition('=')
        if not separator or not GitHubActions._name(owner, 1) or not ref:
            parser.error('invalid --discover-org; expected OWNER=CREDENTIAL_REF')
        discovery.append((owner, ref))
    source = ConfigFile(args.config)
    config = source.load()
    runtime = load_runtime(args.runtime)
    # Every GET runs inside an open broker vend: the GitHub quota ledger records it
    # and the shared reserve/brake can refuse it (providers/runtime.py).
    github = BrokerVendGitHub({k: BrokerCredential(**v) for k, v in runtime['credentials'].items()},
        broker_command=(runtime.get('broker_executable', 'token-broker'),))
    try:
        report = _run(args, source, config, runtime, github, discovery)
    finally:
        github.close()
    print(json.dumps(report, indent=2))
    return 0


def _run(args, source, config, runtime, github, discovery):
    resolver = github
    adapter = MaintenanceGitHub(transport=github.transport, credential_resolver=resolver, clock=lambda: time.time())
    identity = tuple(config[k] for k in ('owner_gateway_id', 'owner_profile', 'owner_epoch'))
    state = Path(args.state_dir)
    with OwnerLock(state / 'collector.lock', config, identity), Store(state / 'state.sqlite') as store:
        Collector(store, config, adapter)  # Restore daemon quota/cooldown/crash fences.
        path = state / 'scope-confirmations.json'
        try:
            evidence = json.loads(path.read_text()) if path.exists() else {}
            if type(evidence) is not dict or any(type(v) is not dict for v in evidence.values()):
                evidence = {}
        except (ValueError, OSError):
            evidence = {}
        schedule_path = state / 'scope-maintenance.json'
        try:
            maintenance = json.loads(schedule_path.read_text())
            if type(maintenance) is not dict or type(maintenance.get('visits')) is not dict:
                maintenance = {'visits': {}}
        except (ValueError, OSError):
            maintenance = {'visits': {}}
        now = time.time()
        last_apply = maintenance.get('last_apply')
        apply_due = not (type(last_apply) in (int, float) and math.isfinite(last_apply)
                         and now - min(now, last_apply) < REVISIT)
        row = store.connection.execute("SELECT payload FROM collector_cache WHERE key='snapshot'").fetchone()
        snapshot = json.loads(row[0]) if row else None
        candidate, report = reconcile(config, runtime, adapter.transport, resolver, adapter=adapter,
            evidence=evidence, snapshot=snapshot, interval=args.confirmation_interval, discover_org=discovery,
            schedule=maintenance['visits'], now=now, apply_due=apply_due)
        _save_evidence(path, evidence)
        changed = any(report.get(k) for k in ('changes', 'additions', 'removals'))
        applied = bool(args.apply and changed and apply_due)
        if applied:
            # Fence first: a crash after CAS must not permit another hourly apply.
            maintenance['last_apply'] = now
            _save_evidence(schedule_path, maintenance)
            candidate = source.update(candidate, expected_revision=config['config_revision'],
                                      expected_owner_epoch=config['owner_epoch'], backup=True)
        _save_evidence(schedule_path, maintenance)
    report.update(applied=applied, revision=candidate['config_revision'])
    return report


if __name__ == '__main__':
    raise SystemExit(main())
