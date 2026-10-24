"""Deterministic evidence bindings; unsupported proof shapes remain UNKNOWN."""
import json
import re
from urllib.parse import urlsplit


def data(row):
    value = row.get('content')
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def call(row):
    fn = row.get('call', {}).get('function', {})
    args = fn.get('arguments', {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    return fn.get('name'), args if isinstance(args, dict) else {}


def proof(v, evidence, fields):
    p = v.get('proof')
    if (not isinstance(p, dict) or set(p) != set(fields)
            or any(not isinstance(x, str) or not x or len(x) > 4000 for x in p.values())
            or p['source_ref'] not in v['evidence_refs']
            or p['source_ref'] not in evidence):
        raise ValueError('missing_disposition_proof')
    return p, evidence[p['source_ref']]


def authorization(v, evidence):
    p, source = proof(v, evidence, ('action', 'source_ref', 'approval_path'))
    action, path = p['action'], p['approval_path']
    candidate = evidence['candidate'].get('content', '')
    if not isinstance(candidate, str) or action not in candidate:
        raise ValueError('boundary_action_not_reported')
    users = [key for key, row in evidence.items() if row['role'] == 'user']
    if source['role'] == 'user':
        # Narrow literal restrictions only; no quoted/hypothetical substring match.
        # Older directives cannot certify a boundary after newer steering.
        text = source.get('content')
        if (p['source_ref'] != users[-1] or not isinstance(text, str)
                or not re.fullmatch(r'(?:Do not|Never) ' + re.escape(action) + r'[.!]?', text.strip())
                or v['boundary_kind'] not in ('denial', 'scope')
                or path != 'user_explicit_direction'
                or 'explicit user direction' not in candidate):
            raise ValueError('unproven_current_user_boundary')
        return
    name, args = call(source)
    result = data(source)
    # The native terminal emits these outer fields before execution; text printed
    # by a command/fetched page is not this trusted executor result envelope.
    if (source['role'] != 'tool' or name != 'terminal' or args.get('command') != action
            or v['boundary_kind'] != 'approval'
            or result.get('status') != 'pending_approval'
            or result.get('approval_pending') is not True or result.get('command') != action
            or path != '/approve' or '/approve' not in candidate):
        raise ValueError('unproven_current_approval_boundary')
    keys = list(evidence)
    later = keys[keys.index(p['source_ref'])+1:]
    if any(evidence[k]['role'] == 'user' or (
            evidence[k]['role'] == 'tool' and call(evidence[k]) == (name, args)) for k in later):
        raise ValueError('superseded_approval_evidence')


def urls(text):
    if not isinstance(text, str):
        return set()
    return {value.rstrip('.,;!?') for value in re.findall(r'https?://[^\s<>"\'`\[\]()]+' , text)}


def remediation(v, evidence):
    p, source = proof(v, evidence, ('target', 'source_ref', 'scope_ref'))
    target = p['target']
    scope = evidence.get(p['scope_ref'], {})
    parsed = urlsplit(target)
    name, args = call(source)
    result = data(source)
    if (source['role'] != 'tool' or scope.get('role') != 'user'
            or p['scope_ref'] not in v['evidence_refs']
            or not isinstance(scope.get('content'), str)
            or target not in urls(scope['content']) or target not in urls(evidence['candidate'].get('content', ''))
            or parsed.scheme not in ('http', 'https') or not parsed.netloc
            or parsed.username is not None or parsed.password is not None
            or args.get('url') != target or result.get('url') != target
            or result.get('error') or result.get('success') is False
            or type(result.get('status', result.get('status_code'))) is not int
            or not 200 <= result.get('status', result.get('status_code')) < 300):
        raise ValueError('missing_exact_path_readback')
    # A later failed read of this target invalidates an earlier success citation.
    keys = list(evidence)
    for key in keys[keys.index(p['source_ref'])+1:]:
        row = evidence[key]
        if row['role'] == 'tool' and call(row)[1].get('url') == target:
            raise ValueError('superseded_path_readback')
