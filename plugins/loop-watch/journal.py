"""Bounded JSONL append shared by every diagnostic and stats writer (Unix)."""
import errno
import fcntl
import json
import os
from pathlib import Path

MAX_BYTES = 16 * 1024 * 1024
BACKUPS = 3


def append(path, record, *, durable=False, max_bytes=MAX_BYTES, backups=BACKUPS):
    """Serialize rotate+open+append under a stable sidecar lock across processes.

    Do not retain a data-file descriptor across calls: another writer can rotate it.
    Oversize records fail without altering retained data; stats callers retry errors.
    """
    path = Path(path)
    data = (json.dumps(record) + '\n').encode('utf-8')
    if len(data) > max_bytes:
        raise OSError(errno.EFBIG, 'loop-watch record exceeds journal size cap')
    if backups < 1 or max_bytes < 1:
        raise ValueError('positive journal cap and backup count required')
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            size = path.stat().st_size if path.exists() else 0
            if size and size + len(data) > max_bytes:
                for number in range(backups, 1, -1):
                    source = Path(str(path) + '.' + str(number - 1))
                    if source.exists():
                        os.replace(source, str(path) + '.' + str(number))
                os.replace(path, str(path) + '.1')
            with open(path, 'ab') as stream:
                stream.write(data)
                stream.flush()
                if durable:
                    os.fsync(stream.fileno())
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
