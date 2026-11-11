"""Guarded repository reconciliation (dry-run default); stop the collector first.

Renames retain stable IDs. Deletions require two real metadata 404s, complete
same-credential org listings, and a completed intervening collector snapshot.
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
from plugins.runners.providers.runtime import GitHubHTTPTransport, BrokerCredential, BrokerCredentialResolver

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


def reconcile(config, runtime, transport, resolver, *, pause=time.sleep, adapter=None,
              evidence=None, snapshot=None, now=None, interval=INTERVAL, discover_org=()):
    if type(interval) not in (int, float) or not math.isfinite(interval) or interval <= 0:
        raise ValueError('invalid_confirmation_interval')
    now = time.time() if now is None else now
    names, changes, errors, pending, additions, removals = {}, [], [], [], [], []
    evidence = {} if evidence is None else evidence
    previous = deepcopy(evidence); evidence.clear()
    adapter = adapter or GitHubActions(transport=transport, credential_resolver=resolver, max_cycle_requests=2048)
    adapter.begin_cycle()
    referenced = set(runtime.get('job_repositories', {})) | {w['scope_id'] for w in config['allowed_workflows']}
    listings = {}
    canonical_names = set()

    def get(credential, path):
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
        if isinstance(exc, _Failure) and str(exc) in ('rate_limited', 'backoff', 'cycle_budget', 'unauthorized'):
            raise RuntimeError(str(exc)) from None
        errors.append({'id': scope_id, 'error': str(exc)})

    for scope in config['scopes']:
        if scope['kind'] != 'repository':
            continue
        try:
            if not GitHubActions._name(scope['name'], 2):
                raise CanonicalError('invalid_repository')
            credential = adapter._credential(scope)
            response = get(credential, '/repos/' + scope['name'])
            if response.status != 404:
                # Canonical validation still controls the one numeric 301 hop.
                name = canonical_repository(scope['name'], lambda p: response if p == '/repos/' + scope['name'] else get(credential, p))
                canonical_names.add(name.lower())
                if name != scope['name']:
                    if scope['id'] in referenced:
                        raise CanonicalError('referenced_scope_requires_coordinated_rename')
                    names[scope['id']] = name
                    changes.append({'id': scope['id'], 'old': scope['name'], 'new': name})
                continue
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
            collection = (snapshot or {}).get('collection', {})
            snap_at = (snapshot or {}).get('at')
            expected_owner = _owner(config)
            intervened = (collection.get('state') == 'complete' and collection.get('id')
                          and collection['id'] != old.get('collection_id')
                          and (snapshot or {}).get('owner') == expected_owner
                          and type(snap_at) in (int, float) and first < snap_at < now)
            row = dict(id=scope['id'], name=scope['name'])
            if valid and now - first >= interval and intervened:
                removals.append(row)
            else:
                pending.append(dict(row, reason='confirmation_interval_or_collector_snapshot'))
        except (_Failure, CanonicalError) as exc:
            failure(scope['id'], exc)

    candidate = repoint_scopes(config, names)
    existing = canonical_names | {s['name'].lower() for s in candidate['scopes'] if s['kind'] == 'repository'}
    for owner, ref in discover_org:
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
    return normalize(candidate), dict(changes=changes, additions=additions, removals=removals, pending=pending, errors=errors)


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
    resolver = BrokerCredentialResolver({k: BrokerCredential(**v) for k, v in runtime['credentials'].items()},
        broker_command=(runtime.get('broker_executable', 'token-broker'),))
    adapter = GitHubActions(transport=GitHubHTTPTransport(), credential_resolver=resolver, max_cycle_requests=2048)
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
        row = store.connection.execute("SELECT payload FROM collector_cache WHERE key='snapshot'").fetchone()
        snapshot = json.loads(row[0]) if row else None
        candidate, report = reconcile(config, runtime, adapter.transport, resolver, adapter=adapter,
            evidence=evidence, snapshot=snapshot, interval=args.confirmation_interval, discover_org=discovery)
        _save_evidence(path, evidence)
        changed = any(report.get(k) for k in ('changes', 'additions', 'removals'))
        if args.apply and changed:
            candidate = source.update(candidate, expected_revision=config['config_revision'],
                                      expected_owner_epoch=config['owner_epoch'], backup=True)
    report.update(applied=bool(args.apply and changed), revision=candidate['config_revision'])
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
