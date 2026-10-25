"""Generic validated record storage, not an authority or health reducer.

Store(path): explicit local SQLite WAL database, context-managed close.
put(kind, document, *, key=None, at=None): validate an exact contract definition;
key defaults to document.id (Transition requires an explicit opaque key). Timestamp
is a numeric UTC epoch; default uses time.time. Replaces one record atomically.
get(kind,key)/list(kind,limit=1000): detached documents. transaction(): composable
atomic batch with nested savepoints. Connections are thread-affine; use one Store
per thread/process. Active status is derived, never supplied by a caller.
prune(now=...): observations/transitions/resolved incidents 30d, terminal job/repair
audits 90d, tombstones 30d. Other records and active work never expire. Retention
starts at the last actual put, not a read. No TTL-based dispatch retries.
retire_runner(id,at=...): replace registration with compact identity tombstone;
ephemeral/JIT raw observations and their transitions are compacted immediately.
Audits/active incidents are retained independently. tombstone(id) reads that marker.

Schema v1 is additive from v0 (unversioned databases). Existing databases receive
an SQLite online backup before migration; backup_path exposes it. Unknown future
schemas refuse writers, including already-open instances. No automatic downgrade.
"""
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .models import validate

SCHEMA_VERSION = 1
DAY = 86400


class StoreError(RuntimeError):
    pass


def _timestamp(value):
    value = time.time() if value is None else value
    if type(value) not in (int, float) or not math.isfinite(value):
        raise StoreError('invalid_timestamp')
    return value


def _active(kind, value):
    if kind == 'Job':
        return value['status'] not in ('concluded', 'cancelled')
    if kind == 'Repair':
        return value['state'] not in ('closed', 'failed')
    if kind == 'Incident':
        return value['resolved_at'] is None
    return kind not in ('Observation', 'Transition')


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes')) / 'runners' / 'state.sqlite'
        self.backup_path = None
        self._depth = 0
        self._serial = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = os.open(str(self.path) + '.migration.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            existed = self.path.exists() and self.path.stat().st_size > 0
            self.connection = sqlite3.connect(self.path, isolation_level=None, timeout=10)
            try:
                version = self.connection.execute('PRAGMA user_version').fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise StoreError('future_schema')
                os.chmod(self.path, 0o600)
                if version < SCHEMA_VERSION:
                    if existed:
                        self.backup_path = self.path.with_name(self.path.name + '.pre-v1-' + uuid.uuid4().hex + '.bak')
                        fd = os.open(self.backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                        os.close(fd)
                        with sqlite3.connect(self.backup_path) as backup:
                            self.connection.backup(backup)
                        os.chmod(self.backup_path, 0o600)
                    self._migrate(version)
                self.connection.execute('PRAGMA journal_mode=WAL')
                self.connection.execute('PRAGMA synchronous=FULL')
                self.connection.execute('PRAGMA foreign_keys=ON')
                os.chmod(self.path, 0o600)
            except BaseException:
                self.connection.close()
                raise
        finally:
            os.close(lock)

    def _migrate(self, version):
        if version != 0:
            raise StoreError('unsupported_schema')
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            self.connection.execute('''CREATE TABLE runner_records (
                kind TEXT NOT NULL, record_key TEXT NOT NULL, payload TEXT NOT NULL,
                updated_at REAL NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)),
                PRIMARY KEY(kind,record_key))''')
            self.connection.execute('CREATE INDEX runner_retention ON runner_records(kind,active,updated_at)')
            self.connection.execute('''CREATE TABLE runner_tombstones (
                record_key TEXT PRIMARY KEY, payload TEXT NOT NULL, retired_at REAL NOT NULL)''')
            self.connection.execute('CREATE TABLE health_states (runner_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            self.connection.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            self.connection.execute('COMMIT')
        except BaseException:
            self.connection.execute('ROLLBACK')
            raise

    def save_health(self, runner_id, state):
        from dataclasses import asdict
        from .health import State
        validate('OpaqueID', runner_id)
        if not isinstance(state, State):
            raise StoreError('invalid_health_state')
        with self.transaction():
            self.connection.execute('INSERT INTO health_states VALUES (?,?) ON CONFLICT(runner_id) DO UPDATE SET payload=excluded.payload',
                                    (runner_id, json.dumps(asdict(state), allow_nan=False)))

    def load_health(self, runner_id):
        from .health import State
        row = self.connection.execute('SELECT payload FROM health_states WHERE runner_id=?', (runner_id,)).fetchone()
        return State(**json.loads(row[0])) if row else State()

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def transaction(self):
        """BEGIN IMMEDIATE outer write transaction; inner scopes are savepoints."""
        self._serial += 1
        savepoint = f'nested_{self._serial}'
        outer = self._depth == 0
        self.connection.execute('BEGIN IMMEDIATE' if outer else f'SAVEPOINT {savepoint}')
        self._depth += 1
        try:
            version = self.connection.execute('PRAGMA user_version').fetchone()[0]
            if version != SCHEMA_VERSION:
                raise StoreError('future_schema' if version > SCHEMA_VERSION else 'unsupported_schema')
            yield self
            self.connection.execute('COMMIT' if outer else f'RELEASE SAVEPOINT {savepoint}')
        except BaseException:
            if outer:
                self.connection.execute('ROLLBACK')
            else:
                self.connection.execute(f'ROLLBACK TO SAVEPOINT {savepoint}')
                self.connection.execute(f'RELEASE SAVEPOINT {savepoint}')
            raise
        finally:
            self._depth -= 1

    def put(self, kind, document, *, key=None, at=None):
        value = validate(kind, document)
        if not isinstance(value, dict):
            raise StoreError('record_requires_object')
        key = value.get('id') if key is None else key
        if key is None:
            raise StoreError('record_requires_key')
        if 'id' in value and key != value['id']:
            raise StoreError('identity_mismatch')
        if kind == 'Host':
            validate('Owner', dict(gateway_id=key, profile='validation', owner_epoch=1, config_revision=1))
        else:
            validate('OpaqueID', key)
        stamp = _timestamp(at)
        with self.transaction():
            if kind == 'Runner' and self.tombstone(key) is not None:
                raise StoreError('retired_identity')
            self.connection.execute('''INSERT INTO runner_records VALUES (?,?,?,?,?)
                ON CONFLICT(kind,record_key) DO UPDATE SET payload=excluded.payload,
                updated_at=excluded.updated_at,active=excluded.active''',
                (kind, key, json.dumps(value, allow_nan=False), stamp, int(_active(kind, value))))
        return value

    def get(self, kind, key):
        row = self.connection.execute('SELECT payload FROM runner_records WHERE kind=? AND record_key=?', (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, kind, *, limit=1000):
        if type(limit) is not int or not 0 <= limit <= 10000:
            raise StoreError('invalid_limit')
        return [json.loads(row[0]) for row in self.connection.execute(
            'SELECT payload FROM runner_records WHERE kind=? ORDER BY record_key LIMIT ?', (kind, limit))]

    def tombstone(self, runner_id):
        row = self.connection.execute('SELECT payload FROM runner_tombstones WHERE record_key=?', (runner_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def retire_runner(self, runner_id, *, at=None):
        stamp = _timestamp(at)
        with self.transaction():
            runner = self.get('Runner', runner_id)
            if runner is None:
                if self.tombstone(runner_id) is not None:
                    return self.tombstone(runner_id)  # idempotent; never refresh TTL
                raise StoreError('not_found')
            for kind, payload in self.connection.execute("SELECT kind,payload FROM runner_records WHERE active=1 AND kind IN ('Job','Repair')"):
                value = json.loads(payload)
                if (kind == 'Job' and value['assigned_runner_id'] == runner_id) or (kind == 'Repair' and value['entity_id'] == runner_id):
                    raise StoreError('active_work')
            marker = {k: runner[k] for k in ('id', 'provider', 'endpoint', 'scope_id', 'provider_runner_id', 'lifecycle', 'replacement_of')}
            marker['retired_at'] = stamp
            self.connection.execute('INSERT INTO runner_tombstones VALUES (?,?,?)', (runner_id, json.dumps(marker), stamp))
            self.connection.execute("DELETE FROM runner_records WHERE kind='Runner' AND record_key=?", (runner_id,))
            if runner['lifecycle'] in ('ephemeral', 'jit'):
                ids = []
                for key, payload in self.connection.execute("SELECT record_key,payload FROM runner_records WHERE kind='Observation'"):
                    if json.loads(payload)['entity_id'] == runner_id:
                        ids.append(key)
                transitions = []
                observation_ids = set(ids)
                for key, payload in self.connection.execute("SELECT record_key,payload FROM runner_records WHERE kind='Transition'"):
                    if json.loads(payload)['observation_id'] in observation_ids:
                        transitions.append(key)
                self.connection.executemany("DELETE FROM runner_records WHERE kind='Observation' AND record_key=?", ((key,) for key in ids))
                self.connection.executemany("DELETE FROM runner_records WHERE kind='Transition' AND record_key=?", ((key,) for key in transitions))
            return marker

    def prune(self, *, now=None, observations_days=30, audit_days=90, retired_days=30):
        stamp = _timestamp(now)
        for value in (observations_days, audit_days, retired_days):
            if type(value) is not int or value < 1:
                raise StoreError('invalid_retention')
        with self.transaction():
            removed = 0
            for kinds, days in [(('Observation', 'Transition', 'Incident'), observations_days), (('Job', 'Repair'), audit_days)]:
                marks = ','.join('?' for _ in kinds)
                removed += self.connection.execute(
                    f'DELETE FROM runner_records WHERE active=0 AND kind IN ({marks}) AND updated_at < ?',
                    (*kinds, stamp - days * DAY)).rowcount
            self.connection.execute('DELETE FROM health_states WHERE runner_id IN (SELECT record_key FROM runner_tombstones WHERE retired_at < ?)', (stamp - retired_days * DAY,))
            removed += self.connection.execute('DELETE FROM runner_tombstones WHERE retired_at < ?', (stamp - retired_days * DAY,)).rowcount
            return removed
