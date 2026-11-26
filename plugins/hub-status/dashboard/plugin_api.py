"""GET /api/plugins/hub-status/{ping,status,events} (see ../core.py).

Loading this router starts the transition sampler (one per HERMES_HOME across backends, by flock)."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter()
_CORE = Path(__file__).resolve().parents[1] / "core.py"
_KEY = "_hub_status_core"


def _core():
    mod = sys.modules.get(_KEY)
    if mod is None:
        spec = importlib.util.spec_from_file_location(_KEY, _CORE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[_KEY] = mod
    return mod


if not os.environ.get("HUB_STATUS_NO_SAMPLER"):  # tests load the router without touching a real home
    try:
        _core()._SAMPLER.ensure()
    except Exception:  # never block the backend from loading
        pass

RANGES = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


@router.get("/ping")
async def ping() -> Dict[str, Any]:
    return {"ok": True, "api": 1}


@router.get("/status")
def status() -> Dict[str, Any]:
    core = _core()
    if not os.environ.get("HUB_STATUS_NO_SAMPLER"):
        core._SAMPLER.ensure()
    return core.status()


@router.get("/events")
def events(range: str = "24h") -> Dict[str, Any]:  # noqa: A002 - query name
    return _core().events(RANGES.get(range, RANGES["24h"]))
