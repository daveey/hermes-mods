"""Mounted at /api/plugins/prompt-coach.

POST /check  {session_id?, text}                         -> {id, coach, lens, critique, rewrite, confidence} | {coach:false, skipped}
POST /seed   {session_id?, profile?, rewrite, original}   -> {seeded}
POST /choice {id, choice, edited?}                        -> {ok}
GET  /stats?days=7                                        -> aggregate of log.jsonl
Read-only on state.db; /check makes 1-2 auxiliary-model calls.
"""
import importlib.util
import sys
import time
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel


def _core():
    name = "_prompt_coach_core"
    path = Path(__file__).resolve().parents[1] / "core.py"
    mod = sys.modules.get(name)
    if mod is None or getattr(mod, "__file__", None) != str(path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
    return mod


class CheckBody(BaseModel):
    session_id: str = ""
    text: str


class SeedBody(BaseModel):
    session_id: str = ""
    profile: str = ""
    rewrite: str
    original: str = ""


class ChoiceBody(BaseModel):
    id: str
    choice: str
    edited: bool = False


def create_router(llm=None, db_path=None, log_path=None):
    router = APIRouter()

    @router.get("/ping")
    def ping():
        return {"ok": True}

    @router.post("/check")
    def check(body: CheckBody):  # sync: FastAPI runs it in the threadpool
        core = _core()
        return core.coach(body.session_id, body.text, llm or core.aux_llm, db_path, log_path)

    @router.post("/seed")
    def seed(body: SeedBody):
        return {"seeded": _core().seeded_message(body.session_id, body.profile, body.rewrite, body.original)}

    @router.post("/choice")
    def choice(body: ChoiceBody):
        return _core().record_choice(body.id, body.choice, body.edited, log_path)

    @router.get("/stats")
    def stats(days: float = 7):
        return _core().stats(log_path, time.time() - days * 86400)

    return router


router = create_router()
