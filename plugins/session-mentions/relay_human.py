"""cross_task comment ``relays_user_message``: the origin-side proof that a chat is passing on the user's reply.

kanban-d1b921f32e: a card asks David (``needs_input``), he answers in a chat, and the chat relays the
answer with ``cross_task action=comment``. The courier records every such comment as ``writer_kind='agent'``
(correctly), so the card's no-progress budget saw answered asks as idle runs and stopped the card. With
``relays_user_message=true`` this gateway (the origin, which holds the chat's transcript) verifies the relay
and sends ``relayed_human = {session, message_id, at}``; the receiving core stores it as sent and its
budget counts it as a human answer. ``writer_kind`` stays ``agent``: no operator authority is gained.

Verified here: an interactive chat (not a Kanban worker, not a delegate_task child), whose newest user
message is in this gateway's ``state.db``, and that message not already relayed to the same target (one
relay per user turn per card; ledger under the relay root). A receiver whose core predates the field
ignores it.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional

LEDGER = "relayed-human.json"
LEDGER_KEEP_SECONDS = 30 * 86400


def requested(args: dict) -> bool:
    flag = (args or {}).get("relays_user_message")
    return flag is True or str(flag).strip().lower() == "true"


def _delegated_child() -> bool:
    try:
        from tools.kanban_tools import _is_delegated_child_context
        return bool(_is_delegated_child_context())
    except Exception:  # an older core without the helper: the env worker check still applies
        return False


def latest_user_message(state_db: Path, session_id: str) -> Optional[tuple[int, float]]:
    if not state_db.is_file():
        return None
    uri = f"file:{state_db.resolve().as_posix()}"
    try:
        try:
            con = sqlite3.connect(f"{uri}?mode=ro", uri=True, timeout=2.0)
            con.execute("select 1 from sqlite_master limit 1")
        except sqlite3.OperationalError:
            # mode=ro fails on a WAL db whose -wal/-shm were deleted: rw + query_only never writes.
            con = sqlite3.connect(f"{uri}?mode=rw", uri=True, timeout=2.0)
            con.execute("PRAGMA query_only=ON")
    except sqlite3.Error:
        return None
    try:
        row = con.execute("SELECT id, timestamp FROM messages WHERE session_id = ? AND role = 'user' "
                          "ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return (int(row[0]), float(row[1])) if row else None


def _ledger(base: Path) -> dict:
    try:
        data = json.loads((base / LEDGER).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def stamp(target: str, session_id: Optional[str], *, base: Path, state_db: Path,
          write: Callable[[Path, Any], None], now: Optional[float] = None) -> tuple[Optional[dict], Optional[str]]:
    """``(relayed_human, None)`` for a verified relay, else ``(None, refusal)``. Records the use in the ledger."""
    if os.environ.get("HERMES_KANBAN_TASK", "").strip():
        return None, ("relays_user_message is for a chat passing on the user's reply; a Kanban worker has no "
                      "user turn to relay")
    if _delegated_child():
        return None, "relays_user_message refused: a delegate_task child has no user turn to relay"
    sid = str(session_id or "").strip()
    if not sid:
        return None, "relays_user_message needs an interactive chat session (no session id here)"
    msg = latest_user_message(state_db, sid)
    if msg is None:
        return None, f"relays_user_message refused: session {sid} has no user message to relay"
    message_id, at = msg
    key = f"{target.strip()}|{sid}|{message_id}"
    now = time.time() if now is None else now
    ledger = {k: v for k, v in _ledger(base).items() if isinstance(v, (int, float)) and now - v < LEDGER_KEEP_SECONDS}
    if key in ledger:
        return None, (f"relays_user_message refused: user message {message_id} was already relayed to {target}; "
                      "one relay per user turn. Post it without the flag if it is not a new reply.")
    ledger[key] = now
    write(base / LEDGER, ledger)
    return {"session": sid, "message_id": int(message_id), "at": int(at)}, None
