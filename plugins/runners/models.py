"""Strict contract boundary. validate(name, value) returns a detached JSON value.

Validation errors deliberately expose neither values, property names nor jsonschema
exceptions: even a property name or schema name can contain submitted credentials.
Structural validity is not authorization or evidence verification.
"""
from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

_SCHEMA = json.loads((Path(__file__).parent / 'contracts/runners.schema.json').read_text())
_VALIDATORS = {
    name: Draft202012Validator(
        {'$ref': '#/$defs/' + name, '$defs': _SCHEMA['$defs']},
        format_checker=FormatChecker(),
    ) for name in _SCHEMA['$defs']
}


class ModelError(ValueError):
    """Safe public validation failure; contains no submitted data."""
    code = 'invalid_request'


def _json_value(value, ancestors=None):
    ancestors = set() if ancestors is None else ancestors
    if type(value) in (dict, list):
        if id(value) in ancestors:
            return False
        ancestors.add(id(value))
        try:
            if type(value) is dict:
                return all(type(k) is str and _json_value(v, ancestors) for k, v in value.items())
            return all(_json_value(v, ancestors) for v in value)
        finally:
            ancestors.remove(id(value))
    return value is None or type(value) in (str, bool, int, float)


def runner_id(provider, endpoint, scope_id, provider_runner_id):
    """Opaque stable handle; scope IDs must themselves be permanently assigned."""
    import hashlib
    validate('OpaqueID', scope_id)
    import re
    if provider != 'github_actions' or endpoint != 'https://api.github.com' or not isinstance(provider_runner_id, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', provider_runner_id):
        raise ModelError('invalid_identity')
    key = json.dumps([provider, endpoint, scope_id, provider_runner_id], separators=(',', ':'))
    return 'runner_' + hashlib.sha256(key.encode()).hexdigest()


def validate(name, value):
    """Validate any exact $defs name with FormatChecker, returning a deep copy."""
    valid = False
    try:
        if name in _VALIDATORS and _json_value(value):
            json.dumps(value, allow_nan=False)
            valid = _VALIDATORS[name].is_valid(value)
    except (TypeError, ValueError, RecursionError):
        pass
    if not valid:
        raise ModelError('invalid_contract') from None
    return deepcopy(value)
