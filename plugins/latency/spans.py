"""Privacy-restricted, best-effort span capture and durable JSONL admission.

``root`` is a Hermes home, not the cache directory. Batch validation happens
before any filesystem mutation. Writers share an advisory process/thread lock.
Three JSONL segments total, at most 4 MiB each, are retained for seven days.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import socket
import time
import uuid

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_SEGMENTS = 3
RETENTION_SECONDS = 7 * 86400
MAX_DURATION_MS = 90 * 86400 * 1000
_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]*\Z')
_ALLOWED_ATTRS = frozenset({'session', 'card', 'pr', 'run', 'phase', 'profile', 'board'})
_LOG = logging.getLogger(__name__)


def _token(value, field, limit):
    if not isinstance(value, str) or not value or len(value) > limit or not _TOKEN.fullmatch(value):
        raise ValueError(field + ' must be a safe identifier')
    # Even an otherwise token-shaped scheme cannot carry a URL or credentials.
    if '://' in value:
        raise ValueError(field + ' must not be a URL')
    return value


def _number(value, field):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(field + ' must be a finite number')
    try:
        value = float(value)
    except (ValueError, OverflowError):
        raise ValueError(field + ' must be a finite number') from None
    if not math.isfinite(value):
        raise ValueError(field + ' must be a finite number')
    return value


def validate_span(value, now=None):
    """Normalize a client span; reject unknown fields rather than retain secrets.

    A missing event ID is the SHA256 of the canonical normalized event, making
    an identical retried payload replay-safe. Explicit IDs are safe opaque tokens.
    """
    from . import otel
    if isinstance(value, dict) and 'name' in value:
        value = otel.to_legacy(value)
    context_fields = {'trace_id', 'span_id', 'parent_span_id', 'start_time_unix_nano', 'end_time_unix_nano', 'service_name'}
    if not isinstance(value, dict) or set(value) - ({'id', 'metric', 'start', 'dur_ms', 'host', 'gateway', 'attrs'} | context_fields):
        raise ValueError('span must be an object with only supported fields')
    now = time.time() if now is None else _number(now, 'now')
    start = _number(value.get('start'), 'start')
    duration = _number(value.get('dur_ms'), 'dur_ms')
    if not now - 3600 <= start <= now + 60:
        raise ValueError('start must be within the last hour and at most 60 seconds in the future')
    if not 0 <= duration <= MAX_DURATION_MS:
        raise ValueError('dur_ms must be between zero and 90 days')
    attrs = value.get('attrs', {})
    if not isinstance(attrs, dict) or set(attrs) - _ALLOWED_ATTRS:
        raise ValueError('attrs must contain only session/card/pr/run/phase/profile/board identifiers')
    normalized = {
        'metric': _token(value.get('metric'), 'metric', 100),
        'start': start, 'dur_ms': duration,
        'host': _token(value.get('host'), 'host', 100),
        'gateway': _token(value.get('gateway'), 'gateway', 100),
        'attrs': {key: _token(item, 'attrs.' + key, 256) for key, item in sorted(attrs.items())},
    }
    if 'id' in value:
        event_id = _token(value['id'], 'id', 256)
    else:
        event_id = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    result = {'id': event_id, **normalized}
    if context_fields & value.keys():
        result['trace_id'] = otel.identifier(value.get('trace_id'), 32)
        result['span_id'] = otel.identifier(value.get('span_id'), 16)
        if 'parent_span_id' in value:
            result['parent_span_id'] = otel.identifier(value['parent_span_id'], 16)
        start_ns = otel.nanos(value.get('start_time_unix_nano'))
        end_ns = otel.nanos(value.get('end_time_unix_nano'))
        if abs(start_ns / 1e9 - start) > .000001 or abs((end_ns-start_ns) / 1e6 - duration) > .001:
            raise ValueError('inconsistent OpenTelemetry timing')
        if value.get('service_name') not in ('hermes-desktop', 'hermes-gateway'):
            raise ValueError('unsupported service name')
        result.update(start_time_unix_nano=str(start_ns), end_time_unix_nano=str(end_ns), service_name=value['service_name'])
    return otel.with_ids(result)


def _home():
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except (ImportError, AttributeError):
        return Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes')))


def append_batch(samples, root=None):
    """Validate a complete batch, then append it under a durable locked write.

    Validation and a too-large batch fail without touching the filesystem.
    I/O failures propagate to callers (the span context manager swallows them).
    """
    now = time.time()
    normalized = [validate_span(sample, now=now) for sample in samples]
    payload = b''.join((json.dumps(sample, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode() for sample in normalized)
    if len(payload) > MAX_FILE_BYTES:
        raise ValueError('span batch exceeds segment capacity')
    if not normalized:
        return []
    folder = (Path(root) if root is not None else _home()) / 'cache' / 'latency'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (folder / '.spans.lock').open('a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        active = folder / ('spans-' + datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%d') + '.jsonl')
        for path in folder.glob('spans-*.jsonl'):
            if path.stat().st_mtime < now - RETENTION_SECONDS:
                path.unlink()
        if active.exists() and active.stat().st_size + len(payload) > MAX_FILE_BYTES:
            oldest = active.with_name(active.stem + '.2.jsonl')
            oldest.unlink(missing_ok=True)
            previous = active.with_name(active.stem + '.1.jsonl')
            if previous.exists():
                previous.replace(oldest)
            active.replace(previous)
        with active.open('a+b') as stream:
            os.chmod(active, 0o600)
            original = stream.tell()
            try:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                stream.seek(original)
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
                raise
        others = sorted((p for p in folder.glob('spans-*.jsonl') if p != active),
                        key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
        for path in others[MAX_SEGMENTS - 1:]:
            path.unlink()
        directory = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return normalized


def _local_token(value):
    value = re.sub(r'[^A-Za-z0-9_.:-]', '-', value)[:100].lstrip('.:-')
    return value or 'unknown'


@contextmanager
def span(metric, **attrs):
    """Measure monotonic elapsed time without telemetry masking body errors.

    Only identifier attributes are accepted; invalid telemetry is discarded.
    No result, exception text, prompt, tool body, or query is ever captured.
    """
    start, monotonic_start = time.time(), time.monotonic()
    trace_id = attrs.pop('trace_id', uuid.uuid4().hex)
    span_id = uuid.uuid4().hex[:16]
    parent = attrs.pop('parent_span_id', None)
    context = dict(trace_id=trace_id, span_id=span_id)
    if parent is not None:
        context['parent_span_id'] = parent
    try:
        yield context
    finally:
        try:
            host = _local_token(socket.gethostname())
            duration = (time.monotonic() - monotonic_start) * 1000
            start_ns = round(start * 1e9)
            append_batch([{
                'id': str(uuid.uuid4()), 'metric': metric, 'start': start,
                'dur_ms': duration, **context,
                'start_time_unix_nano': str(start_ns),
                'end_time_unix_nano': str(start_ns + round(duration * 1e6)),
                'service_name': 'hermes-gateway',
                'host': host, 'gateway': _local_token(os.environ.get('HERMES_GATEWAY_ID', host)),
                'attrs': attrs,
            }])
        except Exception:
            # Do not log supplied values or exception strings: either could carry secrets.
            try:
                _LOG.warning('Latency span could not be recorded')
            except Exception:
                pass
