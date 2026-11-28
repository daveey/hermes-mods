"""Attested reference updater entry point; state.json never selects code or argv.

Only the fixed installed runner path is used, after fd checks (regular file, current uid,
no group/other write). No content hash: the updater changes often, and the same file is already
run unattested by the sync launchd job. The snapshot read from the opened fd is what is compiled;
the updater may itself re-exec on interpreters lacking its dependencies.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys


def updater_path() -> Path:
    # This is the installed runner, not a path supplied by state.json or argv.
    return Path.home() / ".hermes" / "sync-runner" / "hermes-sync"


def attested_source() -> bytes:
    path = updater_path()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("unsafe updater ownership or permissions")
        source = stream.read()
    return source


def main() -> None:
    # Execute the snapshot read from the checked fd.
    source = attested_source()
    path = str(updater_path())
    command = "core" if sys.argv[1:] == ["--update"] else "core-waiter"
    sys.argv = [path, command]
    namespace = {"__name__": "__main__", "__file__": path, "__package__": None,
                 "__builtins__": __builtins__}
    exec(compile(source, path, "exec"), namespace)


if __name__ == "__main__":
    main()
