"""Bounded JSONL append shared by every diagnostic and stats writer (Unix)."""
import errno
import fcntl
import json
import os
import queue
import threading
import time
from pathlib import Path

MAX_BYTES = 16 * 1024 * 1024
BACKUPS = 3
# The sidecar lock is shared by every loop-watch process on a host. A holder that is stopped (SIGSTOP,
# a blocking ptrace profiler) or starved must cost dropped diagnostics, never a frozen caller: on
# hermes-hub (2026-11-13 20:09Z) two backends' event loops waited 209 s and 187 s in flock(LOCK_EX).
DIAG_LOCK_TIMEOUT_S = 0.5
DURABLE_LOCK_TIMEOUT_S = 5.0  # stats writers retry the same record on error


def _lock(fd, timeout_s):
    if timeout_s is None:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return
    deadline = time.monotonic() + max(0.0, timeout_s)
    pause = 0.002
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            pass
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES):
                raise
        left = deadline - time.monotonic()
        if left <= 0:
            raise OSError(errno.ETIMEDOUT, 'loop-watch journal lock busy; record dropped')
        time.sleep(min(pause, left))
        pause = min(pause * 2, 0.05)


def append(path, record, *, durable=False, max_bytes=MAX_BYTES, backups=BACKUPS, lock_timeout_s='default'):
    """Serialize rotate+open+append under a stable sidecar lock across processes.

    Do not retain a data-file descriptor across calls: another writer can rotate it.
    Oversize records fail without altering retained data; stats callers retry errors.
    The lock wait is bounded (``lock_timeout_s``; None = wait forever): a busy lock raises ETIMEDOUT.
    """
    if lock_timeout_s == 'default':
        lock_timeout_s = DURABLE_LOCK_TIMEOUT_S if durable else DIAG_LOCK_TIMEOUT_S
    path = Path(path)
    data = (json.dumps(record) + '\n').encode('utf-8')
    if len(data) > max_bytes:
        raise OSError(errno.EFBIG, 'loop-watch record exceeds journal size cap')
    if backups < 1 or max_bytes < 1:
        raise ValueError('positive journal cap and backup count required')
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + '.lock', 'a') as lock:
        _lock(lock, lock_timeout_s)
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


class OffThreadWriter:
    """Runs sink calls on one daemon thread through a bounded queue; a full queue drops the record.

    For callers on an event loop: journal I/O (the cross-process lock, rotation, logging handlers) must
    never run on the loop thread."""

    def __init__(self, maxsize=64, name='loop-watch-journal'):
        self.q = queue.Queue(maxsize)
        self.name = name
        self.dropped = 0
        self.failed = 0
        self._thread = None
        self._start_lock = threading.Lock()

    def _ensure(self):
        if self._thread is not None and self._thread.is_alive():
            return
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
                self._thread.start()

    def submit(self, fn, *args):
        self._ensure()
        try:
            self.q.put_nowait((fn, args))
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def _run(self):
        while True:
            fn, args = self.q.get()
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 - diagnostics are best-effort
                self.failed += 1
            finally:
                self.q.task_done()
