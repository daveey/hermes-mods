#!/usr/bin/env python3
"""Linux-only scheduling fault injection, limited to a newly spawned session.

Daemonized children that leave the session are deliberately NOT signalled.
Pidfds pin identity: a reused numeric pid is never signalled by accident.
"""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import time


def members(session):
    """Open identity handles only for members of our owned session."""
    handles = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        fd = None
        try:
            fd = os.pidfd_open(pid)
            if os.getsid(pid) == session:
                handles.append(fd)
                fd = None
        except (ProcessLookupError, PermissionError):
            pass
        finally:
            if fd is not None:
                os.close(fd)
    return handles


def send(handles, sig):
    for fd in handles:
        try:
            signal.pidfd_send_signal(fd, sig)
        except (ProcessLookupError, PermissionError):
            pass


def run(command, stopped=0.3, runnable=0.05, timeout=90):
    if stopped < 0 or runnable <= 0 or timeout <= 0:
        raise ValueError('invalid timing')
    child = subprocess.Popen(command, start_new_session=True)
    handles = []
    deadline = time.monotonic() + timeout
    try:
        while os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(command, timeout)
            handles = members(child.pid)
            try:
                send(handles, signal.SIGSTOP)
                time.sleep(min(stopped, max(0, deadline - time.monotonic())))
            finally:
                send(handles, signal.SIGCONT)
                for fd in handles:
                    os.close(fd)
                handles = []
            time.sleep(runnable)
        # Leave the leader unreaped until the finally sweep, pinning the SID.
        result = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
        assert result is not None
        return result.si_status if result.si_code == os.CLD_EXITED else -result.si_status
    finally:
        # A deadline or interruption must not leave any session members stopped.
        handles = members(child.pid)
        try:
            send(handles, signal.SIGCONT)
            send(handles, signal.SIGKILL)
        finally:
            for fd in handles:
                os.close(fd)
        child.wait()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stopped', type=float, default=0.3)
    p.add_argument('--runnable', type=float, default=0.05)
    p.add_argument('--timeout', type=float, default=90)
    p.add_argument('command', nargs=argparse.REMAINDER)
    a = p.parse_args()
    command = a.command[1:] if a.command[:1] == ['--'] else a.command
    if not command:
        p.error('a command is required')
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        code = run(command, a.stopped, a.runnable, a.timeout)
        return code if code >= 0 else 128 - code
    except KeyboardInterrupt:
        return 130
    except subprocess.TimeoutExpired:
        return 124


if __name__ == '__main__':
    raise SystemExit(main())
