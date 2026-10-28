"""Short, private Unix-socket fixtures; never change tempfile's global root."""
import errno
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time


def fresh_profile_arguments(directory, profile):
    """Select test-only flags once, before launching a disposable profile.

    Keep the TemporaryDirectory alive through all wakes and child cleanup.
    This is not a production launch option or a real-Keychain acceptance test.
    """
    if not isinstance(directory, tempfile.TemporaryDirectory):
        raise ValueError('a live disposable TemporaryDirectory is required')
    root = Path(directory.name)
    profile = Path(profile)
    if (not root.is_dir() or root.is_symlink() or profile.parent != root
            or profile.is_symlink() or profile.exists()):
        raise ValueError('refusing existing or non-isolated fixture profile')
    args = ['--user-data-dir=' + str(profile)]
    if sys.platform == 'darwin':
        args.append('--use-mock-keychain')
    return args


def chrome_environment(root):
    """Chrome's launcher/crash caches are outside --user-data-dir by default."""
    root = Path(root)
    return dict(HOME=str(root), XDG_DATA_HOME=str(root / 'data'), XDG_CONFIG_HOME=str(root / 'config'),
                XDG_CACHE_HOME=str(root / 'cache'), CHROME_CONFIG_HOME=str(root / 'config'))


def socket_directory():
    # macOS's default /var/folders tempfile root is too long for a capability
    # socket. Honor an explicitly selected managed scratch root, not /tmp.
    home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')
    configured = os.environ.get('TMPDIR')
    system_roots = ('/tmp', '/private/tmp', '/var/folders', '/private/var/folders')
    if configured and not any(configured == p or configured.startswith(p + '/') for p in system_roots):
        root = Path(configured)
    else:
        root = home / 'cache' / 'scratch'
    root.mkdir(parents=True, exist_ok=True)
    directory = tempfile.TemporaryDirectory(prefix='', dir=root)
    path = Path(directory.name) / ('bu-lease-' + 'x' * 43 + '.sock')
    if len(os.fsencode(path)) > 103:
        directory.cleanup()
        raise ValueError('managed scratch root too long for portable Unix sockets; select a shorter TMPDIR')
    return directory


# Failure deadlines for real peer startup and exit under host load. A ready
# peer returns at once; these bounds are not behavioral assertions.
PEER_READY_S = 20
PEER_EXIT_S = 30
# Real headless Chrome on a busy GitHub runner. start_chrome waits 30s; a 20s
# test budget failed main while the process was still alive (missed by <50ms
# after a full wait). A ready browser returns at once.
CHROME_READY_S = 60


def announce_ready():
    """Print only after listen() — or after close(), for a deliberately refused socket."""
    sys.stdout.buffer.write(b'ready\n')
    sys.stdout.buffer.flush()


def spawn_peer(command):
    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'
    return subprocess.Popen(command, stdout=subprocess.PIPE, env=env)


def wait_ready_line(process, seconds=PEER_READY_S):
    """Block until the child prints ``ready``, or fail at the deadline."""
    deadline = time.monotonic() + seconds
    fd = process.stdout.fileno()
    os.set_blocking(fd, False)
    buf = b''
    while b'ready\n' not in buf:
        if process.poll() is not None:
            raise AssertionError('peer %s exited before ready: %r' % (process.pid, buf))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError('peer %s not ready within %ss: %r' % (process.pid, seconds, buf))
        readable, _, _ = select.select([fd], [], [], min(0.2, remaining))
        if not readable:
            continue
        chunk = process.stdout.read(256)
        if chunk:
            buf += chunk
    return process


def wait_socket_accepts(path, seconds=PEER_READY_S):
    """Connect succeeds only after listen(); a bound-but-not-listening socket refuses."""
    deadline = time.monotonic() + seconds
    last = None
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(0.2)
                client.connect(path)
                return
        except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
            last = exc
            if time.monotonic() >= deadline:
                raise AssertionError('socket did not accept %s: %s' % (path, last))
            threading.Event().wait(0.02)


def wait_socket_refused(path, seconds=PEER_READY_S):
    """A closed listener refuses. File existence alone is not that state."""
    deadline = time.monotonic() + seconds
    while True:
        refused = False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(0.2)
                client.connect(path)
        except ConnectionRefusedError:
            refused = True
        except FileNotFoundError:
            refused = False
        if refused:
            return
        if time.monotonic() >= deadline:
            raise AssertionError('socket still accepting %s' % path)
        threading.Event().wait(0.02)


def wait_process_exit(process, seconds=PEER_EXIT_S):
    """The assertion is that the process exited, not that it did so in a few seconds."""
    try:
        return process.wait(timeout=seconds)
    except subprocess.TimeoutExpired as exc:
        raise AssertionError('process %s still running after %ss' % (process.pid, seconds)) from exc


def wait_exchange(path, payload, seconds=PEER_READY_S):
    """Retry until the peer answers. A dropped client is not a wrong target."""
    from chrome_agent_daemons import exchange
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            return exchange(path, payload, min(deadline, time.monotonic() + 2))
        except (ConnectionRefusedError, TimeoutError, OSError, ValueError) as exc:
            last = exc
            threading.Event().wait(0.05)
    raise AssertionError('exchange failed within %ss: %s' % (seconds, last))


def join_thread(thread, seconds=PEER_EXIT_S):
    if thread is None:
        raise AssertionError('expected a started thread')
    thread.join(seconds)
    if thread.is_alive():
        raise AssertionError('thread still running after %ss' % seconds)


def sweep_until(ledger, predicate, seconds=PEER_EXIT_S, force=False):
    """Retry sweeps until ``predicate`` holds. One sub-second slice is not enough under load."""
    deadline = time.monotonic() + seconds
    while True:
        ledger.sweep_daemons(force=force, deadline=min(deadline, time.monotonic() + 5))
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise AssertionError('sweep did not reach expected state within %ss' % seconds)
        threading.Event().wait(0.05)


def retire_until(ledger, predicate, seconds=15, slice_s=1):
    """Retirement slices until ``predicate`` holds.

    ``retire_daemons`` returns early only when nothing remains, so a long timeout
    on a test that expects a survivor waits out the entire budget.
    """
    from chrome_agent_lifecycle import Lifecycle
    deadline = time.monotonic() + seconds
    remaining = None
    while time.monotonic() + slice_s <= deadline:
        if predicate():
            return remaining
        remaining = Lifecycle(ledger, 'test', lambda: None).retire_daemons(timeout=slice_s)
    if not predicate():
        raise AssertionError('retirement did not reach expected state within %ss' % seconds)
    return remaining


def wait_until_ready(ready, process, seconds, detail):
    """Block until ``ready()`` is truthy. The bound is a load budget, not behavior.

    ``detail`` is called only on failure so a Chrome log is not read on the
    success path.
    """
    deadline = time.monotonic() + seconds
    while not ready():
        code = process.poll()
        if code is not None:
            raise AssertionError('process %s exited %s before ready: %s' % (
                process.pid, code, detail()))
        if time.monotonic() >= deadline:
            raise AssertionError('process %s not ready within %ss: %s' % (
                process.pid, seconds, detail()))
        threading.Event().wait(0.05)


def processes_holding_profile(profile):
    """Pids whose argv names this exact ``--user-data-dir``. Empty without /proc."""
    profile = os.path.realpath(os.fspath(profile))
    needle = os.fsencode(profile)
    flag = b'--user-data-dir=' + needle
    if not needle or not os.path.isdir('/proc'):
        return []
    found = []
    me = os.getpid()
    for entry in os.scandir('/proc'):
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid <= 1 or pid == me:
            continue
        try:
            with open(os.path.join(entry.path, 'cmdline'), 'rb') as stream:
                raw = stream.read()
        except OSError:
            continue
        for part in raw.split(b'\0'):
            if part == flag or part == needle:
                found.append(pid)
                break
            if not part.startswith(b'--user-data-dir='):
                continue
            try:
                named = os.path.realpath(os.fsdecode(part.split(b'=', 1)[1]))
            except (OSError, UnicodeError):
                continue
            if named == profile:
                found.append(pid)
                break
    return found


def reap_profile_processes(profile, wait_s=2):
    """Stop Chrome children that outlive the parent and keep writing the profile.

    Returns pids still alive. Callers must not treat a non-empty return as success:
    ignoring it hides the leak that slows the next launch.
    """
    def _signal(sig):
        for pid in processes_holding_profile(profile):
            try:
                os.kill(pid, sig)
            except OSError:
                pass

    _signal(signal.SIGTERM)
    deadline = time.monotonic() + wait_s
    while processes_holding_profile(profile) and time.monotonic() < deadline:
        threading.Event().wait(0.05)
    _signal(signal.SIGKILL)
    deadline = time.monotonic() + wait_s
    while processes_holding_profile(profile) and time.monotonic() < deadline:
        threading.Event().wait(0.05)
    return processes_holding_profile(profile)


def remove_tree_retrying(root, attempts=8):
    """Delete ``root`` after reaping profile holders.

    Python 3.11 ``shutil.rmtree`` raises ``ENOTEMPTY`` when Chrome creates a file
    in ``Default`` between scandir and rmdir. Retry; do not ignore a live process.
    """
    root = os.fspath(root)
    left = reap_profile_processes(root)
    if left:
        raise AssertionError('profile processes still running: %s' % left)
    busy = (errno.ENOTEMPTY, errno.EBUSY, errno.EACCES, errno.EPERM)
    last = None
    for attempt in range(attempts):
        try:
            shutil.rmtree(root)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            last = exc
            if exc.errno not in busy:
                raise
            threading.Event().wait(0.05 * (attempt + 1))
    shutil.rmtree(root, ignore_errors=True)
    if os.path.exists(root):
        raise OSError(last.errno if last else errno.ENOTEMPTY,
                      'profile tree still present after %s attempts: %s' % (attempts, last),
                      root)


def cleanup_disposable_tree(directory, attempts=8):
    """Remove a TemporaryDirectory once Chrome has stopped writing it.

    ``cleanup()`` detaches its finalizer before ``rmtree``. On ``ENOTEMPTY`` the
    finalizer is already gone, so the retry owns the rest of the removal.
    """
    if not isinstance(directory, tempfile.TemporaryDirectory):
        raise ValueError('a live disposable TemporaryDirectory is required')
    busy = (errno.ENOTEMPTY, errno.EBUSY, errno.EACCES, errno.EPERM)
    try:
        directory.cleanup()
        return
    except OSError as exc:
        if exc.errno not in busy:
            raise
    remove_tree_retrying(directory.name, attempts=attempts)
