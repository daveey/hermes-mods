"""Mounted at /api/plugins/spin-out.

POST /plan {session_id, profile?} -> {tasks: [{title, prompt, needs_parent, message}], skipped, title, cwd, profile}
`message` is the full first message for the child session (brief + pointer to the parent).
Read-only on state.db; one auxiliary-model call per request.
"""
import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel


def _core():
    name = "_spin_out_core"
    path = Path(__file__).resolve().parents[1] / "core.py"
    mod = sys.modules.get(name)
    if mod is None or getattr(mod, "__file__", None) != str(path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
    return mod


class PlanBody(BaseModel):
    session_id: str
    profile: str = ""


def create_router(llm=None, db_path=None):
    router = APIRouter()

    @router.get("/ping")
    def ping():
        return {"ok": True}

    @router.post("/plan")
    def make_plan(body: PlanBody):  # sync: FastAPI runs it in the threadpool
        core = _core()
        out = core.plan(body.session_id, llm or core.aux_llm, db_path)
        profile = body.profile or out.get("profile") or "default"
        out["profile"] = profile
        for t in out.get("tasks", []):
            t["message"] = core.child_message(t, body.session_id, profile, out.get("title", ""))
        return out

    return router


router = create_router()
