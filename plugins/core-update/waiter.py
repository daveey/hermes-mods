"""Attested reference updater entry point; state.json never selects code or argv.

The pin is the hermes-sync blob from the private state repository at
64b00305f8d2b0d2bb1c1b2a5729c5b1510e7646. Refresh it only with source review.
Unknown installed revisions deliberately disable Restart rather than trust state.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import sys

UPDATER_SHA256 = "e352f6565b6c11eda18f0da6a776f95360b7ec25991ec30ea1a6029dd145accb"


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
    if hashlib.sha256(source).hexdigest() != UPDATER_SHA256:
        raise ValueError("installed updater is not the reviewed revision")
    return source


def main() -> None:
    # Execute the same immutable snapshot that was hashed, never reopen the path.
    source = attested_source()
    path = str(updater_path())
    sys.argv = [path, "core-waiter"]
    namespace = {"__name__": "__main__", "__file__": path, "__package__": None,
                 "__builtins__": __builtins__}
    exec(compile(source, path, "exec"), namespace)


if __name__ == "__main__":
    main()
