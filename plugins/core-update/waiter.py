"""Checked reference updater snapshot; state.json never selects code or argv.

Ownership/mode checks are not source approval. The opened bytes are compiled,
and passed to the runner as its content-continuity snapshot for child execs.
Installed-byte parity and independent full-source review remain separate gates.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys


def updater_path() -> Path:
    root = Path.home() / ".hermes"
    # Supported installations may alias only this fixed home (e.g. a renamed
    # root). Do not resolve arbitrary runner/parent symlinks before checking.
    if root.is_symlink():
        if root.lstat().st_uid != os.getuid():
            raise ValueError("foreign updater home alias")
        root = root.resolve(strict=True)
    return root / "sync-runner" / "hermes-sync"


def attested_source() -> bytes:
    """Legacy API name: return checked bytes, NOT an attestation of review."""
    path = updater_path().absolute()
    parent = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
            info = os.fstat(parent)
            if info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
                raise ValueError("unsafe updater directory ownership or permissions")
        if os.fstat(parent).st_uid != os.getuid():
            raise ValueError("unsafe updater directory ownership")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("unsafe updater ownership or permissions")
        source = stream.read()
        after = os.fstat(stream.fileno())
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("updater changed during read")
    return source


def main() -> None:
    source = attested_source()
    path = str(updater_path())
    command = "core" if sys.argv[1:] == ["--update"] else "core-waiter"
    sys.argv = [path, command]
    namespace = {"__name__": "__main__", "__file__": path, "__package__": None,
                 "__builtins__": __builtins__, "_HERMES_SYNC_SOURCE": source}
    exec(compile(source, path, "exec"), namespace)


if __name__ == "__main__":
    main()
