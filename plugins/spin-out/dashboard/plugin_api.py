"""Mounted at /api/plugins/spin-out.

POST /plan {session_id, profile?, auto?} -> {tasks: [{title, prompt, needs_parent, message}], skipped, title, cwd, profile}
  `message` is the full first message for the child session (brief + pointer to the parent).
  auto=true (sent after every finished turn) skips the model call unless the last reply hints at follow-ups,
  and reuses the previous plan while the session's last reply is unchanged.
POST /kanban {session_id, profile?, cwd?, board?, tasks: [{title, message}]} -> {board, cards: [{title, task_id|error}]}
  One Kanban card per task, assigned to the session's profile and linked to the parent session. Created cards are
  recorded in the spun-out ledger.
POST /spun {session_id, items: [{title, ref}]} -> {added}
  Record follow-ups launched as child sessions (ref = "@session:<profile>/<child id>"). The parent's next turn gets a
  one-time note (agent hook in ../__init__.py) so it stops offering them; later plans skip them.
Read-only on state.db; at most one auxiliary-model call per /plan request.
"""
import importlib.util
import sys
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field


API_VERSION = 3  # 2 = /plan auto + /kanban; 3 = /spun ledger. The desktop half gates each on the ping version.


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
    auto: bool = False


class CardTask(BaseModel):
    title: str
    message: str = ""


class SpunItem(BaseModel):
    title: str
    ref: str = ""


class SpunBody(BaseModel):
    session_id: str
    items: list[SpunItem] = Field(default_factory=list)


class KanbanBody(BaseModel):
    session_id: str
    profile: str = ""
    cwd: str = ""
    board: Optional[str] = None
    tasks: list[CardTask] = Field(default_factory=list)


def create_router(llm=None, db_path=None, kb=None, ledger=None):
    router = APIRouter()
    cache: dict = {}  # session_id -> (reply_index, plan) for auto checks
    lock = threading.Lock()

    @router.get("/ping")
    def ping():
        return {"ok": True, "api": API_VERSION}

    @router.post("/plan")
    def make_plan(body: PlanBody):  # sync: FastAPI runs it in the threadpool
        core = _core()
        if body.auto:
            info = core.session_info(body.session_id, db_path)
            idx, _ = core.last_reply(info) if info else (-1, "")
            with lock:
                hit = cache.get(body.session_id)
            if hit and hit[0] == idx:
                return dict(hit[1], cached=True)
        out = core.plan(body.session_id, llm or core.aux_llm, db_path, auto=body.auto, ledger=ledger)
        profile = body.profile or out.get("profile") or "default"
        out["profile"] = profile
        for t in out.get("tasks", []):
            t["message"] = core.child_message(t, body.session_id, profile, out.get("title", ""))
        if "reply_index" in out and not out.get("error"):
            with lock:
                cache[body.session_id] = (out["reply_index"], out)
        return out

    @router.post("/kanban")
    def to_kanban(body: KanbanBody):
        core = _core()
        tasks = [t.model_dump() for t in body.tasks if t.message.strip()]
        if not tasks:
            return {"board": body.board, "cards": [], "error": "no tasks"}
        out = core.kanban_cards(tasks, body.session_id, body.profile or "default", body.cwd, body.board, kb=kb)
        made = [{"title": c["title"], "ref": f"Kanban card {c['task_id']} on board {out.get('board') or 'default'}"}
                for c in out.get("cards", []) if c.get("task_id")]
        if made:
            core.record_spun(body.session_id, made, ledger)
            with lock:
                cache.pop(body.session_id, None)
        return out

    @router.post("/spun")
    def spun(body: SpunBody):
        added = _core().record_spun(body.session_id, [i.model_dump() for i in body.items], ledger)
        with lock:
            cache.pop(body.session_id, None)  # the cached auto plan still lists what was just spun out
        return {"added": added}

    return router


router = create_router()
