"""Loss-aware evidence budgeting. Never shorten human scope or call arguments.

The 32k serialized-character limit is unchanged. Tool/memory bodies alone may
be represented by labelled previews; those previews are not proof of closure.
"""
import copy
import hashlib
import json

import importlib.util
from pathlib import Path
_spec = importlib.util.spec_from_file_location('_coach_goal_images', Path(__file__).with_name('goal_images.py'))
_images = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_images)

LIMIT = 32000


def user_content(content, *, canonical=False):
    """Keep canonical native content verbatim; ambiguous legacy tags fail closed.

    ``canonical`` comes from scope recovery's native hook/sidecar projection,
    never from tag spelling or an input row's self-declared trust label.
    """
    if canonical or not isinstance(content, str) or '<memory-context>' not in content:
        return content
    # Text alone cannot distinguish runtime recall from human-authored limits.
    # Legacy rows without a native split must still stay UNKNOWN.
    raise ValueError('ambiguous_memory_provenance')


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
        if _images.serialized_size(result) <= LIMIT:
            return result
        for row in rows:
            if row.get('role') in ('tool', 'memory'):
                _shorten(row, limit)
    if _images.serialized_size(result) > LIMIT:
        raise ValueError('context_limit')
    return result
