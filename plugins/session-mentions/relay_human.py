"""cross_task comment ``relays_user_message``: the origin-side proof that a chat is passing on the user's reply.

kanban-d1b921f32e: a card asks David (``needs_input``), he answers in a chat, and the chat relays the
answer with ``cross_task action=comment``. The courier records every such comment as ``writer_kind='agent'``
(correctly), so the card's no-progress budget saw answered asks as idle runs and stopped the card. With
``relays_user_message=true`` this gateway (the origin, which holds the chat's transcript) verifies the relay
and sends ``relayed_human = {session, message_id, at}``; the receiving core stores it as sent and its
budget counts it as a human answer. ``writer_kind`` stays ``agent``: no operator authority is gained.

Verified here: an interactive chat (not a Kanban worker, not a delegate_task child), whose newest message a
person typed is in this gateway's ``state.db``, and that message not already relayed to the same target (one
relay per user turn per card; ledger under the relay root). Only a plain or ``steer`` user row counts, with
the same filter as core ``hermes_cli/kanban_relayed_human._latest_user_message``: never a synthetic
``role='user'`` row (the card's own ``kanban_notice``, hidden scaffolding, process/delegation completions,
internal notifications, compaction summaries, ``[System:`` notices or cross-session deliveries).

The ledger is written only after the card's gateway accepted the comment (``record``): a failed delivery
leaves the user message relayable. The receiving core is the authority (it refuses a replayed
``(session, message_id)`` and a message older than the card's ask with HTTP 400 ``relayed_human refused:``);
this ledger is a courtesy check that saves the round trip. A receiver whose core predates the field ignores
it.
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
# Same as core kanban_relayed_human: display kinds of a user row a person typed (NULL/'' = a plain prompt),
# and untyped role='user' rows that are still not the user's words.
_TYPED_KINDS = ("steer",)
_SYNTHETIC_PREFIXES = ("[System:", "[Message from another Hermes session")
# The receiving core's HTTP 400 detail for a relay it will not store.
REFUSED_MARKER = "relayed_human refused:"


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
        kinds = ", ".join("?" for _ in _TYPED_KINDS)
        not_synthetic = " ".join("AND instr(ltrim(COALESCE(content, '')), ?) <> 1" for _ in _SYNTHETIC_PREFIXES)
        row = con.execute("SELECT id, timestamp FROM messages WHERE session_id = ? AND role = 'user' "
                          f"AND (COALESCE(display_kind, '') = '' OR display_kind IN ({kinds})) "
                          f"AND COALESCE(_compressed_summary, 0) = 0 {not_synthetic} ORDER BY id DESC LIMIT 1",
                          (session_id, *_TYPED_KINDS, *_SYNTHETIC_PREFIXES)).fetchone()
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


def _key(target: str, relayed: dict) -> str:
    return f"{target.strip()}|{relayed['session']}|{int(relayed['message_id'])}"


def _live(base: Path, now: float) -> dict:
    return {k: v for k, v in _ledger(base).items() if isinstance(v, (int, float)) and now - v < LEDGER_KEEP_SECONDS}


def stamp(target: str, session_id: Optional[str], *, base: Path, state_db: Path,
          now: Optional[float] = None) -> tuple[Optional[dict], Optional[str]]:
    """``(relayed_human, None)`` for a verified relay, else ``(None, refusal)``. Writes nothing: call
    ``record`` once the card's gateway accepted the comment."""
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
    relayed = {"session": sid, "message_id": int(message_id), "at": int(at)}
    now = time.time() if now is None else now
    if _key(target, relayed) in _live(base, now):
        return None, (f"relays_user_message refused: user message {message_id} was already relayed to {target}; "
                      "one relay per user turn. Post it without the flag if it is not a new reply.")
    return relayed, None


def record(target: str, relayed: dict, *, base: Path, write: Callable[[Path, Any], None],
           now: Optional[float] = None) -> None:
    """Mark ``relayed`` as used for ``target``; call only after a successful delivery."""
    now = time.time() if now is None else now
    ledger = _live(base, now)
    ledger[_key(target, relayed)] = now
    write(base / LEDGER, ledger)


def refusal_of(result: Any) -> Optional[str]:
    """The receiving core's reason when a delivery result is its HTTP 400 ``relayed_human refused: ...``
    (every Desktop version passes the error text through), else None."""
    if not isinstance(result, dict) or result.get("ok") is not False:
        return None
    text = str(result.get("error") or "")
    try:  # a raw '400: {"detail": "relayed_human refused: ..."}' error from the Desktop's REST call
        body = json.loads(text[text.index("{"):])
        if isinstance(body, dict):
            text = str(body.get("detail") or body.get("error") or text)
    except ValueError:
        pass
    at = text.find(REFUSED_MARKER)
    if at < 0:
        return None
    return text[at + len(REFUSED_MARKER):].strip() or "refused"
