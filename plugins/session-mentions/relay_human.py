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
the same filter as core ``hermes_cli/kanban_relayed_human._latest_user_message`` (its synthetic openings and
running ``/loop`` tick read from core when importable, else mirrored): never a synthetic ``role='user'`` row (the
card's own ``kanban_notice``, hidden scaffolding, process/delegation completions, internal notifications,
compaction summaries, the held-draft notice, ``[System:``/``[System note:`` notices or cross-session deliveries).
On top of core's filter it skips the rows hermes-mods writes (``[coach]`` nudges, the coach's restart brief) and
refuses a ``[selfheal-probe]`` session outright (scripted prompts, never a person's reply). Core cannot know those
openings, so the plugin's ``pre_tool_call`` guard (``core_relay_refusal``) refuses core's own
``kanban_comment relays_user_message`` when core's pick would be one of them.

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
# Same as core kanban_relayed_human: display kinds of a user row a person typed (NULL/'' = a plain prompt).
_TYPED_KINDS = ("steer",)
# Core's openings of untyped synthetic user rows (``kanban_relayed_human._synthetic_prefixes()``), mirrored for a
# core without that module: compaction/runtime scaffolding, the pre-final gate's held-draft notice, the ``/loop``
# wakeup and ``/heartbeat`` frames, gateway ``[System:``/``[System note:`` notices and the session-mentions
# courier. With core present core's own list is used, so a nudge registered there is excluded here too.
_CORE_SYNTHETIC_PREFIXES = (
    "[System: Your previous response was truncated", "[System: The previous response was cut off",
    "[System: Your previous tool call", "[Your active task list was preserved across context compression]",
    "[IMPORTANT: Background process ", "[runtime] Your draft above was NOT shown to the user.", "[/loop wakeup #",
    "[Heartbeat — recurring instruction, fires every ", "[System:", "[System note:",
    "[Message from another Hermes session",
)
# Untyped user rows hermes-mods writes that core does not know: the coach's `[coach] ...` nudges (stored as user
# rows by older cores) and its restart brief, the first prompt of a "restart clean" session, which ends with a
# `---\n[coach] restarted clean from @session:...` trailer (plugins/coach/core.brief).
MODS_SYNTHETIC_PREFIXES = ("[coach]",)
MODS_SYNTHETIC_MARKERS = ("\n---\n[coach] restarted clean from ", "\n---\n[coach] moved to the board from ")
# A deliberate live probe of a detector (coach self_heal.PROBE_RE): an agent-launched session whose prompts are
# scripted. Nothing in it is a person's reply, so no relay from it is verified.
PROBE_PREFIX = "[selfheal-probe]"
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


def _core() -> Any:
    """Core's ``hermes_cli.kanban_relayed_human`` (the filter core's own ``verify()`` uses), or None."""
    try:
        from hermes_cli import kanban_relayed_human as krh  # type: ignore
        return krh
    except Exception:
        return None


def core_synthetic_prefixes() -> tuple[str, ...]:
    """Core's openings of untyped synthetic user rows: its live registry when importable (one list, no drift),
    else the mirror ``_CORE_SYNTHETIC_PREFIXES``."""
    fn = getattr(_core(), "_synthetic_prefixes", None)
    if callable(fn):
        try:
            got = tuple(str(p) for p in fn() if p)
            if got:
                return got
        except Exception:
            pass
    return _CORE_SYNTHETIC_PREFIXES


def is_mods_synthetic(content: object) -> bool:
    """A user row hermes-mods wrote (a coach nudge or restart brief), not the user's words."""
    text = str(content or "")
    return text.lstrip().startswith(MODS_SYNTHETIC_PREFIXES) or any(m in text for m in MODS_SYNTHETIC_MARKERS)


def is_probe(content: object) -> bool:
    return str(content or "").lstrip().lower().startswith(PROBE_PREFIX)


def _connect(state_db: Path) -> Optional[sqlite3.Connection]:
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
    return con


def _slash_loop_tick_since(con: sqlite3.Connection, session_id: str) -> Optional[float]:
    """Core ``_slash_loop_tick_since``: when this session's slash-command ``/loop`` tick (``/loop 10m /recap``)
    fired, while it is in flight (``awaiting_response``); its prompt is stored as an untyped row, the loop's, not a
    reply. A wakeup-framed loop's row is caught by its frame instead. ``None`` otherwise."""
    fn = getattr(_core(), "_slash_loop_tick_since", None)
    if callable(fn):
        try:
            return fn(con, session_id)
        except Exception:
            pass
    try:
        row = con.execute("SELECT value FROM state_meta WHERE key = ?", (f"loop:{session_id}",)).fetchone()
        state = json.loads(row[0]) if row and row[0] else None
        if not isinstance(state, dict) or not state.get("awaiting_response"):
            return None
        if not str(state.get("prompt") or "").lstrip().startswith("/"):
            return None
        return float(state.get("last_fired_at") or 0) or None
    except (sqlite3.Error, TypeError, ValueError):
        return None


def _slash_loop_tick_row(con: sqlite3.Connection, session_id: str, prefixes: tuple[str, ...]) -> Optional[int]:
    """Id of an in-flight slash ``/loop`` tick's own prompt row, picked as core picks it: the OLDEST untyped,
    uncompressed, non-synthetic row at/after the fire. ``awaiting_response`` outlives an interrupted tick, so every
    later untyped row is the person's."""
    since = _slash_loop_tick_since(con, session_id)
    if since is None:
        return None
    for row_id, content in con.execute(
            "SELECT id, content FROM messages WHERE session_id = ? AND role = 'user' "
            "AND COALESCE(display_kind, '') = '' AND COALESCE(_compressed_summary, 0) = 0 "
            "AND timestamp >= ? ORDER BY id ASC", (session_id, since - 1)):
        if not str(content or "").lstrip().startswith(prefixes):
            return int(row_id)
    return None


def _user_rows(con: sqlite3.Connection, session_id: str):
    """Untyped/steer, uncompressed ``role='user'`` rows newest first: ``(id, timestamp, content, kind)``."""
    kinds = ", ".join("?" for _ in _TYPED_KINDS)
    return con.execute("SELECT id, timestamp, content, COALESCE(display_kind, '') FROM messages WHERE session_id = ? "
                       f"AND role = 'user' AND (COALESCE(display_kind, '') = '' OR display_kind IN ({kinds})) "
                       "AND COALESCE(_compressed_summary, 0) = 0 ORDER BY id DESC", (session_id, *_TYPED_KINDS))


def latest_user_row(state_db: Path, session_id: str, *, mods: bool = True) -> Optional[tuple[int, float, str]]:
    """``(id, timestamp, content)`` of the session's newest row a person typed, by core's filter
    (``kanban_relayed_human._latest_user_message``: its synthetic openings and in-flight slash ``/loop`` tick
    row, from core when importable) plus, with ``mods``, the hermes-mods rows core does not know
    (``is_mods_synthetic``). ``mods=False`` is exactly core's pick (the row core ``verify()`` would name)."""
    con = _connect(state_db)
    if con is None:
        return None
    prefixes = core_synthetic_prefixes()
    try:
        tick_id = _slash_loop_tick_row(con, session_id, prefixes)
        for message_id, at, content, _kind in _user_rows(con, session_id):
            text = str(content or "")
            if text.lstrip().startswith(prefixes):
                continue
            if tick_id is not None and int(message_id) == tick_id:
                continue  # an in-flight slash /loop tick's own prompt row
            if mods and is_mods_synthetic(text):
                continue
            return int(message_id), float(at), text
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return None


def latest_user_message(state_db: Path, session_id: str) -> Optional[tuple[int, float]]:
    row = latest_user_row(state_db, session_id)
    return (row[0], row[1]) if row else None


def probe_session(state_db: Path, session_id: str) -> bool:
    """True for a ``[selfheal-probe]`` session: its first user prompt opens with the tag (its later turns are
    scripted too, coach self_heal.PROBE_RE)."""
    con = _connect(state_db)
    if con is None:
        return False
    try:
        row = con.execute("SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id LIMIT 1",
                          (session_id,)).fetchone()
    except sqlite3.Error:
        return False
    finally:
        con.close()
    return bool(row) and is_probe(row[0])


def _probe_refusal(sid: str, row: Optional[tuple[int, float, str]], state_db: Path) -> Optional[str]:
    if (row and is_probe(row[2])) or probe_session(state_db, sid):
        return (f"relays_user_message refused: session {sid} is a [selfheal-probe] session; its prompts are "
                "scripted, not a person's reply")
    return None


def core_pick(state_db: Path, session_id: str) -> Optional[tuple[int, float, str]]:
    """The row core ``verify()`` would name: core's own ``_latest_user_message`` when importable, else the mirror
    (``latest_user_row(mods=False)``); ``(id, timestamp, content)``."""
    fn = getattr(_core(), "_latest_user_message", None)
    if not callable(fn):
        return latest_user_row(state_db, session_id, mods=False)
    try:
        got = fn(session_id, str(state_db))
    except Exception:
        return latest_user_row(state_db, session_id, mods=False)
    if not got:
        return None
    con = _connect(state_db)
    if con is None:
        return None
    try:
        row = con.execute("SELECT content FROM messages WHERE id = ?", (int(got[0]),)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return int(got[0]), float(got[1]), str((row or ("",))[0] or "")


def core_relay_refusal(state_db: Path, session_id: Optional[str]) -> Optional[str]:
    """Why core's own ``relays_user_message`` (``kanban_comment``) must not run from this session, else None:
    core's ``verify()`` would name a row hermes-mods wrote (a ``[coach]`` nudge, a restart brief) or a
    ``[selfheal-probe]`` prompt. Core cannot know these mods openings, so the session-mentions ``pre_tool_call``
    guard refuses the call before core verifies it."""
    sid = str(session_id or "").strip()
    if not sid or os.environ.get("HERMES_KANBAN_TASK", "").strip():
        return None  # core refuses these itself
    picked = core_pick(state_db, sid)
    if picked is None:
        return None  # core finds no user message and refuses
    probe = _probe_refusal(sid, picked, state_db)
    if probe:
        return probe
    if is_mods_synthetic(picked[2]):
        return (f"relays_user_message refused: the newest user row in session {sid} (message {picked[0]}) is a "
                "coach nudge or brief, not the user's words. Post the comment without the flag; relay with it "
                "once the user has typed their reply.")
    return None


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
    msg = latest_user_row(state_db, sid)
    probe = _probe_refusal(sid, msg, state_db)
    if probe:
        return None, probe
    if msg is None:
        return None, f"relays_user_message refused: session {sid} has no user message to relay"
    message_id, at, _text = msg
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
