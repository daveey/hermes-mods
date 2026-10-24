"""Verified process handles for recovery of this supervisor's dedicated children."""
import os
from pathlib import Path
import signal
import subprocess
import time


def fingerprint(pid):
    """PID plus creation identity; never infer identity from an argv substring."""
    try:
        stat = Path('/proc/%d/stat' % pid)
        if stat.exists():
            fields = stat.read_text().rsplit(')', 1)[1].split()
            return None if fields[0] == 'Z' else 'linux:' + fields[19]
        result = subprocess.check_output(['ps', '-p', str(pid), '-o', 'lstart=', '-o', 'stat='],
                                         text=True, timeout=5).strip()
        started, state = result.rsplit(None, 1)
        return None if state.startswith('Z') else 'ps:' + started
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def record(process):
    started = fingerprint(process.pid)
    if started is None:
        raise RuntimeError('cannot establish child process identity')
    return {'pid': process.pid, 'started': started}


class RecoveredProcess:
    """Popen's lifecycle subset, signalling only the recorded process generation."""
    def __init__(self, identity):
        self.pid = identity['pid']
        self.started = identity['started']
        self.returncode = None
        if not isinstance(self.pid, int) or self.pid <= 0 or not self.started:
            raise ValueError('invalid child identity')

    def poll(self):
        if fingerprint(self.pid) != self.started:
            self.returncode = 0
        return self.returncode

    def _signal(self, sig):
        if self.poll() is None:
            try:
                os.kill(self.pid, sig)
            except ProcessLookupError:
                self.returncode = 0

    def terminate(self):
        self._signal(signal.SIGTERM)

    def kill(self):
        self._signal(signal.SIGKILL)

    def wait(self, timeout=None):
        deadline = time.monotonic() + (timeout if timeout is not None else 30)
        while self.poll() is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired('recovered child', timeout)
            time.sleep(.05)
        return self.returncode
