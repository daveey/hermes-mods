"""GET /api/plugins/core-update/status, POST /api/plugins/core-update/restart (see ../core.py)."""
from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter()
_CORE = Path(__file__).resolve().parents[1] / "core.py"
_CORE_LOCK = threading.Lock()


def _core():
    # Only loading/publication is serialized, never status or restart work.
    with _CORE_LOCK:
        # keyed on mtime so edits to core.py apply without a backend restart
        key = f"_core_update_core_{int(_CORE.stat().st_mtime_ns)}"
        mod = sys.modules.get(key)
        if mod is None:
            spec = importlib.util.spec_from_file_location(key, _CORE)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sys.modules[key] = mod
        return mod


@router.get("/ping")
async def ping() -> Dict[str, Any]:
    return {"ok": True, "api": 1}


@router.get("/status")
def status() -> Dict[str, Any]:
    try:
        return _core().status()
    except Exception as exc:  # fail open: the chip just hides
        return {"state": "unavailable", "error": str(exc)[:200]}


@router.post("/restart")
def restart() -> Dict[str, Any]:
    return _core().restart()
