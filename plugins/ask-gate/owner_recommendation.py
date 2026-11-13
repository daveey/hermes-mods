"""owner_recommendation_hold: a pick between options inside the card's limits is applied, not handed to David.

Red-sweep slug owner-recommendation-not-applied (prevention card t_01a1d6a03ce6f54b). On 2026-11-13 a red sweep found
four cards blocked on David with a choice an agent could make itself; the sweep applied each pick under his standing
rule ("use your best judgement... i probly tell you to do it"):
- worker-5 t_01a1d0866e63dd56: grant two runner GitHub Apps read-only Pull requests/Commit statuses permissions?
- worker-6 t_01a1d5b9382b24ad: macOS tests-os lane A) PR lane B) scheduled non-required C) keep manual?
- worker-7 t_01a1d4eee7cc5b15: register claude-fable-5-2 on the Mac and mini like the hub (subscription quota)?
- macbook t_01a1cf6f00320b7e: also count David's own iPhone in the room-accuracy run, under the same limits?
The kanban-worker skill already said "apply your recommendation" (references/owner-recommendation.md); nothing at
the ask surface checked it, and the structured judge passes every "choice between options" as ok.

Deterministic, no model call. A human_request is held when it is a pick (two or more choices, or "reply yes or no /
A or B / pick / choose / decide" wording), its interaction is reply/approval, and its action, why_human and scope
name none of the limits only David can change: real money (a $ amount, budget/cap, paid plan, cloud spend),
an acceptance threshold or required gate, credentials or a write/admin permission (a read-only grant is not one),
other people, safety/consent/hard-to-reverse effects, a step only a human can do, or a rule David stated (quoted).
Evidence is not read: it cites tool output ("read-only installation token") that must not release the hold.
An unchanged ask is held at most MAX_HOLDS times, then passes (logged owner_recommendation_released).
"""
from __future__ import annotations

import re

MAX_HOLDS = 2

_INTERACTIONS = {"reply", "approval"}

_PICK = re.compile(
    r"\b(?:reply|answer|respond)\s+(?:with\s+)?(?:(?:a\s+)?yes\s+or\s+no|[A-E]\s*(?:,\s*[A-E]\s*)*(?:or|/)\s*[A-E]\b"
    r"|with\s+(?:the\s+|your\s+)?(?:letter|pick|choice|option))"
    r"|\b(?:pick|choose|select)\s+(?:one|which|between|how|whether|an?\s+option|the\s+option)\b"
    r"|\bdecide\s+(?:whether|which|between|how)\b|\bwhich\s+(?:option|one|way|lane|route)\b"
    r"|(?:^|\s)A\)\s.+\sB\)\s", re.I | re.S)

# Limits only David can change. Each releases the hold when named in action, why_human or scope.
_LIMITS = (
    ("money", re.compile(
        r"[$€£]\s?\d|\b\d[\d.,]*\s*(?:usd|dollars?|eur|gbp)\b"
        r"|\b(?:budget|invoice|purchas\w*|buy(?:ing)?|refund\w*|pay(?:ment|ing)?|paid\s+(?:plan|tier|seats?|capacity"
        r"|runners?|service|account|minutes))\b"
        r"|\b(?:spend(?:ing)?|cost)\s+(?:cap|limit|envelope|ceiling)\b|\bstanding\s+(?:cap|envelope)\b"
        r"|\b(?:aws|gcp|azure|cloud|gpu|vast|runpod|blacksmith|api)\s+(?:spend|costs?|bill\w*|charges?)\b"
        r"|\brecurring\s+(?:\w+\s+)?(?:spend|costs?|charges?|fees?)\b|\bbilling\s+(?:change|plan|account)\b", re.I)),
    ("threshold", re.compile(
        r"\bacceptance(?:\s+criteri(?:a|on))?\b"
        r"|\b(?:lower|relax|waiv|weaken|drop|reduc|loosen|chang|amend|revis)\w*\s+(?:the\s+|its\s+|a\s+|our\s+)?"
        r"(?:\w+\s+){0,2}(?:target|threshold|bar|floor|criteri(?:a|on)|slo|required\s+checks?)\b"
        r"|\b(?:below|short\s+of)\s+(?:the\s+)?(?:target|threshold|bar)\b"
        r"|\b(?:bypass\w*|skip\w*|disabl\w*)\s+(?:the\s+)?(?:review|required\s+(?:ci|checks?)|gate|ship\s+gate)\b", re.I)),
    ("credential", re.compile(
        r"\b(?:passwords?|passcodes?|2fa|mfa|otp|api\s+keys?|secrets?|private\s+keys?|credentials?|tokens?"
        r"|sign(?:s|ing)?\s+in|log(?:s|ging)?\s?in|keychain)\b", re.I)),
    ("other_people", re.compile(
        r"\b(?:Rose|wife|family|kids?|guests?|colleagues?|coworkers?|co-workers?|teammates?|customers?|end\s+users"
        r"|external\s+\w+|third[- ]part(?:y|ies)|other\s+people|someone\s+else|publicly|publish\w*|announc\w*"
        r"|email\s+(?:to\s+)?\w+|post\s+(?:to|on)\s+\w+)\b", re.I)),
    ("safety", re.compile(
        r"\b(?:irreversible|hard\s+to\s+(?:reverse|undo)|cannot\s+be\s+undone|one-way|permanent\w*|destructive"
        r"|data\s+loss|delet(?:e|es|ing)|wip(?:e|es|ing)|purg\w+|force-?push\w*|security|safety|outage|downtime"
        r"|consent|production\s+data)\b", re.I)),
    ("human_only", re.compile(
        r"\b(?:physical(?:ly)?|in\s+person|by\s+hand|on\s+(?:your|his)\s+(?:phone|watch|device)|captcha)\b", re.I)),
    ("david_rule", re.compile(
        r"\bDavid\s+(?:\w+\s+){0,2}?(?:said|says|asked|wrote|instructed|told|requested|directed|decided)\b"
        r"|\bus_[0-9a-f]{6,}\b", re.I)),
)

# A permission change releases only when it is not read-only.
# ("scope" is not a permission word here: asks say "widens the scope" of the work itself.)
_PERMISSION = re.compile(r"\b(?:permissions?|access|roles?|entitlements?|grant(?:s|ed|ing)?)\b", re.I)
_READ_ONLY = re.compile(r"\bread-?only\b|:\s*read\b|\bread\s+(?:perms?|permissions?|access|scopes?)\b", re.I)
_ELEVATED = re.compile(
    r"\b(?:write|read[/-]write|admin\w*|owner|maintain\w*|elevated|full|org-wide|root)\s+"
    r"(?:access|permissions?|roles?|rights|scopes?)\b|:\s*(?:write|admin)\b|\bsudo\b", re.I)

HOLD_MESSAGE = (
    "ask-gate: owner_recommendation_hold. This ask hands David a pick between options that stays inside the card's "
    "limits: it names no real money over a standing envelope, credential or write/admin permission, other people, "
    "safety/consent or hard-to-reverse effect, acceptance-threshold change, human-only step or rule David stated. "
    "David's standing rule: apply your recommendation and comment how to reverse. Pick the option the evidence "
    "supports (lowest cost or risk when it is close), kanban_comment the pick, why, and the exact reversal (the "
    "commit to revert, the setting to restore, the card to archive), then continue through the normal review, CI and "
    "landing gates. If you cannot recommend, gather the evidence that decides it first. Ask David only when a limit "
    "really changes: name it in why_human (\"$40 over the $25 GPU cap\", \"lowers the 50% target\", \"grants write "
    "access\", \"affects Rose\", \"David said: ask me before X\").")


def limit_named(request: dict) -> str | None:
    """The first David-only limit the request names in action/why_human/scope, else None."""
    text = "\n".join(str(request.get(k) or "") for k in ("action", "why_human", "scope"))
    for name, rx in _LIMITS:
        if rx.search(text):
            return name
    if _PERMISSION.search(text) and (_ELEVATED.search(text) or not _READ_ONLY.search(text)):
        return "permission"
    return None


def is_pick(request: dict) -> bool:
    choices = request.get("choices")
    if isinstance(choices, list) and len(choices) >= 2:
        return True
    return bool(_PICK.search(str(request.get("action") or "")))


def hit(request: dict) -> dict | None:
    """{"quote": action} when the request is an in-limits pick David's standing rule says to apply, else None."""
    if str(request.get("interaction") or "").strip().lower() not in _INTERACTIONS:
        return None
    if not is_pick(request) or limit_named(request):
        return None
    return {"quote": " ".join(str(request.get("action") or "").split())[:240]}
