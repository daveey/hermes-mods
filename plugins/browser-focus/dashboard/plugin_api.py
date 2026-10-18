"""GET /api/plugins/browser-focus/status, POST /show, POST /hide (see ../__init__.py)."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter()
_CORE = Path(__file__).resolve().parents[1] / "__init__.py"


def _core():
    # keyed on mtime so edits apply without a backend restart
    key = f"_browser_focus_core_{int(_CORE.stat().st_mtime_ns)}"
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
async def status() -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(_core().status)
    except Exception as exc:  # fail open: the toggle just hides
        return {"available": False, "error": str(exc)[:200]}


@router.post("/show")
async def show() -> Dict[str, Any]:
    return await asyncio.to_thread(_core().show)


@router.post("/hide")
async def hide() -> Dict[str, Any]:
    return await asyncio.to_thread(_core().hide)
