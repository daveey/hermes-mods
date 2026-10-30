"""superseding_work: before David is asked to approve a spend or change, surface in-flight work on the same subject.

Self-heal agent-4ee5f101e2: an approval card asked David to fund on-demand CI hosts (+$500/month) while his own
approved migration to autoscaling runners (a blocked card on the same board) would replace them, and a later reply
told him "no sign that CI has moved" after searching only his literal word. Nothing at the ask surface listed
open or recently decided work on that subject.

Deterministic and read-only: topic tokens of the ask (title + action + scope, or a reply plus the user's last
message) are scored against every board's open cards and cards done in the last 7 days (IDF over that set, so
words every card shares count for little). Cards the ask already names by id are dropped; what remains is the
list the agent must name and rule out. The caller holds once per ask; a retry passes.
"""
from __future__ import annotations

import math
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

RECENT_DONE_S = 7 * 86400
MAX_HITS = 6
MIN_SHARED = 3          # distinct shared topic tokens
MIN_COVERAGE = 0.18     # share of the ask's (IDF-weighted) topic the card covers
BODY_CHARS = 4000

# Ask boilerplate: words every approval card carries, which say nothing about its subject.
_ASK_STOP = set("""
approve approval approved decline declined unblock archive archived confirm confirmed cancel cancelled proceed
resume month monthly /month cost costs spend spending budget dollar dollars per hour hours day days week weeks
year years two three four five six yes current currently still new old only both each via using use used
""".split())

_CARD_ID = re.compile(r"\bt_[0-9a-f]{8,}\b")
_DENIAL = re.compile(
    r"\bno\s+(?:sign|evidence|record|trace|indication|mention)s?\b"
    r"|\b(?:found|find|see|saw|turned\s+up)\s+(?:no|nothing)\b"
    r"|\bnothing\s+(?:shows|suggests|indicates)\b"
    r"|\b(?:has|have|hasn|haven)(?:n[’']?t|\s+not)\s+(?:moved|migrated|switched|changed|happened)\b",
    re.I)


def off() -> bool:
    if os.environ.get("ASK_GATE_SUPERSEDING_OFF"):
        return True
    try:
        from hermes_cli.config import load_config_readonly  # type: ignore
        val = (load_config_readonly().get("ask_gate") or {}).get("superseding_work")
        return val is False or str(val).lower() in {"false", "off", "0", "no"}
    except Exception:
        return False


def tokens(core: Any, text: str) -> set[str]:
    """core.topic_tokens, minus card ids/urls/pure numbers, plus the parts of hyphen/slash compounds
    (terraform-aws-github-runner -> runner, on-demand -> demand) so compounds meet plain words."""
    out: set[str] = set()
    for t in core.topic_tokens(text or ""):
        if t.startswith(("t_", "http")) or t.isdigit() or t in _ASK_STOP:
            continue
        out.add(t)
        for p in re.split(r"[-/._:#~+]+", t):
            if len(p) >= 3 and not p.isdigit() and p not in core._TOPIC_STOP and p not in _ASK_STOP:
                out.add(core._stem(p))
    return out


def _rows(db_paths: list[Path], connect: Callable, now: float) -> list[tuple]:
    out = []
    for p in db_paths:
        try:
            c = connect(p, timeout=2)
            try:
                cols = {r[1] for r in c.execute("pragma table_info(tasks)")}
                body = "substr(coalesce(body,''),1,%d)" % BODY_CHARS if "body" in cols else "''"
                q = (f"select id, title, status, completed_at, {body} from tasks where "
                     "(status not in ('done','archived') or (status='done' and coalesce(completed_at,0) > ?))")
                out += [r for r in c.execute(q, (now - RECENT_DONE_S,))]
            finally:
                c.close()
        except sqlite3.Error:
            continue
    return out


def matches(core: Any, query: str, named_text: str = "", exclude: set[str] | None = None,
            db_paths: list[Path] | None = None, now: float | None = None) -> list[dict]:
    """Open / recently-done cards on the ask's subject that the ask does not already name, best first."""
    now = time.time() if now is None else now
    q = tokens(core, query)
    if len(q) < MIN_SHARED:
        return []
    named = set(_CARD_ID.findall(named_text or "")) | set(_CARD_ID.findall(query or "")) | (exclude or set())
    rows = _rows(db_paths if db_paths is not None else core.boards(), core._ro_connect, now)
    docs, seen = [], set()
    for r in rows:
        if r[0] in seen:
            continue
        seen.add(r[0])
        docs.append((r, tokens(core, f"{r[1] or ''}\n{r[4] or ''}")))
    if not docs:
        return []
    df: dict[str, int] = {}
    for _, toks in docs:
        for t in toks & q:
            df[t] = df.get(t, 0) + 1
    n = len(docs)
    idf = {t: math.log(1 + n / c) for t, c in df.items()}
    total = sum(idf.values())
    if total <= 0:
        return []
    hits = []
    for r, toks in docs:
        if r[0] in named:
            continue
        shared = toks & q
        if len(shared) < MIN_SHARED:
            continue
        cov = sum(idf[t] for t in shared) / total
        if cov >= MIN_COVERAGE:
            hits.append({"id": r[0], "title": r[1] or "", "status": r[2], "completed": r[3],
                         "score": round(cov, 3), "shared": sorted(shared)[:8], "n_shared": len(shared)})
    # Open work is what can replace the ask; recently done cards show what was just decided. Open first.
    hits.sort(key=lambda h: (h["status"] != "done", h["score"]), reverse=True)
    return hits[:MAX_HITS]


def render(core: Any, hits: list[dict]) -> str:
    lines = []
    for h in hits:
        when = f" {core.fmt_ts(h['completed'])}" if h.get("status") == "done" and h.get("completed") else ""
        lines.append(f"- {h['id']} [{h['status']}{when}] {h['title'][:100]} (shared: {', '.join(h['shared'][:5])})")
    return "\n".join(lines)


def ask_message(core: Any, hits: list[dict]) -> str:
    return ("ask-gate: superseding_work — this approval ask names none of the open or recently decided cards on "
            "the same subject:\n" + render(core, hits) + "\nRead each one. If one replaces, changes or makes this "
            "ask moot, say so and ask about that instead (or drop the ask). Otherwise add a line naming each card id "
            "and why it does not replace this ask, then file it again. This check holds an ask once; it grants no "
            "approval and changes no task state.")


def denials(text: str) -> list[str]:
    """Sentences that tell the user a change they named has not happened ("I found no sign that X moved")."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [s.strip() for s in parts if _DENIAL.search(s)]


def reply_message(core: Any, hits: list[dict]) -> str:
    return ("[ask-gate superseding_work] Your reply says there is no sign of a change the user named, but these "
            "open or recently decided cards are on the same subject and the reply names none of them:\n"
            + render(core, hits) + "\nSearch for the change itself, not only the user's word for it: read these "
            "cards and say whether one of them is the change the user meant (and its status), or why none is. "
            "Then rewrite the reply. This is a one-time check.")
