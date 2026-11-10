"""replan_hold: an approved goal whose first plan ran out is replanned, not handed back to David.

Self-heal agent-866a12f54f. On 2026-11-02 the babybear CI-cost card t_c21c170e (goal: cut Actions spend >=50%
without losing coverage) blocked needs_input with "Decide whether to end the capacity project with its >=50%
equal-workload target explicitly unproven (recommended), or expand scope to a separately reviewed test", and the hub
chat relayed it to David three times. Changing the approach to an approved goal is not changing the goal; only a
changed limit (spend, permission, acceptance, safety) or a human-only step needs him. The judge passed the ask as
a "choice between options", so nothing at the ask surface checked it.

Deterministic, no model call. An ask is held when it is shaped like "end/stop with the target unmet/unproven" or
"expand scope / new scope / redesign / new approach" and names no changed limit. Chat replies and clarify use a
stricter shape (no bare "redesign"/"new approach": in a live chat David may be choosing a design). Naming the
limit releases the hold; an unchanged ask is held at most MAX_HOLDS times, then passes (logged replan_released).
"""
from __future__ import annotations

import re

MAX_HOLDS = 2

_END = re.compile(
    r"\b(?:end(?:ing|s)?|stop(?:ping|s)?|clos(?:e|es|ing)|abandon(?:ing|s)?|drop(?:ping|s)?|giv(?:e|es|ing)\s+up"
    r"|wind(?:ing)?\s+down|shelv(?:e|es|ing)|halt(?:ing|s)?|conclud(?:e|es|ing)|wrap(?:ping)?\s+up)\b"
    r"[^.!?\n]{0,140}?"
    r"\b(?:unproven|unmet|unreached|unachieved|missed|short\s+of|below\s+(?:the\s+)?(?:target|goal)"
    r"|not\s+(?:yet\s+)?(?:met|proven|reached|achieved|hit|demonstrated)"
    r"|without\s+(?:meeting|reaching|hitting|proving|achieving))\b", re.I)
_SCOPE_STRICT = re.compile(
    r"\b(?:expand(?:s|ing|ed)?|widen(?:s|ing)?|broaden(?:s|ing)?|extend(?:s|ing)?|enlarg\w*|increas\w*)\s+"
    r"(?:the\s+|its\s+|our\s+)?scope\b"
    r"|\bscope\s+(?:expansion|increase|creep)\b|\bnew\s+scope\b|\bexpanded\s+scope\b"
    r"|\bbeyond\s+(?:the\s+)?(?:approved|original|current|initial)\s+scope\b"
    r"|\b(?:original|approved|initial|current)\s+scope\s+(?:is\s+|was\s+)?(?:exhausted|used\s+up|done)\b", re.I)
_SCOPE_BROAD = re.compile(
    _SCOPE_STRICT.pattern
    + r"|\bre-?design(?:s|ing)?\b|\b(?:new|different|alternative|another|fresh)\s+approach\b"
    r"|\bre-?plan(?:s|ning|ned)?\b|\bpivot(?:s|ing)?\b", re.I)
_DECISION = re.compile(
    r"\b(?:decide|decision|whether|choose|pick|your\s+call|up\s+to\s+you|approve|confirm|should\s+(?:I|we)"
    r"|want\s+me\s+to|do\s+you\s+want|prefer)\b|\bor\b", re.I)

# A named changed limit: the ask says WHICH constraint of the approved goal would change.
_LIMITS = (
    ("spend", re.compile(
        r"\+\s?\$\s?\d|\$\s?\d[\d.,]*\s*(?:k\b)?\s*(?:more|extra|over|additional)\b"
        r"|\$\s?\d[\d.,]*\s*(?:/|per\s+)\s*(?:mo|month|hour|hr|day|week|year)\b"
        r"|\b(?:over|exceed\w*|above|beyond|rais\w*|increas\w*)\s+(?:the\s+)?(?:\$\s?\d[\d.,]*\s+)?(?:standing\s+)?"
        r"(?:budget|cap|spend(?:ing)?\s+limit)\b"
        r"|\b(?:more|extra|additional|recurring|new)\s+(?:aws\s+|gpu\s+|cloud\s+)?(?:spend|spending|money|cost|budget)\b",
        re.I)),
    ("permission", re.compile(
        r"\b(?:new|more|extra|additional|broader|elevated|admin)\s+(?:permissions?|access|credentials?|roles?)\b"
        r"|\bgrant(?:s|ing)?\s+(?:\w+\s+){0,3}(?:access|permissions?|roles?)\b|\bpermission\s+(?:change|grant)\b"
        r"|\bsudo\b", re.I)),
    ("acceptance", re.compile(
        r"\bacceptance(?:\s+criteri(?:a|on))?\b|\b(?:chang|lower|relax|drop|waiv|weaken|revis|amend|reduc)\w*\s+"
        r"(?:the\s+|its\s+)?(?:approved\s+)?(?:criteri(?:a|on)|target|threshold|bar|goal)\b", re.I)),
    ("safety", re.compile(
        r"\b(?:irreversible|destructive|data\s+loss|delet(?:e|es|ing)|security|safety|outage)\b", re.I)),
    ("human_only", re.compile(
        r"\b(?:physical(?:ly)?|in\s+person|sign(?:s|ing)?\s+in|password|2fa|captcha|his\s+(?:phone|device))\b", re.I)),
)

HOLD_MESSAGE = (
    "ask-gate: replan_hold. This ask hands David a choice between ending an approved goal with its target unmet and "
    "a new approach to it (\"expand scope\", \"redesign\", \"new approach\"). A new approach to the same approved goal "
    "is not new scope: approval covers the goal and its limits, not the first plan. Write the new approach on the "
    "owner card (kanban_comment), get an independent review (a reviewer card, or delegate_task pinned to "
    "claude-opus-5-5 + anthropic), then continue the work. Ask David only when a limit changes: name it in "
    "why_human/the ask (spend, permission, acceptance or safety: e.g. \"$40 over the approved cap\", \"needs admin "
    "access\", \"lowers the 50% target\") or the step only a human can do.")


def limit_named(text: str) -> str | None:
    for name, rx in _LIMITS:
        if rx.search(text or ""):
            return name
    return None


def shape(text: str, broad: bool = True) -> str | None:
    """"end_unmet" / "new_scope" when the text is shaped like a give-up-or-expand-scope ask, else None."""
    text = text or ""
    if _END.search(text):
        return "end_unmet"
    if (_SCOPE_BROAD if broad else _SCOPE_STRICT).search(text):
        return "new_scope"
    return None


def structured_hit(text: str) -> dict | None:
    """Kanban asks (human_request action + why_human, or a needs_input block reason)."""
    s = shape(text, broad=True)
    if s is None or limit_named(text):
        return None
    return {"shape": s, "quote": " ".join(text.split())[:240]}


def chat_hits(asks: list[str]) -> list[str]:
    """Chat-reply/clarify asks with the strict shape and a decision cue, naming no changed limit."""
    out = []
    for a in asks or []:
        if not isinstance(a, str):
            continue
        s = shape(a, broad=False)
        if s is None or limit_named(a):
            continue
        if s == "new_scope" and not _DECISION.search(a):
            continue
        out.append(a)
    return out


def ask_contexts(asks: list[str], reply: str) -> list[str]:
    """Each ask with the reply paragraph it sits in (the ask itself when it is not found)."""
    paras = [p for p in re.split(r"\n\s*\n", reply or "") if p.strip()]
    out = []
    for a in asks or []:
        if not isinstance(a, str):
            continue
        head = " ".join(a.split())[:60]
        para = next((p for p in paras if head and head in " ".join(p.split())), None)
        out.append(para if para is not None else a)
    return out
