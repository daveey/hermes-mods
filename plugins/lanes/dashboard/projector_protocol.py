"""Bounded JSON-only private-pipe protocol. No client paths or credentials."""
from __future__ import annotations

import hashlib
import json

VERSION = 1
COMMAND_CAP = 16 * 1024
REPLY_CAP = 8 * 1024 * 1024


def encode(value, cap):
    raw = json.dumps(value, allow_nan=False, separators=(',', ':'), sort_keys=True).encode() + b'\n'
    if len(raw) > cap:
        raise ValueError('lanes RPC size limit')
    return raw


def decode(raw, cap):
    if len(raw) > cap or not raw.endswith(b'\n'):
        raise ValueError('lanes RPC framing')
    value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    if not isinstance(value, dict):
        raise ValueError('lanes RPC object required')
    return value


def digest(value):
    return hashlib.sha256(encode(value, REPLY_CAP)).hexdigest()


def validate_params(params):
    if (not isinstance(params, (list, tuple)) or len(params) != 3 or
            isinstance(params[0], bool) or params[0] not in (1., 6., 24., 168.) or
            params[1] not in ('1h', '6h', '24h', '7d') or type(params[2]) is not bool):
        raise ValueError('invalid projection parameters')


def project_command(session, ident, params, generation, cursor, mode):
    validate_params(params)
    if mode not in ('off', 'primary', 'shadow') or type(generation) is not int:
        raise ValueError('invalid source revision')
    return {'v': VERSION, 'session': session, 'id': ident, 'op': 'project',
            'params': list(params), 'revision': [generation, digest(cursor)], 'mode': mode}


def validate_command(cmd, session):
    if cmd.get('v') != VERSION or cmd.get('session') != session or type(cmd.get('id')) is not int:
        raise ValueError('invalid session or request')
    op = cmd.get('op')
    if op == 'project':
        validate_params(cmd.get('params'))
        revision = cmd.get('revision')
        if (not isinstance(revision, list) or len(revision) != 2 or type(revision[0]) is not int or
                not isinstance(revision[1], str) or len(revision[1]) != 64 or
                any(c not in '0123456789abcdef' for c in revision[1]) or
                cmd.get('mode') not in ('off', 'primary', 'shadow')):
            raise ValueError('invalid revision')
    elif op not in ('observation_cursor', 'stop'):
        raise ValueError('invalid operation')


def validate_result(reply, cmd):
    for key in ('v', 'session', 'id', 'params', 'revision'):
        if reply.get(key) != cmd.get(key):
            raise ValueError('mismatched projection reply')
    view = reply.get('view')
    if (reply.get('op') != 'result' or not isinstance(view, dict) or
            type(view.get('configured')) is not bool):
        raise ValueError('invalid projection view')
    views = reply.get('views')
    if (not isinstance(views, dict) or set(views) != {'collapsed', 'expanded'} or
                              any(not isinstance(v, dict) or type(v.get('configured')) is not bool
                                  for v in views.values()) or
                              views['expanded' if cmd['params'][2] else 'collapsed'] != view):
        raise ValueError('invalid projection variants')
    timing = reply.get('timing', {})
    if not all(type(timing.get(k)) in (int, float) for k in ('project_start', 'project_end')):
        raise ValueError('invalid projection timing')
    if timing['project_end'] < timing['project_start'] or type(reply.get('projector_pid')) is not int:
        raise ValueError('invalid projection timing or pid')
    encode(reply, REPLY_CAP)  # also rejects nonfinite values throughout the view
    return reply
