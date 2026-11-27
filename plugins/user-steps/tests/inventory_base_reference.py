"""Frozen pre-attribution inventory path from da74b589 (PR463 base).

Keep the original bare-lock/cache/decoder algorithm independent of attribution,
including overhead added when tracing is disabled. Both timing arms use the
separately tested GIL-preserving digest scheduling; its deliberate extra hashing
calls are not attribution overhead. Freeze that scheduling too, independently of
production, so later helper overhead still appears in the comparison.
"""
import hashlib
import json
import threading
from collections import OrderedDict

_INVENTORY = OrderedDict()
_INVENTORY_LOCK = threading.Lock()
_INVENTORY_MAX = 65536


def _object(raw):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('object required')
    return value


def _inventory_digest(raw):
    raw = raw if isinstance(raw, bytes) else raw.encode()
    if len(raw) <= 2047:
        return hashlib.sha256(raw).digest()
    digest = hashlib.sha256()
    view = memoryview(raw)
    for offset in range(0, len(raw), 2047):
        digest.update(view[offset:offset + 2047])
    return digest.digest()


def _inventory(raw, key, gateway, profile, linked_sessions, storage_type=None):
    digest = _inventory_digest(raw)
    cache_key = (digest, storage_type, key, gateway, profile, linked_sessions)
    with _INVENTORY_LOCK:
        value = _INVENTORY.get(cache_key)
        if value is not None:
            _INVENTORY.move_to_end(cache_key)
            return value
    if storage_type == 'text':
        raw = raw.decode('utf-8')
    task = _object(raw)
    parts = key.split('/')
    if len(parts) not in (2, 3) or not all(parts) or task.get('id') != parts[-1]:
        raise ValueError('unknown replica identity')
    value = ((parts[-2], parts[-1]), task.get('created_at'),
             frozenset(linked_sessions(task, gateway, profile)))
    with _INVENTORY_LOCK:
        _INVENTORY[cache_key] = value
        _INVENTORY.move_to_end(cache_key)
        while len(_INVENTORY) > _INVENTORY_MAX:
            _INVENTORY.popitem(last=False)
    return value
