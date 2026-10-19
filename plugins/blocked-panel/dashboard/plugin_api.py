"""Mounted at /api/plugins/blocked-panel.

GET  /ping                                         -> {ok, api}
POST /steps {title, reason, body?, comments?, cached_only?} -> {steps[], cached?} | {steps: [], error|pending}
One auxiliary-model call per distinct input (cached on disk under <home>/cache/blocked-panel/).
"""
import importlib.util
import sys
from pathlib import Path
from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

API_VERSION = 1


def _core():
    """core.py, reloaded when the file changes (edits apply without a backend restart)."""
    name = "_blocked_panel_core"
    path = Path(__file__).resolve().parents[1] / "core.py"
    mtime = path.stat().st_mtime
    mod = sys.modules.get(name)
    if mod is None or getattr(mod, "__file__", None) != str(path) or getattr(mod, "_mtime", None) != mtime:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        setattr(mod, "_mtime", mtime)
        sys.modules[name] = mod
    return mod


class StepsBody(BaseModel):
    title: str = ""
    reason: str = ""
    body: str = ""
    comments: List[str] = []
    cached_only: bool = False


def create_router(llm=None, home=None):
    router = APIRouter()

    @router.get("/ping")
    def ping():
        return {"ok": True, "api": API_VERSION}

    @router.post("/steps")
    def steps(b: StepsBody):  # sync: FastAPI runs it in the threadpool
        return _core().steps_for(b.title, b.reason, b.body, b.comments, llm=llm, home=home, cached_only=b.cached_only)

    return router


router = create_router()
