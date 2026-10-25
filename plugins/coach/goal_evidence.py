"""Loss-aware evidence budgeting. Never shorten human scope or call arguments.

The 32k serialized-character limit is unchanged. Tool/memory bodies alone may
be represented by labelled previews; those previews are not proof of closure.
"""
import copy
import hashlib
import json

LIMIT = 32000


def split_user(content):
    """Quarantine the runtime's recalled-memory suffix, not fresh human authority.

    Ambiguous tags fail closed rather than deleting possible user restrictions.
    This is a conservative text boundary, not cryptographic provenance.
    """
    if not isinstance(content, str) or '<memory-context>' not in content:
        return content, None
    start = content.index('<memory-context>')
    suffix = content[start:]
    if (suffix.count('<memory-context>') != 1 or suffix.count('</memory-context>') != 1
            or not suffix.rstrip().endswith('</memory-context>')):
        raise ValueError('ambiguous_memory_provenance')
    return content[:start], suffix


def _shorten(row, limit):
    content = row.get('content')
    text = content if isinstance(content, str) else json.dumps(content)
    if len(text) <= limit:
        return
    previous = row.get('omission')
    row['omission'] = dict(previous) if previous else {
        'original_chars': len(text),
        'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
        'uncertain': True,
        'reason': 'bounded_non_authoritative_body',
    }
    # Prefix is intentionally NOT parsed as structured result data.
    row['content'] = text[:limit]
    row['omission']['retained_chars'] = limit
    row['omission']['omitted_chars'] = row['omission']['original_chars'] - limit


def bound(value):
    """Return an isolated, bounded list/dict or explicit UNKNOWN via ValueError.

    Preserve every row, pair identifier, full call, human turn, and candidate.
    Re-budgeting an already-bounded capture retains its original digest/size.
    The budget is on serialized JSON (including escaping and metadata).
    """
    result = copy.deepcopy(value)
    rows = list(result.values()) if isinstance(result, dict) else result
    if len(rows) > 200:
        raise ValueError('context_limit')
    for limit in (2048, 1024, 512, 128, 0):
        if len(json.dumps(result)) <= LIMIT:
            return result
        for row in rows:
            if row.get('role') in ('tool', 'memory'):
                _shorten(row, limit)
    if len(json.dumps(result)) > LIMIT:
        raise ValueError('context_limit')
    return result
