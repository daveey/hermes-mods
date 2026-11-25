"""Bounded, best-effort cache diagnostics; never store URLs or board payloads."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import time

CAP = 256
BYTE_CAP = 262144


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


def observer_trace(cache, event, url, context, **fields):
    emit(dict(cache=cache, event=event, url=fingerprint(url), context=fingerprint(context), **fields))


def emit(record, *, path=None):
    """One fixed-size ring shared by observers; contention drops diagnostics only.

    Keep the inode stable under flock, including truncation, so several backend
    and projector processes cannot race a rename/rotation. No cache lock is held
    by callers, and no filesystem error changes provider evidence or admission.
    """
    if path is None:
        from hermes_constants import get_hermes_home
        path = Path(get_hermes_home()) / 'logs' / 'lanes-pr-observations.json'
    fd = None
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raw = os.read(fd, BYTE_CAP + 1)
        try:
            rows = json.loads(raw) if raw and len(raw) <= BYTE_CAP else []
        except (ValueError, UnicodeError):
            rows = []
        if not isinstance(rows, list):
            rows = []
        rows = rows[-(CAP - 1):] + [dict(record, pid=os.getpid(), at=time.time())]
        body = json.dumps(rows, separators=(',', ':')).encode()
        if len(body) > BYTE_CAP:
            return
        os.lseek(fd, 0, os.SEEK_SET)
        view = memoryview(body)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                return
            view = view[written:]
        os.ftruncate(fd, len(body))
    except OSError:
        pass  # best effort, including contention; never delay a network refresh
    finally:
        if fd is not None:
            os.close(fd)
