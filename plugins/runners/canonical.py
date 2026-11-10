"""Canonical repository names for config generation and rename reconciliation.

Only a 301 to GitHub's numeric repository metadata endpoint is admissible.
Never follow Location, arbitrary response URLs, or redirects in the HTTP client.
"""
import re
from .providers.github_actions import GitHubActions


class CanonicalError(ValueError):
    pass


def canonical_repository(name, get):
    """Return canonical full_name using at most two injected GETs (no mutation)."""
    if not GitHubActions._name(name, 2):
        raise CanonicalError('invalid_repository')
    response = get('/repos/' + name)
    repo_id = None
    if response.status == 301:
        url = response.body.get('url') if type(response.body) is dict else None
        match = re.fullmatch(r'https://api\.github\.com/repositories/([1-9][0-9]*)', url or '') if isinstance(url, str) else None
        if not match:
            raise CanonicalError('unsafe_repository_redirect')
        repo_id = int(match[1])
        response = get('/repositories/' + match[1])
    if response.status != 200 or type(response.body) is not dict:
        raise CanonicalError('repository_lookup_failed')
    body = response.body
    if type(body.get('id')) is not int or body['id'] <= 0 or (repo_id is not None and body['id'] != repo_id):
        raise CanonicalError('repository_identity_mismatch')
    if not GitHubActions._name(body.get('full_name'), 2):
        raise CanonicalError('invalid_repository')
    return body['full_name']


def repoint_scopes(config, names):
    """Copy names by stable scope ID, fail closed on duplicate destinations.

    Does not change IDs, permissions, bindings, targets, or workflow allowlists.
    Callers must reconcile any separate job allowlists before applying a rename.
    """
    from copy import deepcopy
    from .config import normalize
    candidate = deepcopy(config)
    for scope in candidate['scopes']:
        if scope['id'] in names:
            if scope['kind'] != 'repository' or not GitHubActions._name(names[scope['id']], 2):
                raise CanonicalError('invalid_repository')
            scope['name'] = names[scope['id']]
    return normalize(candidate)
