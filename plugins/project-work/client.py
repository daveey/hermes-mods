"""Narrow Unix-socket client; the socket is never mounted into worker sandboxes."""
import json
import os
from pathlib import Path
import socket
import stat

MAX_BYTES = 2_000_000


class Client:
    def __init__(self, path):
        self.path = Path(path)

    def call(self, operation, **arguments):
        info = self.path.lstat()
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise PermissionError('Runtime socket must be owner-only')
        payload = json.dumps({'operation': operation, 'arguments': arguments}).encode() + b'\n'
        if len(payload) > MAX_BYTES:
            raise ValueError('Request too large')
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(90)
            sock.connect(str(self.path))
            sock.sendall(payload)
            with sock.makefile('rb') as stream:
                raw = stream.readline(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or not raw.endswith(b'\n'):
            raise ValueError('Invalid runtime response')
        result = json.loads(raw)
        if result.get('ok') is not True:
            raise RuntimeError(result.get('error', 'Runtime operation failed'))
        return result['result']
