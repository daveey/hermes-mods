"""One source identity and reload policy for coach's path-loaded siblings.

This stateless helper is bootstrapped by file path: coach modules are also loaded
standalone by plugin discovery, the dashboard and tests, without a package parent.
"""
import hashlib
import importlib.util
from pathlib import Path
import sys


def module_key(root, name: str) -> str:
    """Symlink aliases share identity; independent checkouts never share modules."""
    return f"_coach_{name}_{hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()}"


def load_sibling(root, name: str):
    """Return the checkout's cached module, reloading when its nanosecond mtime changes."""
    root = Path(root).resolve()
    key = module_key(root, name)
    path = root / f"{name}.py"
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    mod = sys.modules.get(key)
    if mod is None or getattr(mod, "_coach_mtime", None) != mtime:
        spec = importlib.util.spec_from_file_location(key, path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            # A failed import is not a cache entry (notably mtime=None for a
            # missing file). Do not remove a concurrent loader's replacement.
            if sys.modules.get(key) is mod:
                del sys.modules[key]
            raise
        setattr(mod, "_coach_mtime", mtime)
    return mod
