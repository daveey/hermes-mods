"""First-turn workspace association for unassigned Desktop/TUI social chats.

Uses the owning runtime's normal workspace setter, never a cross-host ID or a
background scan. Runs before the first model call/tools; no prompt cache rewrite.
CLI/worker sessions, resumed/branched chats and chosen workspaces are untouched.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def route_first_turn(routing, *, session_id=None, user_message=None, is_first_turn=False,
                     parent_session_id=None, platform=None, **_):
    if (not session_id or is_first_turn is not True or parent_session_id
            or platform not in ("desktop", "tui") or os.environ.get("HERMES_KANBAN_TASK")
            or not isinstance(user_message, str) or not routing.is_social(title=user_message)):
        return None
    # Never import/start another runtime just to find a session.
    server = sys.modules.get("tui_gateway.server")
    required = ("_sessions_lock", "_sessions", "_session_db", "_set_session_cwd", "_cwd_info", "_emit")
    if server is None or any(not hasattr(server, name) for name in required):
        return None
    with server._sessions_lock:
        matches = [(sid, s) for sid, s in server._sessions.items() if s.get("session_key") == session_id]
    if len(matches) != 1:
        return None
    sid, session = matches[0]
    # This hook belongs to the admitted first turn, not an unrelated worker. The
    # history lock serializes a simultaneous UI workspace move/close where supported.
    with session["history_lock"]:
        if (session.get("source") not in ("desktop", "tui") or session.get("explicit_cwd")
                or session.get("cwd_pinned") or session.get("parent_session_id")
                or session.get("_closing") or session.get("room_plumbing")
                or session.get("social_project_routed")):
            return None
        from hermes_constants import get_hermes_home
        home = Path(session.get("profile_home") or get_hermes_home())
        target = routing.project_path(home)
        if not target or not Path(target).is_dir():
            return None
        with server._session_db(session) as db:
            if db is None:
                return None
            row = db.get_session(session_id)
            # A persisted cwd is provenance for an existing workspace, even home.
            # Require a row from this submit, not a guessed/inherited session.
            if not row or row.get("cwd") or row.get("parent_session_id") or row.get("ended_at"):
                return None
        cwd = server._set_session_cwd(session, target)
        session["social_project_routed"] = True
    server._emit("session.info", sid, server._cwd_info(session, cwd))
    log.info("inbox: first social turn %s associated with host-local pr project", session_id)
    return {"context": "This new social-publishing chat was automatically assigned to the pr project. "
                       f"Its working directory is now {cwd}."}
