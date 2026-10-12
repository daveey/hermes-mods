"""Mounted at /api/plugins/topic-shift.

POST /check  {session_id, text}           -> {shift, confidence, label, reason, title} | {shift:false, skipped}
POST /brief  {session_id, text, profile?} -> {brief, title, cwd, profile, seeded}
Read-only on state.db; one auxiliary-model call per request.
"""
import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel


def _core():
    name = "_topic_shift_core"
    path = Path(__file__).resolve().parents[1] / "core.py"
    mod = sys.modules.get(name)
    if mod is None or getattr(mod, "__file__", None) != str(path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
    return mod


class CheckBody(BaseModel):
    session_id: str
    text: str


class BriefBody(BaseModel):
    session_id: str
    text: str
    profile: str = ""


def create_router(llm=None, db_path=None):
    router = APIRouter()

    def _llm():
        return llm or _core().aux_llm

    @router.get("/ping")
    def ping():
        return {"ok": True}

    @router.post("/check")
    def check(body: CheckBody):  # sync: FastAPI runs it in the threadpool
        return _core().classify(body.session_id, body.text, _llm(), db_path)

    @router.post("/brief")
    def make_brief(body: BriefBody):
        core = _core()
        out = core.brief(body.session_id, body.text, _llm(), db_path)
        profile = body.profile or out.get("profile") or "default"
        out["seeded"] = core.seeded_message(body.session_id, profile, out.get("title", ""), out.get("brief", ""), body.text)
        return out

    return router


router = create_router()
