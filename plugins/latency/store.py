"""Bounded, replay-safe latency aggregation. All durations are milliseconds.

Positive values use logarithmic buckets with ratio 1.005 (upper-edge error
at most 0.5%); zero has its own bucket. Queries select whole UTC hours
intersecting [since, until), then label those hours in the requested timezone.
Only identities and the slowest 20 events per metric/UTC day retain raw data.
"""
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

RETENTION_SECONDS = 90 * 86400
_LOG_RATIO = math.log(1.005)
_ZERO = -1000000


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name + ' must be a finite number')
    try:
        value = float(value)
    except (OverflowError, ValueError):
        raise ValueError(name + ' must be a finite number') from None
    if not math.isfinite(value):
        raise ValueError(name + ' must be a finite number')
    return value


def _sample(sample):
    if not isinstance(sample, dict):
        raise ValueError('sample must be an object')
    result = {key: sample.get(key) for key in ('id', 'metric', 'host', 'gateway')}
    for key, value in result.items():
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise ValueError(key + ' must be a nonempty string')
    result['start'] = _number(sample.get('start'), 'start')
    result['dur_ms'] = _number(sample.get('dur_ms'), 'dur_ms')
    if result['dur_ms'] < 0:
        raise ValueError('dur_ms must be nonnegative')
    attrs = sample.get('attrs', {})
    if not isinstance(attrs, dict) or set(attrs) - {'session', 'card', 'pr', 'run', 'phase'}:
        raise ValueError('unsupported attrs')
    if any(not isinstance(v, str) or len(v) > 256 for v in attrs.values()):
        raise ValueError('attrs must be short strings')
    result['attrs'] = dict(attrs)
    return result


def _bucket(value):
    if value == 0:
        return _ZERO
    bucket = math.ceil(math.log(value) / _LOG_RATIO)
    # log/exp roundoff near a boundary must never turn an upper edge into
    # an underestimate. Advancing a bucket still stays below 1% error.
    if _edge(bucket) < value:
        bucket += 1
    return bucket


def _edge(bucket):
    if bucket == _ZERO:
        return 0.0
    try:
        return math.exp(bucket * _LOG_RATIO)
    except OverflowError:
        return float.fromhex('0x1.fffffffffffffp+1023')


def _stats(hist):
    n = sum(hist.values())
    result = dict(n=n, p50=None, p90=None, p99=None)
    if n:
        ordered = sorted(hist.items())
        for label, percentile in (('p50', 50), ('p90', 90), ('p99', 99)):
            rank = (n * percentile + 99) // 100
            total = 0
            for bucket, count in ordered:
                total += count
                if total >= rank:
                    result[label] = _edge(bucket)
                    break
    return result


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute('PRAGMA journal_mode=WAL').fetchone()
            db.executescript('''
                CREATE TABLE IF NOT EXISTS identities(id TEXT PRIMARY KEY, start REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS identities_start ON identities(start);
                CREATE TABLE IF NOT EXISTS hist(metric TEXT, host TEXT, hour INTEGER,
                    bucket INTEGER, n INTEGER NOT NULL, PRIMARY KEY(metric,host,hour,bucket));
                CREATE INDEX IF NOT EXISTS hist_hour ON hist(hour);
                CREATE TABLE IF NOT EXISTS slow(id TEXT PRIMARY KEY, metric TEXT, day INTEGER,
                    start REAL, dur REAL, sample TEXT);
                CREATE INDEX IF NOT EXISTS slow_group ON slow(metric,day,dur);
                CREATE TABLE IF NOT EXISTS cursors(source TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);
            ''')

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(str(self.path), timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, samples, source=None, cursor=None):
        samples = [_sample(s) for s in samples]
        encoded_cursor = json.dumps(cursor, allow_nan=False)
        inserted = 0
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            groups = set()
            for sample in samples:
                if not db.execute('INSERT OR IGNORE INTO identities VALUES (?,?)',
                                  (sample['id'], sample['start'])).rowcount:
                    continue
                inserted += 1
                hour = math.floor(sample['start'] / 3600) * 3600
                day = math.floor(sample['start'] / 86400) * 86400
                db.execute('''INSERT INTO hist VALUES (?,?,?,?,1)
                    ON CONFLICT(metric,host,hour,bucket) DO UPDATE SET n=n+1''',
                           (sample['metric'], sample['host'], hour, _bucket(sample['dur_ms'])))
                db.execute('INSERT INTO slow VALUES (?,?,?,?,?,?)',
                           (sample['id'], sample['metric'], day, sample['start'],
                            sample['dur_ms'], json.dumps(sample, allow_nan=False)))
                groups.add((sample['metric'], day))
            for metric, day in groups:
                db.execute('''DELETE FROM slow WHERE metric=? AND day=? AND id NOT IN
                    (SELECT id FROM slow WHERE metric=? AND day=? ORDER BY dur DESC,id LIMIT 20)''',
                           (metric, day, metric, day))
            if source is not None:
                db.execute('INSERT OR REPLACE INTO cursors VALUES (?,?)', (source, encoded_cursor))
        return inserted

    def cursor(self, source):
        with self._connection() as db:
            row = db.execute('SELECT value FROM cursors WHERE source=?', (source,)).fetchone()
        return json.loads(row[0]) if row else None

    def meta_get(self, key, default=None):
        with self._connection() as db:
            row = db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def meta_set(self, key, value):
        encoded = json.dumps(value, allow_nan=False)
        with self._connection() as db:
            db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', (key, encoded))

    def prune(self, now):
        cutoff = _number(now, 'now') - RETENTION_SECONDS
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for table, field in (('identities', 'start'), ('slow', 'start')):
                db.execute(f'DELETE FROM {table} WHERE {field} < ?', (cutoff,))
            # Keep the boundary hour: it can contain observations newer than cutoff.
            db.execute('DELETE FROM hist WHERE hour + 3600 <= ?', (cutoff,))

    def view(self, metric, since, until, timezone='America/Los_Angeles'):
        since, until = _number(since, 'since'), _number(until, 'until')
        if until < since:
            raise ValueError('until must not precede since')
        zone = ZoneInfo(timezone)
        lower = math.floor(since / 3600) * 3600
        with self._connection() as db:
            rows = db.execute('SELECT hour,bucket,n FROM hist WHERE metric=? AND hour>=? AND hour<?',
                              (metric, lower, until if until > since else lower)).fetchall()
            slow = db.execute('''SELECT sample FROM slow WHERE metric=? AND start>=? AND start<?
                ORDER BY dur DESC,id LIMIT 20''', (metric, since, until)).fetchall()
        total, days, hours = defaultdict(int), defaultdict(lambda: defaultdict(int)), defaultdict(lambda: defaultdict(int))
        for hour, bucket, n in rows:
            total[bucket] += n
            days[datetime.fromtimestamp(hour, zone).date().isoformat()][bucket] += n
            hours[hour][bucket] += n
        return {**_stats(total),
                'series': [{'day': day, **_stats(hist)} for day, hist in sorted(days.items())],
                'hours': [{'hour': hour, **_stats(hist)} for hour, hist in sorted(hours.items())],
                'slowest': [json.loads(row[0]) for row in slow]}
