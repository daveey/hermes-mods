"""Verified process handles for recovery of this supervisor's dedicated children."""
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time


def fingerprint(pid, timeout=5):
    """PID plus creation identity; never infer identity from an argv substring."""
    try:
        stat = Path('/proc/%d/stat' % pid)
        if stat.exists():
            fields = stat.read_text().rsplit(')', 1)[1].split()
            return None if fields[0] == 'Z' else 'linux:' + fields[19]
        result = subprocess.check_output(['ps', '-p', str(pid), '-o', 'lstart=', '-o', 'stat='],
                                         text=True, timeout=timeout).strip()
        started, state = result.rsplit(None, 1)
        return None if state.startswith('Z') else 'ps:' + started
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def is_zombie(pid, timeout):
    """Positive OS state only; a failed/ambiguous probe is not retirement.

    ps reports unreaped children on Darwin, where kill(pid, 0) still succeeds.
    Include the PID and validate the whole state token so diagnostics/extra rows
    cannot accidentally become evidence. Callers supply their remaining budget.
    """
    try:
        stat = Path('/proc/%d/stat' % pid)
        if stat.exists():
            return stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z'
        result = subprocess.check_output(['ps', '-p', str(pid), '-o', 'pid=', '-o', 'stat='],
                                         text=True, timeout=timeout).split()
        return (len(result) == 2 and result[0] == str(pid)
                and re.fullmatch(r'Z[<+ENLsVX]*', result[1]) is not None)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return False


def record(process):
    started = fingerprint(process.pid)
    if started is None:
        raise RuntimeError('cannot establish child process identity')
    return {'pid': process.pid, 'started': started}


def require_free_endpoint(port):
    """Bind, not HTTP probing: even a silent/non-CDP occupant must refuse launch."""
    from chrome_agent_lifecycle import LifecycleError
    try:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(('127.0.0.1', port))
            probe.listen(1)
    except OSError as exc:
        raise LifecycleError('upstream endpoint occupied or unverifiable; refusing launch') from exc


def owns_listener(identity, port):
    """Verify the exact browser process owns the IPv4 loopback listening socket.

    No argv/URL inference. The preflight bind is only advisory; this check closes
    the launch race. An unavailable OS ownership probe is a refusal, not consent.
    """
    pid, started = identity['pid'], identity['started']
    if fingerprint(pid) != started:
        return False
    try:
        if sys.platform == 'linux':
            inodes = set()
            for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
                fields = line.split()
                if fields[1] == '0100007F:%04X' % port and fields[3] == '0A':
                    inodes.add('socket:[%s]' % fields[9])
            owned = set()
            for fd in Path('/proc/%d/fd' % pid).iterdir():
                try:
                    owned.add(os.readlink(fd))
                except FileNotFoundError:
                    continue  # unrelated descriptor closed during enumeration
            verified = bool(inodes) and inodes <= owned
        elif sys.platform == 'darwin':
            result = subprocess.check_output(
                ['lsof', '-nP', '-a', '-p', str(pid), '-iTCP:%d' % port, '-sTCP:LISTEN', '-Fpn'],
                text=True, timeout=2)
            lines = result.splitlines()
            verified = 'p%d' % pid in lines and 'n127.0.0.1:%d' % port in lines
        else:
            return False
        return verified and fingerprint(pid) == started
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return False


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
