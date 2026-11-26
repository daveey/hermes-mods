"""Content-verified, process-local projection of dismissal events.

Keep only (timestamp, rule), not historical prompts or session payloads. Validate
all retained prefix bytes before incremental reuse: size/mtime alone cannot prove
append-only history. No TTL, log truncation, persisted index or background work.
"""
from collections import OrderedDict
import hashlib
import math
from pathlib import Path
import threading
import json

_lock = threading.Lock()
_entries = OrderedDict()
_MAX_PATHS = 8


def _project(raw):
    result = []
    # Match read_log's UTF-8 decode / splitlines semantics and malformed-JSON skip.
    for line in raw.decode('utf-8').splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            return None
        ts = row.get('ts', 0)
        if not isinstance(ts, (int, float)) or not math.isfinite(ts):
            return None  # preserve legacy comparison errors via the caller
        if row.get('event') == 'choice' and row.get('choice') == 'dismissed':
            rule = row.get('rule')
            if rule is not None and not isinstance(rule, (str, int, float, bool)):
                return None
            result.append((ts, rule))
    return tuple(result)


def read(path: Path):
    """Return dismissal projection, or None to ask for the unchanged legacy path."""
    key = str(path.absolute())
    with _lock:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            _entries.pop(key, None)
            return ()
        old = _entries.get(key)
        start = 0
        rows = ()
        if old is not None:
            size, digest, retained = old
            if len(raw) >= size and hashlib.sha256(memoryview(raw)[:size]).digest() == digest:
                start, rows = size, retained
        # Never retain an unterminated final line: later bytes may invalidate it.
        boundary = raw.rfind(b'\n') + 1
        appended = _project(raw[start:boundary])
        tail = _project(raw[boundary:])
        if appended is None or tail is None:
            _entries.pop(key, None)
            return None
        rows += appended
        _entries[key] = boundary, hashlib.sha256(memoryview(raw)[:boundary]).digest(), rows
        _entries.move_to_end(key)
        while len(_entries) > _MAX_PATHS:
            _entries.popitem(last=False)
        return rows + tail
