"""Narrow Unix-socket client; the socket is never mounted into worker sandboxes."""
import json
import os
from pathlib import Path
import socket
import stat
import sqlite3
import threading
import logging
import time
from contextlib import contextmanager


class DurableIngressQueue:
    """Independent local journal: never hold its DB lock across a network call.

    At-least-once delivery relies on service event/message deduplication. Failed
    deliveries remain durable, including uncertain socket timeout outcomes.
    """
    def __init__(self, path, client, retry_interval=1):
        self.path, self.client = Path(path), client
        self.retry_interval = retry_interval
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.worker = None
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = self.path.parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or self.path.parent.resolve() != self.path.parent.absolute()):
            raise PermissionError('Ingress directory must be private and real')
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise PermissionError('Ingress journal must be owner-only')
        finally:
            os.close(fd)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS pending (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, retry_at REAL NOT NULL DEFAULT 0)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=0.2)
        try:
            with db:
                yield db
        finally:
            db.close()

    def append(self, operation, **arguments):
        payload = json.dumps({'operation': operation, 'arguments': arguments})
        if len(payload.encode()) + 1 > MAX_BYTES:
            raise ValueError('Request too large')
        with self.connect() as db:
            db.execute('INSERT INTO pending(payload) VALUES (?)', (payload,))
        self.wake.set()

    def drain_once(self):
        with self.connect() as db:
            row = db.execute('SELECT id,payload FROM pending WHERE retry_at <= ? ORDER BY id LIMIT 1', (time.time(),)).fetchone()
        if row is None:
            return False
        event = json.loads(row[1])
        try:
            self.client.call(event['operation'], **event['arguments'])
        except Exception:
            # One rejected command must not permanently starve later messages.
            with self.connect() as db:
                db.execute('UPDATE pending SET retry_at=? WHERE id=?',
                           (time.time() + self.retry_interval, row[0]))
            raise
        with self.connect() as db:
            db.execute('DELETE FROM pending WHERE id=?', (row[0],))
        return True

    def start(self):
        if self.worker is not None:
            return
        def run():
            while not self.stop.is_set():
                self.wake.clear()
                try:
                    if self.drain_once():
                        continue
                except Exception:
                    logging.getLogger(__name__).warning('Ingress delivery deferred; journal retained')
                    continue  # Drain other due rows before waiting for this retry.
                self.wake.wait(self.retry_interval)
        self.worker = threading.Thread(target=run, name='project-work-ingress', daemon=True)
        self.worker.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.worker is not None:
            self.worker.join(timeout=1)

MAX_BYTES = 2_000_000


class Client:
    def __init__(self, path):
        self.path = Path(path)

    def call(self, operation, **arguments):
        info = self.path.lstat()
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise PermissionError('Runtime socket must be owner-only')
        payload = json.dumps({'operation': operation, 'arguments': arguments}).encode() + b'\n'
        if len(payload) > MAX_BYTES:
            raise ValueError('Request too large')
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(90)
            sock.connect(str(self.path))
            sock.sendall(payload)
            with sock.makefile('rb') as stream:
                raw = stream.readline(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or not raw.endswith(b'\n'):
            raise ValueError('Invalid runtime response')
        result = json.loads(raw)
        if result.get('ok') is not True:
            raise RuntimeError(result.get('error', 'Runtime operation failed'))
        return result['result']
