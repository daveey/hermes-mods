"""Durable project workflow ledger. This database is NOT a native task database."""
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import sqlite3
import threading
import uuid
from decimal import Decimal, InvalidOperation


def message_order(value):
    try:
        return (0, Decimal(value))
    except InvalidOperation:
        return (1, value)  # Non-Slack test/transport identifiers.


def followup_text(text):
    if not isinstance(text, str):
        raise ValueError('Text must be a string')
    return text if text.strip() else '[Non-text human follow-up: inspect the original Slack message and attachments.]'

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY, team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                    data TEXT NOT NULL, UNIQUE(team_id, channel_id, thread_id));
                CREATE TABLE IF NOT EXISTS inbound (
                    team_id TEXT NOT NULL, channel_id TEXT NOT NULL,
                    message_id TEXT NOT NULL, workflow_id TEXT NOT NULL,
                    context TEXT NOT NULL, text TEXT NOT NULL,
                    UNIQUE(team_id, channel_id, message_id));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def lock(self):
        # Serialize native effects across threads AND processes, not just SQL writes.
        key = str(self.path)
        with _LOCKS_GUARD:
            lock = _LOCKS.setdefault(key, threading.Lock())
        with lock, open(key + '.lock', 'a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def get(self, workflow_id):
        with self.connect() as db:
            record = db.execute('SELECT data FROM workflows WHERE workflow_id=?',
                                (workflow_id,)).fetchone()
        if record is None:
            raise KeyError(workflow_id)
        return json.loads(record[0])

    def save(self, row):
        with self.connect() as db:
            db.execute('UPDATE workflows SET data=? WHERE workflow_id=?',
                       (json.dumps(row, sort_keys=True), row['workflow_id']))

    def rows(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT data FROM workflows')]

    def seen(self, ctx):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM inbound WHERE team_id=? AND channel_id=? AND message_id=?',
                              (ctx.team_id, ctx.channel_id, ctx.message_id)).fetchone() is not None

    def record(self, ctx, text, workflow_id):
        from dataclasses import asdict
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO inbound VALUES (?,?,?,?,?,?)',
                (ctx.team_id, ctx.channel_id, ctx.message_id, workflow_id, json.dumps(asdict(ctx)), text))

    def receive(self, context, text, policy, approved=False, followup=False):
        """Caller holds lock. Atomic inbound uniqueness and generation transition."""
        team, channel = context['team_id'], context['channel_id']
        thread = context['thread_id'] or context['message_id']
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            duplicate = db.execute('SELECT workflow_id FROM inbound WHERE team_id=? AND channel_id=? AND message_id=?',
                                   (team, channel, context['message_id'])).fetchone()
            if duplicate:
                return self.get(duplicate[0]), False
            found = db.execute('SELECT data FROM workflows WHERE team_id=? AND channel_id=? AND thread_id=?',
                               (team, channel, thread)).fetchone()
            if found:
                row = json.loads(found[0])
                if row['policy'] != policy:
                    raise PermissionError('Stored policy differs from configured policy')
                snapshot = {k: v for k, v in row.items() if k not in ('history', 'text', 'source_ids', 'approval_attempts')}
                row.setdefault('history', []).append(snapshot)
                row.update(generation=row['generation'] + 1, cards={}, evidence={},
                           result_text=None, result_receipt=None, state='pending', error=None,
                           prompt_state=None, approved=False, plan=None, plan_hash=None)
            else:
                if followup:
                    raise KeyError('No workflow for this thread')
                if context['thread_id'] and context['thread_id'] != context['message_id']:
                    raise ValueError('Start new project work in a root channel message')
                row = dict(workflow_id=str(uuid.uuid4()), team_id=team, channel_id=channel,
                           thread_id=thread, policy=policy, generation=1, approved=approved,
                           session_id=None, stored_session_id=None, cards={}, evidence={},
                           result_text=None, result_receipt=None, state='pending', error=None,
                           prompt_state=None, session_state=None, plan=None, plan_hash=None)
                row['title'] = 'Project work ' + row['workflow_id']
            chain = row.get('text', '')
            text = followup_text(text)
            row.setdefault('source_ids', []).append(context['message_id'])
            row.update(context=context, text=(chain + '\n\nFollow-up:\n' + text if chain else text),
                       message_id=max(row.get('message_id', context['message_id']),
                                      context['message_id'], key=message_order))
            db.execute('INSERT INTO inbound VALUES (?,?,?,?,?,?)',
                       (team, channel, context['message_id'], row['workflow_id'], json.dumps(context), text))
            db.execute('INSERT INTO workflows VALUES (?,?,?,?,?) ON CONFLICT(workflow_id) DO UPDATE SET data=excluded.data',
                       (row['workflow_id'], team, channel, thread, json.dumps(row, sort_keys=True)))
            return row, True
