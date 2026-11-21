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


def matches(manifest):
    try:
        return all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in manifest.items())
    except OSError:
        return False
