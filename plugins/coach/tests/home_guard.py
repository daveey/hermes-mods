"""Process-wide audit seatbelt: swallowed errors/background threads must still fail tests.

Code/dependency roots and pytest-owned temporary directories are not live state, even
when a managed interpreter or TMPDIR happens to live below ~/.hermes.
"""
import os
from pathlib import Path
import sys
import threading
from urllib.parse import unquote, urlsplit


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
        sys.addaudithook(self.audit)
