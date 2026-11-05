"""Process-wide audit seatbelt: swallowed errors/background threads must still fail tests.

Code/dependency roots and pytest-owned temporary directories are not live state, even
when a managed interpreter or TMPDIR happens to live below ~/.hermes.
"""
import functools
import os
from pathlib import Path
import sys
import threading
from urllib.parse import unquote, urlsplit


class _AuditStr(str):
    """Unique audit identity, even for CPython's interned one-character paths."""


class _AuditBytes(bytes):
    pass


class HomeGuard:
    def __init__(self, live_home, allowed=(), plugin_roots=(), code_roots=()):
        self.live_home = Path(live_home).resolve()
        self.code_roots = tuple(Path(p).resolve() for p in code_roots)
        self.allowed = tuple(Path(p).resolve() for p in allowed)
        self.plugin_roots = tuple(Path(p).resolve() for p in plugin_roots)
        self.violations = []
        self.local = threading.local()

    def allow(self, path):
        path = Path(path).resolve()
        if path not in self.allowed:
            self.allowed += (path,)

    def audit(self, event, args):
        if getattr(self.local, 'busy', False) or event not in ('open', 'sqlite3.connect', 'exec'):
            return
        raw = args[0].co_filename if event == 'exec' else args[0]
        if not isinstance(raw, (str, bytes, os.PathLike)):
            return
        pending = getattr(self.local, 'fd_open', None)
        if (event == 'open' and pending is not None and raw is pending[0]
                and len(args) > 1 and args[1] is None):
            # os.open emits mode=None. An earlier hook's io.open(raw) can reuse
            # the exact object, but its event has a string mode and is cwd-based.
            # Consume once: another audit hook may itself open a same-named file.
            self.local.fd_open = None
            raw = pending[1]
        raw = os.fsdecode(raw)
        # The gateway reaper inspects these kernel-owned regular procfs files for
        # every PID after every test. They cannot be symlinks into a Hermes home.
        # Do NOT exempt /proc/<pid>/root or /proc/<pid>/fd (those can alias live data).
        parts = raw.split('/')
        if event == 'open' and len(parts) == 4 and parts[1] == 'proc' and parts[2].isdigit() and parts[3] in ('cmdline', 'environ', 'stat'):
            return
        if raw.startswith('<') or raw == ':memory:':
            return
        if event == 'sqlite3.connect' and raw.startswith('file:'):
            raw = unquote(urlsplit(raw).path)
        self.local.busy = True
        try:
            path = Path(raw).resolve()
            if any(path.is_relative_to(root) for root in self.allowed):
                return
            code = any(path.is_relative_to(root) for root in self.code_roots)
            if code:
                mode = args[1] if event == 'open' and len(args) > 1 else None
                flags = args[2] if event == 'open' and len(args) > 2 else 0
                writing = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or bool(
                    flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
                if event == 'exec' or (event == 'open' and not writing):
                    return
            live = path.is_relative_to(self.live_home)
            foreign = any(path.is_relative_to(root) for root in self.plugin_roots)
            if live or foreign or code:
                message = f'{event}: {path} on {threading.current_thread().name}'
                self.violations.append(message)
                raise AssertionError('coach live-home/foreign-plugin leak: ' + message)
        finally:
            self.local.busy = False

    def install(self):
        if getattr(self, '_installed', False):
            return
        # CPython's open audit event has (path, mode, flags), but no dir_fd.
        # Carry the missing authority through the Python os.open entry point;
        # never exempt relative events or infer their target from caller frames.
        # This is a test seatbelt, not a sandbox for native/direct-syscall code.
        original = os.open

        @functools.wraps(original)
        def open_with_dir_fd(path, flags, mode=0o777, *, dir_fd=None):
            raw = os.fsdecode(os.fspath(path))
            previous = getattr(self.local, 'fd_open', None)
            self.local.fd_open = None
            try:
                if dir_fd is not None and not os.path.isabs(raw):
                    if sys.platform == 'darwin':
                        import fcntl
                        # Darwin F_GETPATH returns the kernel's path for this fd.
                        base = os.fsdecode(fcntl.fcntl(dir_fd, 50, bytes(1024)).split(b'\0', 1)[0])
                    elif sys.platform.startswith('linux'):
                        base = os.readlink(f'/proc/self/fd/{dir_fd}')
                    else:
                        raise OSError('HomeGuard cannot resolve dir_fd on this platform')
                    if not os.path.isabs(base) or base.endswith(' (deleted)'):
                        raise OSError('HomeGuard cannot resolve directory fd safely')
                    # A distinct object prevents an earlier audit hook's same-name
                    # open from consuming our fd mapping (including one-char names).
                    path = os.fspath(path)
                    if not isinstance(path, (_AuditStr, _AuditBytes)):
                        path = _AuditBytes(path) if isinstance(path, bytes) else _AuditStr(path)
                    self.local.fd_open = (path, os.path.join(base, raw))
                # Preserve kernel dir_fd semantics and flags; unresolvable fds fail closed.
                return original(path, flags, mode, dir_fd=dir_fd)
            finally:
                self.local.fd_open = previous

        if original in os.supports_dir_fd:
            os.supports_dir_fd.add(open_with_dir_fd)
        os.open = open_with_dir_fd
        sys.addaudithook(self.audit)
        self._installed = True
