"""Durable person dependencies, independent of core's transient outcome declarations.

One atomic JSON snapshot per session; a cross-process lock serializes tool writers.
Readers see complete snapshots, never truncate a label into the canonical record.
"""
from contextlib import contextmanager, closing
import fcntl
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import tempfile


def home():
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except ImportError:
        return Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')


def root(session):
    """Follow only compression parents, with read errors propagated (never empty)."""
    db = home() / 'state.db'
    if not db.exists():
        return session
    with closing(sqlite3.connect(f'file:{db}?mode=rw', uri=True, timeout=2)) as conn:
        conn.execute('PRAGMA query_only=ON')
        seen = {session}
        for _ in range(64):
            row = conn.execute('select parent_session_id from sessions where id=?', (session,)).fetchone()
            if not row or not row[0]:
                return session
            parent = conn.execute('select end_reason from sessions where id=?', (row[0],)).fetchone()
            if not parent or 'compress' not in str(parent[0] or '').lower():
                return session
            if row[0] in seen:
                raise ValueError('cyclic compression lineage')
            session = row[0]
            seen.add(session)
    raise ValueError('compression lineage exceeds bound')


def path(session):
    return home() / 'session-outcome' / 'people' / (hashlib.sha256(session.encode()).hexdigest() + '.json')


def read(session):
    session = root(session)
    try:
        data = json.loads(path(session).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return []
    if data.get('session') != session or not isinstance(data.get('records'), list):
        raise ValueError('invalid person dependency ledger')
    return data['records']


@contextmanager
def edit(session):
    session = root(session)
    target = path(session)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with target.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = read(session)
        yield records


def save(session, records):
    session = root(session)
    target = path(session)
    fd, temporary = tempfile.mkstemp(prefix=target.stem + '-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump({'version': 1, 'session': session, 'records': records}, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def open_records(session):
    return [r for r in read(session) if r['state'] in ('waiting', 'verifying')]


def projection(record):
    phase = 'Verifying' if record['state'] == 'verifying' else 'Waiting on'
    return {'kind': 'goal', 'id': record['id'], 'state': record['state'],
            'label': f"{phase} {record['contact']} ({record['channel']} {record['thread']}): "
                     f"{record['response']}. Next: {record['next_step']}"}
