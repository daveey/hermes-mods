"""system-one: library plugin. Sibling plugins load client.py with `load()` below (copy the 12-line helper);
nothing is registered with Hermes."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load():
    """The client module, or None. Sibling plugins use the same logic via their own copy of this helper:
    resolve <plugins>/system-one/client.py next to their own (symlink-resolved) directory."""
    path = Path(__file__).resolve().parent / "client.py"
    mod = sys.modules.get("_system_one_client")
    if mod is None:
        spec = importlib.util.spec_from_file_location("_system_one_client", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod = sys.modules.setdefault("_system_one_client", mod)  # register only once fully loaded (threads)
    return mod


def register(ctx) -> None:
    return None
