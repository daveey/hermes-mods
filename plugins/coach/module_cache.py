"""One source identity and reload policy for coach's path-loaded siblings.

This helper is bootstrapped by file path: coach modules are also loaded
standalone by plugin discovery, the dashboard and tests, without a package parent.
"""
import _imp
import hashlib
import importlib.util
from pathlib import Path
import sys
import threading
import types


def module_key(root, name: str) -> str:
    """Symlink aliases share identity; independent checkouts never share modules."""
    return f"_coach_{name}_{hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()}"


def _load_lock(key):
    # The helper itself is path-loaded more than once. A helper-local lock would
    # not serialize those callers. Publish a process-shared holder before any
    # sibling code runs; keep the global import lock only for this short lookup.
    lock_key = key + "_loader_lock"
    _imp.acquire_lock()
    try:
        holder = sys.modules.get(lock_key)
        if holder is None:
            holder = types.ModuleType(lock_key)
            setattr(holder, "lock", threading.RLock())
            sys.modules[lock_key] = holder
        return getattr(holder, "lock")
    finally:
        _imp.release_lock()


def load_sibling(root, name: str):
    """Return a fully initialized checkout module, reloading on changed mtime."""
    root = Path(root).resolve()
    key = module_key(root, name)
    path = root / f"{name}.py"
    with _load_lock(key):
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
                # missing file). A later caller must retry initialization.
                if sys.modules.get(key) is mod:
                    del sys.modules[key]
                raise
            setattr(mod, "_coach_mtime", mtime)
        return mod
