"""Read-only lifecycle evidence parsers for trusted, fixed probe sources.

No runner-name heuristics, source text, host paths, or journal metadata escape
these parsers. A host being under pressure does not prove a particular runner
was withheld. Missing/denied observations never become a clear state. Source
record time is retained, so a fresh probe cannot freshen an old log message.
"""
import datetime
import json
import math
import os
import re
import stat


# Last whole UTC second representable by datetime/RFC3339. Avoid the
# rounded datetime.max timestamp, which can become year 10000 as a float.
MAX_TIMESTAMP = 253402300799


def valid_timestamp(value, ttl=0):
    """Bound before float conversion/arithmetic, including projected expiry."""
    return (type(value) in (int, float) and 0 <= value <= MAX_TIMESTAMP
            and type(ttl) in (int, float) and 0 <= ttl <= MAX_TIMESTAMP
            and value + ttl <= MAX_TIMESTAMP)


def expected_sources(config):
    pairs = []
    for key, kind, source in (
        ('intentional_drain', 'drained', 'systemd'),
        ('maintenance_marker', 'maintenance', 'maintenance_marker'),
        ('admission_unit', 'disk_pressure', 'admission_journal'),
        ('headroom', 'root_headroom', 'runner_journal'),
        ('supervisor_log', 'label_withdrawn', 'supervisor_log'),
    ):
        if config.get(key): pairs.append((kind, source))
    return pairs


def validate_wire(data, sources):
    if type(data) is not dict or set(data) != set(sources):
        raise ValueError('invalid_lifecycle')
    for ref, rows in data.items():
        expected = set(expected_sources(sources[ref]))
        if type(rows) is not list or len(rows) != len(expected):
            raise ValueError('invalid_lifecycle')
        seen = set()
        for row in rows:
            if type(row) is not dict or set(row) != {'kind', 'source', 'state', 'observed_at', 'recorded_at'}:
                raise ValueError('invalid_lifecycle')
            if any(type(row[key]) is not str for key in ('kind', 'source', 'state')):
                raise ValueError('invalid_lifecycle')
            pair = (row['kind'], row['source'])
            if pair not in expected or pair in seen or row['state'] not in ('held', 'clear', 'unknown'):
                raise ValueError('invalid_lifecycle')
            if row['kind'] == 'label_withdrawn' and row['state'] != 'unknown':
                raise ValueError('invalid_lifecycle')
            seen.add(pair)
            for key in ('observed_at', 'recorded_at'):
                value = row[key]
                if value is not None and not valid_timestamp(value):
                    raise ValueError('invalid_lifecycle')


def evidence(kind, source, state='unknown', at=None, recorded_at=None):
    return dict(kind=kind, source=source, state=state,
                observed_at=at, recorded_at=recorded_at)


def service_evidence(text, now, *, intentional_drain=False):
    result = evidence('drained', 'systemd')
    if not isinstance(text, str) or not intentional_drain:
        return result
    fields = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
    if fields.get('LoadState') != 'loaded':
        return result
    active, enabled = fields.get('ActiveState'), fields.get('UnitFileState')
    if active == 'inactive' and enabled == 'disabled':
        return evidence('drained', 'systemd', 'held', now)
    if active == 'active' or enabled == 'enabled':
        return evidence('drained', 'systemd', 'clear', now)
    return result


def last_journal(text):
    if not isinstance(text, str) or not text.strip():
        return None, None
    try:
        row = json.loads(text.strip().splitlines()[-1])
        if type(row) is not dict or type(row.get('__REALTIME_TIMESTAMP')) not in (str, int, float):
            return None, None
        stamp = float(row['__REALTIME_TIMESTAMP']) / 1000000
        if not valid_timestamp(stamp) or not isinstance(row['MESSAGE'], str):
            return None, None
        return row['MESSAGE'], stamp
    except (ValueError, TypeError, KeyError, OverflowError):
        return None, None


def admission_evidence(text, lane):
    result = evidence('disk_pressure', 'admission_journal')
    message, at = last_journal(text)
    if message is None:
        return result
    try:
        data = json.loads(message)
        if type(data) is not dict or type(data.get('pressure')) is not bool or type(data.get('held')) is not dict:
            return result
        if data['pressure'] is False:
            state = 'clear'
        elif type(data['held'].get(lane)) is int and data['held'][lane] > 0:
            state = 'held'
        else:
            state = 'unknown'
        return evidence('disk_pressure', 'admission_journal', state, at, at)
    except (ValueError, TypeError, KeyError):
        return result


def headroom_evidence(text):
    result = evidence('root_headroom', 'runner_journal')
    message, at = last_journal(text)
    if message is None:
        return result
    # Last entry only: never search backwards and resurrect an old wait.
    if re.fullmatch(r'single-job runner: waiting for [1-9][0-9]* GiB root headroom', message):
        return evidence('root_headroom', 'runner_journal', 'held', at, at)
    return evidence('root_headroom', 'runner_journal', at=at, recorded_at=at)


def marker_evidence(path, now):
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            return evidence('maintenance', 'maintenance_marker')
        return evidence('maintenance', 'maintenance_marker', 'held', now)
    except FileNotFoundError:
        return evidence('maintenance', 'maintenance_marker', 'clear', now)
    except OSError:
        return evidence('maintenance', 'maintenance_marker')


def label_evidence(text, runner, now):
    result = evidence('label_withdrawn', 'supervisor_log')
    if not isinstance(text, str):
        return result
    rows = [line for line in text.splitlines() if line.startswith(runner + ' ')]
    if not rows:
        return result
    match = re.fullmatch(re.escape(runner) + r' saved custom labels: \[[A-Za-z0-9_, .-]+\] withdrawn ([0-9T:Z-]+) by supervisor \(disk emergency\)', rows[-1])
    if not match:
        return result
    try:
        at = datetime.datetime.fromisoformat(match[1].replace('Z', '+00:00'))
        if at.tzinfo is None:
            return result
        stamp = at.timestamp()
        if not math.isfinite(stamp) or stamp < 0:
            return result
        # This append-only event is not proof the label is still withdrawn.
        # UI must call it historical, independently of current provider labels.
        return evidence('label_withdrawn', 'supervisor_log', at=now, recorded_at=stamp)
    except (ValueError, OverflowError):
        return result
