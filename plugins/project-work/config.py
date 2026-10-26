"""Private per-profile policy. No target is accepted from a model argument."""
from dataclasses import fields
import json
import os
from pathlib import Path
import stat
from .service import Policy


def load_config(home):
    home = Path(home).resolve()
    path = home / 'project-work.json'
    if path.is_symlink():
        raise ValueError('Policy symlinks are forbidden')
    data = json.loads(path.read_text())
    if set(data) != {'version', 'policy', 'runtime_socket'} or data['version'] != 1:
        raise ValueError('Unsupported policy schema')
    if set(data['policy']) != {f.name for f in fields(Policy)}:
        raise ValueError('Unknown or missing policy fields')
    policy = Policy(**data['policy'])
    socket = Path(data['runtime_socket'])
    if not socket.is_absolute() or str(socket.resolve()) != str(socket):
        raise ValueError('Runtime socket must be an absolute non-symlink path')
    # The endpoint cannot point into a repository or task-writable directory.
    if socket.parent != home / 'project-work':
        raise ValueError('Runtime socket must live under profile/project-work')
    for target in (path, home):
        info = target.stat()
        if info.st_uid != os.getuid() or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise PermissionError('Policy/home must be owned and not group/world writable')
    return policy, socket, home / 'project-work' / 'ledger.sqlite3'
