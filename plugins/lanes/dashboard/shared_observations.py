"""Cross-process, home-scoped display observations, never landing authority.

Only the existing background observer enters this file lock. A stable private
lock inode serializes whole-batch provider reads; atomic data replacement makes
process death safe. Exact context keys prevent a new board head using old data.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import stat
import time

BYTE_CAP = 4 * 1024 * 1024
ENTRY_CAP = 1024
TERMINAL_TTL = 3600
LOCK_TIMEOUT = 35


def _key(url, context):
    return hashlib.sha256(json.dumps([url, context], sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _valid_value(value, terminal):
    return (isinstance(value, dict) and set(value) <= {'merged', 'approved', 'checks'}
            and all(type(value[key]) is bool for key in ('merged', 'approved') if key in value)
            and ('checks' not in value or value['checks'] in ('green', 'red', 'pending'))
            and (not terminal or not value or value.get('merged') is True))


def _open(path, flags):
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise PermissionError('PR observation cache requires a private owned regular file')
        return fd
    except BaseException:
        os.close(fd)
        raise


class SharedObservations:
    def __init__(self, directory, clock=time.time):
        self.directory = Path(directory)
        self.clock = clock

    @contextlib.contextmanager
    def _lease(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise PermissionError('PR observation cache requires a private owned directory')
        path = self.directory / 'fill.lock'
        fd = _open(path, os.O_RDWR | os.O_CREAT)
        try:
            deadline = time.monotonic() + LOCK_TIMEOUT
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('PR observation fill busy')
                    time.sleep(.05)
            # Never fill through an unlinked/replaced lock inode.
            current, held = path.lstat(), os.fstat(fd)
            if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
                raise PermissionError('PR observation lock changed')
            yield
        finally:
            os.close(fd)

    def _read(self):
        try:
            fd = _open(self.directory / 'values.json', os.O_RDONLY)
        except FileNotFoundError:
            return {}
        try:
            with os.fdopen(fd, 'rb') as file:
                raw = file.read(BYTE_CAP + 1)
            if len(raw) > BYTE_CAP:
                raise ValueError('PR observation cache exceeds byte limit')
            body = json.loads(raw)
            if not isinstance(body, dict) or body.get('version') != 1:
                raise ValueError('Invalid PR observation cache version')
            entries = body.get('entries')
            if not isinstance(entries, dict) or len(entries) > ENTRY_CAP:
                raise ValueError('Invalid PR observation entries')
            for key, row in entries.items():
                if (not isinstance(key, str) or len(key) != 64 or not isinstance(row, list)
                        or len(row) != 3 or type(row[0]) not in (float, int)
                        or not math.isfinite(row[0]) or not isinstance(row[1], dict)
                        or type(row[2]) is not bool or not _valid_value(row[1], row[2])
                        or not (row[1] or row[2])):
                    raise ValueError('Invalid PR observation row')
            return entries
        except (ValueError, RecursionError):
            # A safe but malformed cache is replaceable, not a permanent outage.
            # The fill lock ensures only one process performs the repair/read.
            logging.getLogger(__name__).warning('Replacing malformed PR observation cache')
            return {}

    def _write(self, entries):
        rows = sorted(entries.items(), key=lambda pair: pair[1][0], reverse=True)[:ENTRY_CAP]
        body = json.dumps({'version': 1, 'entries': dict(rows)}, separators=(',', ':'), allow_nan=False).encode()
        while len(body) > BYTE_CAP and rows:
            rows.pop()
            body = json.dumps({'version': 1, 'entries': dict(rows)}, separators=(',', ':'), allow_nan=False).encode()
        # One reusable staging path bounds crash leftovers to one capped file.
        name = self.directory / 'values.pending'
        fd = _open(name, os.O_RDWR | os.O_CREAT)
        try:
            with os.fdopen(fd, 'wb') as file:
                file.truncate(0)
                file.write(body)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.directory / 'values.json')
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass

    def fetch(self, contexts, factory, ttl, trace=None):
        """Return url -> (original read age, observation, terminal).

        Failure/unknown is never stored. Hits do not renew timestamps. The core
        Reader still performs its real snapshot and sequential head confirmation.
        A storage/lock failure propagates to the observer's existing backoff,
        never bypassing sharing with an independent provider read.
        """
        with self._lease():
            entries = self._read()
            now = self.clock()
            entries = {key: row for key, row in entries.items() if 0 <= now - row[0] < TERMINAL_TTL}
            keys = {url: _key(url, ctx) for url, ctx in contexts.items()}
            missing = {url: contexts[url] for url, key in keys.items()
                       if key not in entries or now - entries[key][0] >= (TERMINAL_TTL if entries[key][2] else ttl)}
            for url, context in contexts.items():
                if trace is not None:
                    try:
                        trace('provider_fill' if url in missing else 'shared_hit', url, context)
                    except Exception:
                        pass  # diagnostics never influence provider evidence
            if missing:
                reader = factory()
                started = self.clock()  # before network, never extended by fill duration
                reader.prefetch(missing)
                states = getattr(reader, 'states', {})
                for url in missing:
                    value = reader.get(url)
                    terminal = states.get(url) in ('MERGED', 'CLOSED') or value.get('merged') is True
                    if value or terminal:
                        entries[keys[url]] = [started, value, terminal]
                self._write(entries)
            now = self.clock()
            return {url: (now - entries[key][0], entries[key][1], entries[key][2])
                    for url, key in keys.items() if key in entries
                    and 0 <= now - entries[key][0] < (TERMINAL_TTL if entries[key][2] else ttl)}
