"""refusals: chats the provider's safety filter refused 2+ times in a row (self-heal agent-2e0c97e3a0).

A refusal caused by a chat's earlier history (not the new message) refuses every later message too. Core keeps each
refused ask as a display-only user row with display_kind `content_refused`; 2+ of those in a row among a session's
user asks means the user re-asked and still got no answer, so core's history-triggered recovery (compact + retry
once, then point at /new) did not resolve it. A run made only of automated deliveries (the refused kanban notice or
close-out prompt, then core's automatic retry of it) is still a stuck chat, but its symptom says automated
deliveries were refused, not that the user re-asked.
One signal per session: the fingerprint is the session id, and the
cursor remembers filed sessions so later refusals in the same chat do not refile it.

Stdlib only, Python 3.9+; signals.py loads it by path and passes its own helpers in.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Callable, Dict, List, Optional, Tuple

REFUSED_KIND = "content_refused"
# Core's retry of an automated delivery (cron/card notice) the provider refused: not a human re-ask.
AUTO_RETRY_PREFIX = "[Hermes: automatic retry of"
# The automated delivery the provider refused in the first place: a run opens with it (stored without the retry
# prefix) and core's retry follows. Anchored openings only, so a human question quoting a notice stays human: core's
# kanban notice line ("<glyph> [board] @who Kanban t_...", tui_gateway/session_notifications.py), Hermes plugin
# envelopes, core's resume/job notes and a cross-session relay.
AUTOMATED_RE = re.compile(
    r"^\s*(?:[ℹ✔✓⏸✖⏱🔄⏳⚠]\ufe0f?\s*(?:\[[^\]\n]+\]\s*)?(?:@\S+\s+)?Kanban\s+t_\w+"
    r"|(?i:\[(?:chat-closeout|kanban close-out|owner-wake|false-stop recovery|goal continuation|babysitter)\b)"
    r"|\[System note:|\[Message from another Hermes session|\[ASYNC DELEGATION |\[IMPORTANT: Background process "
    r"|\[IMPORTANT: \d+ (?:Kanban task updates|background process results) arrived\b"
    r"|\[IMPORTANT: \d+ background (?:processes|subagent delegations) completed\b"
    r"|\[IMPORTANT: Watch(?:-pattern | patterns ))")
STREAK_MIN = 2
MAX_FILED = 500  # remembered sessions per db; older ones age out (their fingerprint still dedups on the lane)


def automated(content: str) -> bool:
    """A row Hermes delivered on its own: core's retry of a failed automated delivery, or such a delivery itself."""
    return content.startswith(AUTO_RETRY_PREFIX) or bool(AUTOMATED_RE.match(content))


def refused_streaks(conn: sqlite3.Connection, since_id: int, since_ts: float
                    ) -> List[Tuple[str, int, int, float, str, bool]]:
    """``(session_id, streak, last_row_id, last_ts, last_ask, all_auto)`` for every session whose user asks end a
    run of >= STREAK_MIN consecutive `content_refused` rows at a row newer than ``since_id`` / ``since_ts``.
    ``all_auto`` is true when every row of that run is ``automated``: core's retry (AUTO_RETRY_PREFIX) or the
    original delivery it retries, which opens the run without the prefix. Steers,
    observed rows and compaction summaries (other display kinds / flags) are not asks and neither break nor
    extend a run."""
    sessions = [r[0] for r in conn.execute(
        "select distinct session_id from messages where role='user' and display_kind=? and id>? and timestamp>=?",
        (REFUSED_KIND, since_id, since_ts))]
    out = []
    for sid in sessions:
        rows = conn.execute(
            "select id, timestamp, display_kind, substr(coalesce(content,''),1,200) from messages "
            "where session_id=? and role='user' and coalesce(observed,0)=0 and coalesce(_compressed_summary,0)=0 "
            "and (display_kind is null or display_kind=?) order by id", (sid, REFUSED_KIND)).fetchall()
        best: Optional[Tuple[int, int, float, str, bool]] = None
        run = 0
        all_auto = True  # every row of the current run is an automated delivery or core's retry of one
        for rid, ts, kind, content in rows:
            if kind == REFUSED_KIND:
                all_auto = (all_auto if run else True) and automated(content)
                run += 1
            else:
                run = 0
            if run >= STREAK_MIN and rid > since_id and ts >= since_ts:
                best = (run, rid, ts, content, all_auto)
        if best:
            out.append((sid, *best))
    return out


def collect(home, cur: dict, now: float, host: str, *, homes: Callable, ro: Callable, fp: Callable,
            backfill_s: float) -> List[dict]:
    seen: Dict[str, dict] = cur.setdefault("refusals", {})
    out = []
    for prof, h in homes(home):
        db = h / "state.db"
        if not db.is_file():
            continue
        key = str(db)
        st = seen.setdefault(key, {})
        since_id = int(st.get("last_id") or 0)
        since_ts = float(st.get("since_ts", now - backfill_s))  # first sight looks back backfill_s only
        filed = list(st.get("filed") or [])
        try:
            c = ro(db)
            try:
                top = c.execute("select coalesce(max(id),0) from messages").fetchone()[0]
                streaks = refused_streaks(c, since_id, since_ts)
            finally:
                c.close()
        except sqlite3.Error:
            continue  # no display_kind column (older core) or unreadable: nothing to say
        for sid, n, rid, ts, ask, all_auto in streaks:
            if sid in filed:
                continue
            filed.append(sid)
            who = ("automated deliveries were refused repeatedly (core's automatic delivery retries; no human "
                   "re-ask)" if all_auto else "the user re-asked and got no answer")
            out.append({
                "kind": "refusals", "component": "agent", "at": ts, "session_id": sid, "profile": prof,
                "symptom": f"chat refused by the provider's safety filter {n} times in a row; {who} "
                           "(history-triggered refusal not recovered)",
                "details": f"session {sid} on {host}: {n} consecutive content_refused user rows, latest row {rid}. "
                           "Check agent.log for 'history-triggered refusal: compacted+retried outcome=' lines.",
                "quote": ask, "fp": fp("agent", "refusal_streak", sid),
            })
        st.update(last_id=int(top), since_ts=0.0, filed=filed[-MAX_FILED:])
    return out
