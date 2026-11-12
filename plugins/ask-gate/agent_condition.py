"""agent_condition_hold: an "ask first" condition an agent set is answered by that agent, not David.

Self-heal agent-ad97832ad7. On 2026-11-12 an orchestrator applied the exact-head-pinned bb-size-ok label at David's
instruction and then told the worker card "operator says ask before any new head" (its own condition: "so I told it
to ask before pushing again"). The worker blocked needs_input asking David to authorize a test-only CI repair, with
why_human "The operator explicitly prohibited pushing any different head ... and required asking before a new
head." The structured judge passed it; the orchestrator lifted its own rule 23 s later.

Deterministic, no model call. A human_request is held when its action/why_human attributes the requirement to an
agent (operator, orchestrator, creator, parent card, card comment, steer, "my own condition") together with
ask/approve/permission/prohibit/require language, and nothing in it quotes David's own words ("David said/asked/
wrote/instructed ...", a user-step id us_..., or a quoted line attributed to David). An unchanged ask is held at most
MAX_HOLDS times, then goes on to the judge (whose STRUCTURED_SYSTEM carries the same rule).
"""
from __future__ import annotations

import re

MAX_HOLDS = 2

_AGENT = (r"(?:operator|orchestrator|creator(?:\s+(?:card|agent|task|chat))?|parent\s+(?:card|task|agent)"
          r"|managing\s+(?:chat|session|agent)|supervisor(?:\s+agent)?|card\s+comment|(?:operator\s+|orchestrator\s+)?steer)")
_VERB = (r"(?:says|said|told|tells|asked|asks|requir\w*|prohibit\w*|forb[ia]d\w*|instruct\w*|want\w*|insist\w*"
         r"|demand\w*|direct\w*|order\w*|imposed|added|set|restrict\w*|disallow\w*)")
# The requirement is attributed to an agent: "the operator (explicitly) prohibited", "orchestrator's condition",
# "per the operator steer", "my own condition", "self-imposed".
_ATTRIBUTED = re.compile(
    r"\b" + _AGENT + r"\b(?:\s+\w+){0,3}?\s+" + _VERB + r"\b"
    r"|\b" + _AGENT + r"(?:'s|’s)\s+(?:own\s+)?(?:condition|rule|requirement|instruction|steer|prohibition|direction"
    r"|order|restriction|gate)\b"
    r"|\b(?:per|under|by|from)\s+(?:the\s+|an?\s+)?" + _AGENT + r"\b"
    r"|\bmy\s+own\s+(?:condition|rule|requirement)\b|\bself-imposed\b", re.I)
_ASKING = re.compile(
    r"\b(?:ask\w*|approv\w*|permission\w*|prohibit\w*|forb[ia]d\w*|requir\w*|authori[sz]\w*|sign-?off|confirm\w*"
    r"|clearance|consent|ok\s+from|go-?ahead)\b", re.I)
# David's own words: he is the subject of a speech verb, a user-step id, or a quoted line attributed to him.
_DAVID_QUOTED = re.compile(
    r"\bDavid\s+(?:\w+\s+){0,2}?(?:said|says|asked|asks|wrote|writes|instructed|told|tells|replied|answered"
    r"|requested|directed|typed|decided)\b"
    r"|\bus_[0-9a-f]{6,}\b"
    r"|\bDavid\b[^\n\"“]{0,60}?[\"“][^\"”\n]{8,}[\"”]"
    r"|[\"“][^\"”\n]{8,}[\"”]\s*[—–-]+\s*David\b", re.I)

HOLD_MESSAGE = (
    "ask-gate: agent_condition_hold. This ask's reason is a condition an agent set (an operator/orchestrator/"
    "creator steer, a card comment, your own rule), not David: nothing in it quotes David's own words. Do not ask "
    "David to lift another agent's condition. Either apply your in-scope recommendation (an exact-head approval or "
    "label is re-checked by its own gate, so a new head bypasses nothing: re-apply or re-request it for the new "
    "head the normal way), or put the question on your card with kanban_comment, addressed to the agent that set "
    "the condition (the managing chat/creator answers it). Ask David only when the requirement is his: quote his "
    "words (\"David said ...\", a user-step id) in why_human or evidence.")


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?;])\s+|\n+", text or "") if s.strip()]


def hit(request: dict) -> dict | None:
    """{"quote": sentence} when the request attributes its human-only reason to an agent's condition, else None."""
    fields = [str(request.get(k) or "") for k in ("action", "why_human", "evidence", "scope")]
    if any(_DAVID_QUOTED.search(f) for f in fields):
        return None
    for field in fields[:2]:  # action + why_human carry the attribution
        for s in _sentences(field):
            if _ATTRIBUTED.search(s) and _ASKING.search(s):
                return {"quote": " ".join(s.split())[:240]}
    return None
