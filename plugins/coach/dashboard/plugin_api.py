"""Mounted at /api/plugins/coach.

GET  /ping                                   -> {ok, api}
POST /state  {session_id}                    -> {nudge|null, turn, msgs, muted, demoted, shown_turn, budget_turns}
     Evaluates the user-channel rules for the session and passes the first one through the gate (1 per 15 turns,
     session mutes, weekly demotion). A returned nudge is already logged as shown.
POST /offer  {session_id, rule}              -> same shape; for client-detected rules (screenshot_midturn)
POST /event  {session_id, rule, event, id?, turn?}  -> {ok}; shown (peer panels) | accepted | dismissed | expired
POST /restart {session_id}                   -> {title, message, cwd, profile} — brief for a clean restart
POST /board   {session_id, profile?, board?} -> {board, cards} — a handoff card via spin-out's kanban_cards
POST /file-bug {session_id, sig}             -> {board: "hermes", cards}
GET  /stats?days=7                           -> per-rule shown/accepted/dismissed/digest + precision (KPI 4)
"""
import importlib.util
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

API_VERSION = 1
BUG_BOARD = "hermes"


def bug_board(kb=None) -> Optional[str]:
    """The ``hermes`` board when this host has one, else None (the active board).

    Hosts without a ``hermes`` board (e.g. hermes-hub) raised ValueError from connect(), so "File it once" 500'd.
    """
    if kb is None:
        try:
            from hermes_cli import kanban_db  # type: ignore
        except Exception:
            return None
        db = kanban_db
    else:
        db = kb[0]
    try:
        db.init_db(board=BUG_BOARD)
        return BUG_BOARD
    except ValueError:
        return None


_HERE = Path(__file__).resolve().parents[1]
_cache_spec = importlib.util.spec_from_file_location("_coach_module_cache", _HERE / "module_cache.py")
assert _cache_spec and _cache_spec.loader
_cache = importlib.util.module_from_spec(_cache_spec)
_cache_spec.loader.exec_module(_cache)


def _core():
    """core.py, using the same checkout cache as the agent hooks."""
    return _cache.load_sibling(_HERE, "core")


class SessionBody(BaseModel):
    session_id: str
    profile: str = ""


class OfferBody(BaseModel):
    session_id: str
    rule: str


class EventBody(BaseModel):
    session_id: str
    rule: str
    event: str
    id: str = ""
    turn: Optional[int] = None


class BoardBody(BaseModel):
    session_id: str
    profile: str = ""
    board: Optional[str] = None


class BugBody(BaseModel):
    session_id: str
    sig: str


def create_router(llm=None, db_path=None, kb=None, store_root=None):
    router = APIRouter()

    def store():
        return _core().Store(store_root)

    def _llm():
        return llm or _core().aux_llm

    @router.get("/ping")
    def ping():
        return {"ok": True, "api": API_VERSION}

    @router.post("/state")
    def get_state(body: SessionBody):
        return _core().state(store(), body.session_id, db_path)

    @router.post("/offer")
    def offer(body: OfferBody):
        return _core().state(store(), body.session_id, db_path, offer=body.rule)

    @router.post("/event")
    def event(body: EventBody):
        return store().record(body.session_id, body.rule, body.event, body.id, body.turn)

    @router.post("/restart")
    def restart(body: SessionBody):
        return _core().brief(body.session_id, _llm(), "restart", db_path)

    @router.post("/board")
    def board(body: BoardBody):
        core = _core()
        b = core.brief(body.session_id, _llm(), "board", db_path)
        if b.get("error"):
            return {"board": body.board, "cards": [], "error": b["error"]}
        task = {"title": b["title"], "message": b["message"]}
        return core.spin_out_core().kanban_cards([task], body.session_id, body.profile or b["profile"], b["cwd"],
                                                 body.board, kb=kb, created_by="coach", key_prefix="coach-board")

    @router.post("/file-bug")
    def file_bug(body: BugBody):
        core = _core()
        st = store()
        card = core.bug_card(st, body.session_id, body.sig)
        out = core.spin_out_core().kanban_cards([card], body.session_id, "default", "", bug_board(kb), kb=kb,
                                                created_by="coach", key_prefix="coach-bug")
        good = [c for c in out.get("cards", []) if c.get("task_id")]
        if good:
            st.mark_filed(body.sig, {"task_id": good[0]["task_id"], "board": out.get("board")})
        return out

    @router.get("/stats")
    def get_stats(days: float = 7):
        return _core().stats(store(), time.time() - days * 86400)

    return router


router = create_router()
