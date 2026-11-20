"""unblock_card_quality: a blocked card's human_request must carry the decision brief David needs.

Why: session summaries kept showing blocked cards David could not act on (2026-11-20, card t_01a1f8b56d1a69fc:
"Reply A or B in this chat. A = authorize one bounded repair run against the current Mac Desktop (PID 5198 ...)":
jargon, no situation, no consequences per option, no recommendation). The ask-gate judge checks whether an ask
should exist, not whether it is answerable. The skills try-to-unblock and generate-unblock-card hold the reasoning;
this rule only checks that their output is present, so the agent cannot skip them.

Deterministic, no model call. Checked, on kanban_block / blocked kanban_create with a human_request:
- why_human names what was tried (``Tried:``): try-to-unblock ran.
- scope has ``Situation:`` and ``Recommend:`` (or ``Recommendation:``) parts with real text.
- a decision (interaction approval|reply) also has ``Options:`` with two or more options, counting option lines
  (``A) ...``, ``- B: ...``) under it, or ``choices`` when given.
- when ``choices`` are given, exactly one label carries ``(recommended)``.
Bounded by MAX_HOLDS per (session, tool, card) by the callers, then the ask passes. Callers fail open on error.
"""
from __future__ import annotations

import os
import re

MAX_HOLDS = 2
# Test isolation only: suites whose fixtures predate this rule set ASK_GATE_UNBLOCK_BRIEF=0 (their conftest).
ENV_SWITCH = "ASK_GATE_UNBLOCK_BRIEF"
DECISION_INTERACTIONS = {"approval", "reply"}
MIN_PART_CHARS = 20

_LABEL = r"(?:^|\n)[ \t]*(?:[-*>][ \t]*)?(?:\*\*|__)?{name}(?:\*\*|__)?[ \t]*:(?:\*\*|__)?"
_PARTS = {
    "situation": re.compile(_LABEL.format(name=r"situation"), re.I),
    "options": re.compile(_LABEL.format(name=r"options?"), re.I),
    "recommend": re.compile(_LABEL.format(name=r"recommend(?:ation|ed)?"), re.I),
}
_ANY_PART = re.compile(_LABEL.format(name=r"(?:situation|options?|recommend(?:ation|ed)?)"), re.I)
_TRIED = re.compile(r"\btried\s*:", re.I)
_OPTION_LINE = re.compile(r"^[ \t]*(?:[-*][ \t]*)?(?:\*\*)?\(?[A-F](?:\)|\.|:|\s+[-—–])", re.M)
_RECOMMENDED = re.compile(r"\(recommended\)", re.I)

HOLD_MESSAGE = (
    "ask-gate: unblock_card_hold. This blocked card would reach David without the brief he needs to answer it "
    "from the panel ({missing}). Load the try-to-unblock skill and check whether you can clear it yourself; if "
    "David is truly needed, load generate-unblock-card and rewrite the human_request: why_human starts "
    "'Tried: ...'; scope has 'Situation:' (2-3 plain sentences, no internal jargon), 'Options:' (one line per "
    "option: what happens, cost, reversible?) and 'Recommend: <option> - <reason>'; choices mark exactly one "
    "label '(recommended)'. Then file it again."
)


def _section(scope: str, name: str) -> str | None:
    """Text of a labelled part up to the next labelled part, or None when the label is absent."""
    m = _PARTS[name].search(scope)
    if not m:
        return None
    rest = scope[m.end():]
    nxt = _ANY_PART.search(rest)
    return (rest[: nxt.start()] if nxt else rest).strip()


def missing(request: dict) -> list[str]:
    """Names of the missing parts; empty when the request carries the whole brief."""
    if not isinstance(request, dict):
        return []
    scope = str(request.get("scope") or "")
    why = str(request.get("why_human") or "")
    interaction = str(request.get("interaction") or "").strip()
    choices = request.get("choices") if isinstance(request.get("choices"), list) else None
    out: list[str] = []
    if not _TRIED.search(why):
        out.append("why_human has no 'Tried:'")
    situation = _section(scope, "situation")
    if situation is None or len(situation) < MIN_PART_CHARS:
        out.append("scope has no 'Situation:'")
    recommend = _section(scope, "recommend")
    if recommend is None or len(recommend) < MIN_PART_CHARS:
        out.append("scope has no 'Recommend:' with a reason")
    if interaction in DECISION_INTERACTIONS:
        options = _section(scope, "options")
        n_lines = len(_OPTION_LINE.findall(options)) if options else 0
        if options is None or (n_lines < 2 and not (choices and len(choices) >= 2)):
            out.append("scope has no 'Options:' listing two or more options")
    if choices:
        marked = [c for c in choices if isinstance(c, dict) and _RECOMMENDED.search(str(c.get("label") or ""))]
        if len(marked) != 1:
            out.append(f"{len(marked)} choices are labelled '(recommended)', expected exactly one")
    return out


def hit(request: dict) -> dict | None:
    """None when the brief is complete, else {'missing': [...], 'message': str}."""
    if os.environ.get(ENV_SWITCH, "1") == "0":
        return None
    gaps = missing(request)
    if not gaps:
        return None
    return {"missing": gaps, "message": HOLD_MESSAGE.format(missing="; ".join(gaps))}
