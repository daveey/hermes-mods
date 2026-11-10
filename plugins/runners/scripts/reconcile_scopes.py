"""Canonical scope regeneration (dry-run default); stop the collector first.

Use instead of copying historical names into config. Stable scope IDs are kept.
Job/workflow scopes require coordinating their separate execution allowlists.
The collector lifetime lock and persisted provider budgets are shared.
"""
import argparse
import json
from pathlib import Path
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from plugins.runners.canonical import canonical_repository, repoint_scopes, CanonicalError
from plugins.runners.config import ConfigFile
from plugins.runners.collector import Collector, OwnerLock
from plugins.runners.store import Store
from plugins.runners.scripts.run_collector import load_runtime
from plugins.runners.providers.github_actions import GitHubActions, _Failure
from plugins.runners.providers.runtime import GitHubHTTPTransport, BrokerCredential, BrokerCredentialResolver


def reconcile(config, runtime, transport, resolver, *, pause=time.sleep, adapter=None):
    names, changes, errors = {}, [], []
    adapter = adapter or GitHubActions(transport=transport, credential_resolver=resolver, max_cycle_requests=2048)
    adapter.begin_cycle()
    referenced = set(runtime.get('job_repositories', {})) | {w['scope_id'] for w in config['allowed_workflows']}
    for scope in config['scopes']:
        if scope['kind'] != 'repository':
            continue
        try:
            credential = adapter._credential(scope)
            def get(path):
                pause(0.5)
                return adapter._get(credential, path, metadata=True)
            name = canonical_repository(scope['name'], get)
            if name != scope['name']:
                if scope['id'] in referenced:
                    raise CanonicalError('referenced_scope_requires_coordinated_rename')
                names[scope['id']] = name
                changes.append({'id': scope['id'], 'old': scope['name'], 'new': name})
        except _Failure as exc:
            if str(exc) in ('rate_limited', 'backoff', 'cycle_budget', 'unauthorized'):
                raise RuntimeError(str(exc)) from None
            errors.append({'id': scope['id'], 'error': str(exc)})
        except CanonicalError as exc:
            errors.append({'id': scope['id'], 'error': str(exc)})
    return repoint_scopes(config, names), {'changes': changes, 'errors': errors}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    source = ConfigFile(args.config)
    config = source.load()
    runtime = load_runtime(args.runtime)
    resolver = BrokerCredentialResolver({k: BrokerCredential(**v) for k, v in runtime['credentials'].items()},
        broker_command=(runtime.get('broker_executable', 'token-broker'),))
    adapter = GitHubActions(transport=GitHubHTTPTransport(), credential_resolver=resolver, max_cycle_requests=2048)
    identity = tuple(config[k] for k in ('owner_gateway_id', 'owner_profile', 'owner_epoch'))
    state = Path(args.state_dir)
    with OwnerLock(state / 'collector.lock', config, identity), Store(state / 'state.sqlite') as store:
        # Restore and persist exactly the daemon's quota/cooldown/crash fences.
        Collector(store, config, adapter)
        candidate, report = reconcile(config, runtime, adapter.transport, resolver, adapter=adapter)
        if args.apply and report['changes']:
            candidate = source.update(candidate, expected_revision=config['config_revision'],
                                      expected_owner_epoch=config['owner_epoch'])
    report.update(applied=bool(args.apply and report['changes']), revision=candidate['config_revision'])
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
