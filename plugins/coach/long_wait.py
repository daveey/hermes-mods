"""long_wait_pending_reply: refuse one long foreground wait while the user of a live chat has had no reply this turn.

Self-heal agent-f1f8b24e9d: asked "ec2 gateway seems stuck", the agent already had `update exit 0` and HTTP 200, then
ran a foreground `sleep 135; ssh …` "waiting for telemetry" and said nothing until the user asked why it was stuck.
The poll_loop rule only counts repeated polls; this rule covers the first single long wait.

Scope (pre_tool_call): a foreground `terminal` call whose sleeps add up to >= MIN_S seconds, or a `process_manage`
wait with timeout >= MIN_S (no timeout = the terminal default, ~180 s), in an interactive turn (not cron, a subagent
or a Kanban worker) whose user has seen no assistant text or interim message yet, and whose ask does not itself ask
for a delay. background=true is never refused. Held once per turn: the same wait again goes through (logged), so the
rule can never loop. Every check fails open.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

RULE = "long_wait_pending_reply"
MIN_S = 60.0
MESSAGE = ("[coach long_wait_pending_reply] The user is waiting and has had no reply this turn. Reply now with what "
           "you know, then run the wait with terminal(background=true, notify=true) and report the rest when it "
           "lands. (This long foreground wait was not run.)")

_SLEEP = re.compile(r"\bsleep\s+(\d+(?:\.\d+)?)([smhd]?)\b")
_UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
# The user asked for the delay: "wait 2 minutes", "check in 5 min", "after the update finishes", "later".
_ASKS_DELAY = re.compile(
    r"\bwait\b|\blater\b|\bafter\b|\bin\s+(?:\d+(?:\.\d+)?|an?|a\s+few|a\s+couple(?:\s+of)?)\s*"
    r"(?:s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?)\b", re.I)
_WORKER_ENV = ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK")
_NOT_LIVE = ("cron", "kanban")


def wait_seconds(tool: str, args) -> float | None:
    """Seconds this call blocks the turn by design (sleep / process wait), or None for a non-wait or background call."""
    if not isinstance(args, dict):
        return None
    name = str(tool or "").rsplit("__", 1)[-1]
    if name == "terminal":
        if args.get("background"):
            return None
        total = sum(float(n) * _UNIT[u] for n, u in _SLEEP.findall(str(args.get("command") or "")))
        return total or None
    if name == "process_manage" and str(args.get("action") or "") == "wait":
        t = args.get("timeout")
        try:
            return float(t) if t is not None else 180.0
        except (TypeError, ValueError):
            return None
    return None


def asks_delay(ask) -> bool:
    return isinstance(ask, str) and bool(_ASKS_DELAY.search(ask))


def live_turn(platform: str = "", parent_session_id: str = "") -> bool:
    if parent_session_id or str(platform or "").lower() in _NOT_LIVE:
        return False
    return not any(os.environ.get(k) for k in _WORKER_ENV)


def persisted_reply(session_id: str, home: str | None = None) -> bool | None:
    """True when state.db holds an assistant row with text after the session's last user row; False when it holds the
    user row and no such reply; None when unknown (no DB, no rows, read error). Read-only; bounded to 200 rows."""
    if not session_id:
        return None
    path = Path(home or os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")) / "state.db"
    if not path.exists():
        return None
    import sqlite3
    uri = path.resolve().as_uri()
    try:
        try:
            conn = sqlite3.connect(uri + "?mode=ro", uri=True, timeout=0.5)
            conn.execute("select 1 from sqlite_master limit 1")
        except sqlite3.OperationalError:
            # mode=ro fails on a WAL db whose -wal/-shm were deleted: rw + query_only never writes.
            conn = sqlite3.connect(uri + "?mode=rw", uri=True, timeout=0.5)
            conn.execute("PRAGMA query_only=ON")
    except Exception:
        return None
    try:
        rows = conn.execute("SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 200",
                            (session_id,)).fetchall()
    except Exception:
        return None
    finally:
        conn.close()
    for role, content in rows:
        if role == "assistant" and isinstance(content, str) and content.strip():
            return True
        if role == "user":
            return False
    return None


def check(turns: dict, session_id: str, tool: str, args, log) -> str | None:
    """The block message for this call, or None. `turns[session_id]` is the pre_llm_call record
    {"turn", "live", "delay", "replied", "held"}; `log(rec)` writes the coach log."""
    secs = wait_seconds(tool, args)
    if secs is None or secs < MIN_S:
        return None
    t = turns.get(session_id or "")
    if not t or not t.get("live") or t.get("delay") or t.get("replied") or not live_turn():
        return None
    name = str(tool).rsplit("__", 1)[-1]
    if t.get("held"):
        log({"event": "long_wait_repeat", "rule": RULE, "session_id": session_id, "tool": name, "secs": secs})
        return None
    if persisted_reply(session_id):  # the interim hook is queued; text sent with this very call may not be in yet
        t["replied"] = True
        return None
    t["held"] = True
    log({"event": "agent_block", "rule": RULE, "session_id": session_id, "tool": name, "secs": secs})
    return MESSAGE
