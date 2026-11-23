"""Small file-loader and identity fence shared by parent and RPC child."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def capture(directory):
    directory = Path(directory).resolve()
    files = list(directory.glob('*.py'))
    try:
        import hermes_cli
        root = Path(hermes_cli.__file__).resolve().parent
        # Include lazy core helpers too: the parent must not bless new disk bytes
        # when a scheduled deployment moves source under an already-loaded router.
        files += list(root.glob('kanban*.py'))
        files += [root / '_launchers.py', root.parent / 'hermes_constants.py', root.parent / 'utils.py']
        files += list(root.glob('config*.py'))
    except ImportError:
        pass
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


# Content is immutable only while its complete filesystem identity is unchanged.
# Keep digests, never a cached True: another manifest must still compare its hash.
_digests = {}


def matches(manifest):
    import os
    cached = os.environ.get('HERMES_LANES_IDENTITY_STAT_CACHE', '1').lower() not in ('0', 'false', 'off', 'no')
    try:
        for name, expected in manifest.items():
            path = Path(name)
            stat = path.stat()
            token = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            hit = _digests.get(name) if cached else None
            if hit is None or hit[0] != token:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                after = path.stat()
                if token != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    return False  # a deployment raced the hash; never certify mixed bytes
                if cached:
                    _digests[name] = (token, digest)
            else:
                digest = hit[1]
            if digest != expected:
                return False
        return True
    except OSError:
        return False
