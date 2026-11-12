"""Gate I: a completion that claims it delivered something to ANOTHER card needs that write's result.

Why (defect kanban-6cf8e9e3fd, 2026-11-12): worker t_01a1d126a2f05af6 completed saying the leftover tests "were posted
on t_01a1d0d5507f91c2". Its operator request was filed 13 s AFTER the completion and refused (the run had ended,
``stale_run``), so nothing was ever posted, and the parent chat relayed the false claim. The stale_run fence was right;
what was missing is a check that intent is not reported as delivery.

A handoff sentence such as "posted on t_X", "commented on t_X", "relayed to t_X" (past tense, not negated, not quoted,
not a question) is a delivery claim. It holds when, on any readable board (live files and ``kanban/sync-shadow``
replicas), there is:

* a comment on ``t_X`` written since this card was created, or
* a ``supervisor_requests`` row from this card naming ``t_X`` with status ``done`` (filed before completion and
  applied: the 01788a16c47 path), or
* ``t_X`` is a card this card created / links to (the content went into that card).

A request that is still ``pending``/``accepted`` is queued, not delivered; a ``rejected``/``failed`` one is a failure.
Either way the refusal says what to write instead. Fails open on unreadable boards.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Callable, Iterable, Optional

TASK_ID = r"t_[0-9a-f]{8}(?:[0-9a-f]{8})?"
# Past-tense delivery verb, then (within the clause) on/to/in + a card id.
CLAIM_RE = re.compile(
    r"\b(?P<verb>posted|commented|delivered|sent|relayed|forwarded|replied|"
    r"left (?:a |the |my )?(?:comment|note|reply)|added (?:a |the |my )?(?:comment|note))\b"
    r"(?P<mid>[^.;\n]{0,80}?)\b(?:on|to|in|onto|into)\s+(?:card\s+|task\s+)?(?P<tid>" + TASK_ID + r")\b",
    re.I)
# Words before the verb that turn the claim into intent, a request, or a failure report.
NOT_CLAIM_RE = re.compile(
    r"\b(?:not|never|no|unable|failed|refused|rejected|will|would|should|could|can|cannot|to be|queued|pending|"
    r"requested|request|asked|asking|awaiting|wait(?:ing)?|planned|plan|intend\w*|tried|attempt\w*|once|if|when|"
    r"before)\b|n't\b", re.I)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
_QUOTED = re.compile(r"\"[^\"\n]{0,400}\"|\u201c[^\u201d\n]{0,400}\u201d|`[^`\n]{0,400}`")
DONE_STATUSES = ("done",)
QUEUED_STATUSES = ("pending", "accepted")


def claimed_deliveries(text: str, own: str) -> list[tuple[str, str]]:
    """``[(target_card, sentence), ...]`` for each delivery claim in ``text`` naming a card other than ``own``."""
    out: list[tuple[str, str]] = []
    for sentence in _SENTENCE_SPLIT.split(_QUOTED.sub('""', text or "")):
        s = sentence.strip()
        if not s or s.endswith("?"):
            continue
        for m in CLAIM_RE.finditer(s):
            tid = m.group("tid")
            if tid == own:
                continue
            before = s[max(0, m.start() - 60):m.start()]
            if NOT_CLAIM_RE.search(before) or NOT_CLAIM_RE.search(m.group("mid")):
                continue
            if (tid, s[:240]) not in out:
                out.append((tid, s[:240]))
    return out


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def shadow_paths(paths: Iterable[Path]) -> list[Path]:
    """Board replicas (``<kanban home>/kanban/sync-shadow/*.db``) next to the given live boards: a comment applied on
    another gateway's card (the operator call's home side) shows up there, not in this gateway's live file."""
    dirs: list[Path] = []
    for p in paths:
        p = Path(p)
        # <home>/kanban.db -> <home>/kanban/sync-shadow; <home>/kanban/boards/<slug>/kanban.db -> same dir
        dirs += [p.parent / "kanban" / "sync-shadow", p.parent.parent.parent / "sync-shadow"]
    out: list[Path] = []
    for d in dict.fromkeys(dirs):
        if d.is_dir():
            out.extend(sorted(d.glob("*.db")))
    return out


def _evidence(own: str, target: str, paths: list[Path], connect: Callable) -> dict:
    """What the boards say about ``own``'s delivery to ``target``."""
    ev: dict = {"comment": False, "related": False, "requests": []}
    since = None
    for p in paths:  # the card's creation time (its own board)
        try:
            conn = connect(p)
        except sqlite3.Error:
            continue
        try:
            row = conn.execute("SELECT created_at FROM tasks WHERE id=?", (own,)).fetchone()
            if row and row[0] is not None:
                since = int(row[0]) if since is None else min(since, int(row[0]))
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    for p in paths:
        try:
            conn = connect(p)
        except sqlite3.Error:
            continue
        try:
            if "task_id" in _cols(conn, "task_comments"):
                row = conn.execute("SELECT 1 FROM task_comments WHERE task_id=? AND created_at >= ? LIMIT 1",
                                   (target, since or 0)).fetchone()
                ev["comment"] = ev["comment"] or bool(row)
            if "requester_task_id" in _cols(conn, "supervisor_requests"):
                for rid, ops, status, error in conn.execute(
                        "SELECT id, ops_json, status, error FROM supervisor_requests WHERE requester_task_id=? "
                        "ORDER BY id", (own,)):
                    try:
                        named = {str(o.get("task_id") or "") for o in json.loads(ops or "[]") if isinstance(o, dict)}
                    except (ValueError, TypeError, AttributeError):
                        named = {target} if target in (ops or "") else set()
                    if target in named:
                        ev["requests"].append((int(rid), str(status or ""), str(error or "")))
            if "parent_id" in _cols(conn, "task_links"):
                if conn.execute("SELECT 1 FROM task_links WHERE (parent_id=? AND child_id=?) OR "
                                "(parent_id=? AND child_id=?) LIMIT 1", (own, target, target, own)).fetchone():
                    ev["related"] = True
            if "kind" in _cols(conn, "task_events"):
                try:
                    if conn.execute("SELECT 1 FROM task_events WHERE task_id=? AND kind='created' AND "
                                    "json_extract(payload,'$.creator_task_id')=? LIMIT 1", (target, own)).fetchone():
                        ev["related"] = True
                except sqlite3.Error:
                    pass
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return ev


def check_deliveries(own: str, handoff: str, paths: list[Path], connect: Callable) -> Optional[str]:
    """Refusal for a handoff whose delivery claims have no write result behind them, or None."""
    claims = claimed_deliveries(handoff, own)
    if not claims:
        return None
    every = list(dict.fromkeys([*paths, *shadow_paths(paths)]))
    for target, sentence in claims:
        ev = _evidence(own, target, every, connect)
        if ev["comment"] or ev["related"] or any(s in DONE_STATUSES for _, s, _ in ev["requests"]):
            continue
        return delivery_refusal(own, target, sentence, ev["requests"])
    return None


def delivery_refusal(own: str, target: str, sentence: str, requests: list[tuple[int, str, str]]) -> str:
    queued = [(r, s) for r, s, _ in requests if s in QUEUED_STATUSES]
    failed = [(r, s, e) for r, s, e in requests if s not in QUEUED_STATUSES and s not in DONE_STATUSES]
    if queued:
        rid, status = queued[-1]
        state = (f"supervisor request #{rid} for {target} is {status}: queued, not delivered. Say so: "
                 f"\"requested an operator comment on {target} (supervisor request #{rid}, {status}); not yet "
                 "delivered\", or wait for its result while this run is live and complete after it is done.")
    elif failed:
        rid, status, err = failed[-1]
        state = (f"supervisor request #{rid} for {target} was {status}" + (f" ({err})" if err else "")
                 + ": nothing was delivered. Report that failure (kanban_block, or a follow-up card) instead.")
    else:
        state = (f"nothing shows it was delivered: no comment on {target} since {own} was created and no supervisor "
                 f"request from {own} naming it. Send it now while this run is live (kanban_comment, or "
                 "kanban_supervisor_request for another gateway's card), wait for the result, then complete; or "
                 "remove the claim and say it was not sent.")
    return (f"[kanban-honest-done] Refused: this handoff claims a delivery to {target} (\"{sentence}\"), but {state} "
            "A write is only reported as done after its result says done; after completion this run can no longer "
            "write (stale_run).")
