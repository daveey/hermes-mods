"""Crash recovery for collection-only test homes, before HomeGuard installs.

PID names are leases: PID reuse errs toward keeping a directory. A dead parent
is insufficient while a detached child still holds a descendant. Linux procfs
inspection fails closed (including same-user non-dumpable processes). The hub
privileged disk guard is the periodic fallback in that case; other platforms
retain TemporaryDirectory cleanup. Never ignore permission errors to reclaim space.
"""
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

NAME = re.compile(r'coach-collection-p([1-9][0-9]*)-[a-z0-9_]{8}')


def dead(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        pass
    return False


def held_paths():
    if not Path('/proc/self/fd').is_dir():
        return None
    paths = set()
    try:
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                if proc.stat().st_uid != os.getuid():
                    continue
                paths.add(os.readlink(proc / 'cwd'))
                for fd in (proc / 'fd').iterdir():
                    try:
                        paths.add(os.readlink(fd).removesuffix(' (deleted)'))
                    except FileNotFoundError:
                        pass
                for line in (proc / 'maps').read_text().splitlines():
                    fields = line.split(None, 5)
                    if len(fields) == 6 and fields[5].startswith('/'):
                        paths.add(fields[5].removesuffix(' (deleted)'))
            except (FileNotFoundError, ProcessLookupError):
                continue
    except OSError:
        return None
    return paths


def sweep(root, now=None, idle_s=3600):
    root = Path(root).absolute()
    if root.resolve() != root:
        return []
    now = time.time() if now is None else now
    removed = []
    for path in root.glob('coach-collection-p*'):
        match = NAME.fullmatch(path.name)
        try:
            if not match or path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid():
                continue
            pid = int(match[1])
            if not dead(pid):
                continue
            newest = path.stat().st_mtime
            def fail(error):
                raise error
            for directory, dirs, files in os.walk(path, onerror=fail):
                for name in dirs + files:
                    newest = max(newest, Path(directory, name).lstat().st_mtime)
            if now - newest <= idle_s:
                continue
            held = held_paths()
            if held is None or any((p + os.sep).startswith(str(path) + os.sep) for p in held):
                continue
            if not dead(pid):
                continue
            shutil.rmtree(path)
            removed.append(path)
        except OSError:
            continue  # concurrent reaper or unreadable entry: retry next pass
    return removed


def create():
    root = Path(tempfile.gettempdir())
    sweep(root)
    return tempfile.TemporaryDirectory(prefix=f'coach-collection-p{os.getpid()}-', dir=root)
