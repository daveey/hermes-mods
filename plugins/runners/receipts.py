"""Read-only verification of a recorded user decision (action_request receipt) for a repair chat.

A relayed "David approved this" is a chat claim and is never evidence. A recorded decision is: the user-steps
store holds the operation (effect, executionAuthorized, frozen request scope). This module only READS it, with a
read-only SQLite connection, and answers: does that receipt authorize management operations for this repair's
subject, and which ones. It cannot create, change or consume a decision, and it never executes anything.
"""
import json
import os
import re
import sqlite3
from pathlib import Path

OPERATION_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
TOOL_KEYS = frozenset({'repair_id', 'operation_id'})
ACTIVE_REPAIR_STATES = ('active', 'verifying')
# Operations a receipt can authorize. diagnose is read-only; service_start needs the scope to say so.
START_WORDING = re.compile(r'\bservice_start\b|\brecover(?:s|ed|y)?\b|\bstart(?:s|ed)? (?:the|that|one) (?:canary|unit|service)\b', re.I)


def store_path():
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        home = Path(get_hermes_home())
    except Exception:
        home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')
    return home / 'user-steps' / 'actions.sqlite3'


def _refuse(reason):
    return dict(verified=False, reason=reason, authorized_operations=[])


def _names_subject(markdown, names):
    for name in names:
        if name and re.search(r'(?<![\w-])' + re.escape(name) + r'(?![\w-])', markdown):
            return True
    return False


def verify_receipt(operation_id, *, store_path, owner_profile, subject_names):
    if not isinstance(operation_id, str) or not OPERATION_ID.match(operation_id):
        return _refuse('invalid_operation_id')
    try:
        c = sqlite3.connect('file:%s?mode=ro' % Path(store_path), uri=True, timeout=5)
        try:
            row = c.execute('SELECT namespace, body FROM operations WHERE id=?', (operation_id,)).fetchone()
        finally:
            c.close()
        if row is None:
            return _refuse('unknown_operation')
        namespace, body = json.loads(row[0]), json.loads(row[1])
        if not (isinstance(namespace, list) and len(namespace) == 2 and namespace[1] == owner_profile):
            return _refuse('unknown_operation')
        intent = body['intent']
        if body.get('operationId') != operation_id:
            return _refuse('unknown_operation')
        if intent.get('effect') != 'approve' or intent.get('executionAuthorized') is not True:
            return _refuse('not_an_authorizing_approval')
        scope = intent['request']['scope']
        markdown = scope['markdown']
        if scope.get('complete') is not True or not isinstance(markdown, str):
            return _refuse('scope_incomplete')
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError):
        return _refuse('receipt_store_unavailable')
    if not _names_subject(markdown, subject_names):
        return _refuse('scope_does_not_name_subject')
    ops = ['diagnose'] + (['service_start'] if START_WORDING.search(markdown) else [])
    return dict(verified=True, reason='verified', authorized_operations=ops, operation_id=operation_id,
                request_id=body.get('requestId'), request_revision=body.get('revision'),
                effect='approve', execution_authorized=True,
                note='Authorizes only the listed runners_operate operations on this repair subject, nothing beyond.')


def receipt_tool(args, *, repair, subject_names, owner_profile, store_path):
    if not isinstance(args, dict):
        return _refuse('invalid_request')
    extra = set(args) - TOOL_KEYS
    if extra:
        return _refuse('unknown_field')
    if args.get('repair_id') != repair.get('id'):
        return _refuse('repair_mismatch')
    if repair.get('state') not in ACTIVE_REPAIR_STATES:
        return _refuse('repair_not_active')
    return verify_receipt(args.get('operation_id'), store_path=store_path, owner_profile=owner_profile,
                          subject_names=subject_names)
