"""Deterministic evidence bindings; unsupported proof shapes remain UNKNOWN."""
import json
import re
import shlex
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
            or result.get('success') is True
            or type(result.get('exit_code')) is int and result['exit_code'] == 0
            or type(result.get('status_code')) is int and 200 <= result['status_code'] < 300
            or path != '/approve' or '/approve' not in candidate):
        raise ValueError('unproven_current_approval_boundary')
    # Execution options do not identify an operation. A newer result for the
    # same literal command supersedes this boundary, never grants permission.
    keys = list(evidence)
    later = keys[keys.index(p['source_ref'])+1:]
    if any(evidence[k]['role'] == 'user' or (
            evidence[k]['role'] == 'tool' and call(evidence[k])[1].get('command') == action) for k in later):
        raise ValueError('superseded_approval_evidence')


def _capability_operation(row):
    """Only paired calls with one literal command/HTTP URL are supported."""
    paired = row.get('call')
    call_id = row.get('tool_call_id')
    if (row.get('role') != 'tool' or not isinstance(paired, dict)
            or not isinstance(call_id, str) or not call_id
            or paired.get('id') != call_id):
        raise ValueError('unpaired_capability_evidence')
    fn = paired.get('function')
    if (not isinstance(fn, dict) or not isinstance(fn.get('name'), str)
            or not fn['name'].strip()):
        raise ValueError('unsupported_capability_operation')
    _, args = call(row)
    fields = [key for key in ('command', 'url') if key in args]
    if len(fields) != 1:
        raise ValueError('unsupported_capability_operation')
    field = fields[0]
    action = args[field]
    if not isinstance(action, str) or not action.strip() or len(action) > 4000:
        raise ValueError('unsupported_capability_operation')
    if field == 'url':
        parsed = urlsplit(action)
        if (parsed.scheme not in ('http', 'https') or not parsed.netloc
                or parsed.username is not None or parsed.password is not None
                or any(c.isspace() for c in action)):
            raise ValueError('unsupported_capability_operation')
    return field, action


def capability(v, evidence):
    """Prove freshness and literal operation binding, not semantic goal relevance.

    evidence must be the ordered output of evidence_for, which pairs original
    calls/results and retains all subsequent user steering and tool results.
    Unsupported proof formats raise ValueError (the gate records UNKNOWN).
    No operation is executed and no access/approval bypass is authorized here.
    """
    p, source = proof(v, evidence, ('source_ref', 'action'))
    operation = _capability_operation(source)
    result = data(source)
    error = result.get('error')
    status = result.get('status', result.get('status_code'))
    if (p['action'] != operation[1]
            or v.get('boundary_kind') not in ('credentials', 'tool', 'service')
            or not isinstance(error, str)
            or error not in ('missing_credentials', 'permission_denied', 'tool_unavailable',
                             'authentication_required', 'access_denied')
            or result.get('success') is True
            or type(result.get('exit_code')) is int and result['exit_code'] == 0
            or type(status) is int and 200 <= status < 300):
        raise ValueError('missing_current_capability_evidence')
    keys = list(evidence)
    for key in keys[keys.index(p['source_ref']) + 1:]:
        row = evidence[key]
        if row.get('role') == 'user':
            raise ValueError('superseded_capability_evidence')
        if row.get('role') == 'tool':
            # Ignore timeout/options/tool-name differences: another result for
            # the same literal command/URL supersedes the cited failure, even
            # when the newer result is itself a failure or is not cited.
            if _capability_operation(row) == operation:
                raise ValueError('superseded_capability_evidence')


def urls(text):
    if not isinstance(text, str):
        return set()
    return {value.rstrip('.,;!?') for value in re.findall(r'https?://[^\s<>"\'`\[\]()]+' , text)}


def _terminal_http_target(row):
    """Recognize one literal curl readback, never arbitrary successful shell text.

    Canonical shell quoting excludes substitutions, pipelines and appended commands.
    Command recognition is NOT executable provenance: ordinary shells can spoof it.
    This is only HTTP availability evidence, not application/content correctness.
    """
    name, args = call(row)
    command = args.get('command')
    if name != 'terminal' or not isinstance(command, str):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    prefix = ['/usr/bin/curl', '-q', '--noproxy', '*', '--path-as-is',
              '--max-time', '5', '--silent', '--show-error', '--output',
              '/dev/null', '--write-out', '%{http_code}', '--']
    if (len(tokens) != len(prefix) + 1 or tokens[:-1] != prefix
            or command != shlex.join(tokens)):
        return None
    return tokens[-1]


def _http_readback(source, target, scope):
    name, args = call(source)
    result = data(source)
    if name == 'terminal':
        # Do not equate HTTP reachability with broader repair/content goals.
        # The semantic judge cannot widen this deliberately literal contract.
        return (scope == 'Restore HTTP 2xx at ' + target + '.'
                and _terminal_http_target(source) == target
                and args.get("http_readback") is True
                and result.get("execution") == "local-http-readback-v1"
                and not urlsplit(target).fragment
                and args.get('background', False) is False
                and type(result.get('exit_code')) is int and result['exit_code'] == 0
                and not result.get('error') and not result.get('session_id')
                and not result.get('approval_pending')
                and isinstance(result.get('output'), str)
                and re.fullmatch(r'2[0-9]{2}', result['output'].strip()) is not None)
    status = result.get('status', result.get('status_code'))
    return (args.get('url') == target and result.get('url') == target
            and not result.get('error') and result.get('success') is not False
            and type(status) is int and 200 <= status < 300)


def remediation(v, evidence):
    p, source = proof(v, evidence, ('target', 'source_ref', 'scope_ref'))
    target = p['target']
    scope = evidence.get(p['scope_ref'], {})
    parsed = urlsplit(target)
    if (source['role'] != 'tool' or scope.get('role') != 'user'
            or p['scope_ref'] not in v['evidence_refs']
            or not isinstance(scope.get('content'), str)
            or target not in urls(scope['content']) or target not in urls(evidence['candidate'].get('content', ''))
            or parsed.scheme not in ('http', 'https') or not parsed.netloc
            or parsed.username is not None or parsed.password is not None
            or not _http_readback(source, target, scope['content'])):
        raise ValueError('missing_exact_path_readback')
    # Any newer steering starts a new verification epoch. Without a native
    # applicability binding, an earlier readback cannot certify current scope.
    keys = list(evidence)
    for key in keys[keys.index(p['source_ref'])+1:]:
        row = evidence[key]
        if row['role'] == 'user' or (row['role'] == 'tool' and (call(row)[1].get('url') == target
                                     or _terminal_http_target(row) == target)):
            raise ValueError('superseded_path_readback')
