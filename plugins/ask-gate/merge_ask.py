"""merge_ask_hold: merging an approved PR with green CI is agent work, not an ask for David.

Self-heal agent-b62d798d7d. A state PR was independently approved at its exact head with 12/12 checks green, and
for 11 hours every agent treated its merge as David's: the owner chat published an approval request "Merge PR 1714
... now, from the hub, as you" ("Why you decide: its body says 'Owner must merge/apply'", a PR body a worker wrote),
and the build card blocked needs_input with why_human "The card contract reserves PR merge ... for David's owner
identity" (a card body an agent wrote). The structured judge passed both as "approval-tier merge ... which only David
may approve". David never reserved the merge and never answered; another chat finally merged it through the token
broker with no input from him, and two days later he asked "what are we waiting on here, this should be done".

Deterministic, no model call. An ask is held when its ask line (a human_request's action before "Unblock =", or an
action_request's title) asks for a PR's merge or landing, and nothing in action, why_human, scope (or the request's
plan) names a limit only David can change: CI not green, no independent approval, a bypass / branch-protection /
admin merge, missing write access, an account setting, other people, or his own quoted words ("David said ...", a
user-step id). Evidence is not read: it quotes tool output. An unchanged ask is held at most MAX_HOLDS times, then
goes on as before (logged merge_ask_released).
"""
from __future__ import annotations

import re

MAX_HOLDS = 2

_PR = (r"(?:\bPR\s*#?\s*\d+|\bpull\s+request\s*#?\s*\d+|\b[\w.-]+/[\w.-]+#\d+"
       r"|github\.com/[\w.-]+/[\w.-]+/pull/\d+|(?<![\w/])#\d+\b)")
_MERGE = r"\b(?:merg(?:e|es|ing)|land(?:s|ing)?)\b"
# The merge verb, then the PR, in one clause ("merge PR 1714", "landing example/repo#88", "click merge on PR #40").
# Not the other order or the past tense: "until PR 1974 has merged", "wait for PR1311 to land" and "PR 852 merged"
# state a fact or a wait inside an ask about something else; "unmerged" has no word boundary before "merg".
_MERGE_ASK = re.compile(_MERGE + r"[^.;\n]{0,60}?" + _PR, re.I)
_ASK_TAIL = re.compile(r"\bun-?block\s*=", re.I)

# Limits only David can change. Any one in action / why_human / scope / plan releases the hold.
_LIMITS = (
    ("ci_not_green", re.compile(
        r"\b(?:ci|checks?|builds?|tests?)\b[^.;\n]{0,30}?\b(?:is|are|went|turned|still)\s+(?:red|failing|failed|"
        r"pending|broken)\b|\bchecks?\s+(?:is|are)\s+(?:not\s+(?:green|passing))\b", re.I)),
    ("not_approved", re.compile(
        r"\bnot\s+(?:yet\s+)?(?:been\s+)?(?:approved|reviewed)\b|\bunreviewed\b|\bunapproved\b"
        r"|\bwithout\s+(?:an?\s+)?(?:independent\s+)?(?:review|approval|ci|tests?|checks?)\b"
        r"|\bno\s+(?:independent\s+)?(?:review|approval)\b", re.I)),
    ("bypass", re.compile(
        r"\bbypass\w*|\bbranch\s+protection\b|\badmin\s+merge\b|--admin\b|\bforce[- ]?push\w*|\boverride\b", re.I)),
    ("permission", re.compile(
        r"\b(?:lacks?|lacking|no|without|missing|needs?)\s+(?:\w+\s+){0,2}?(?:write|merge|admin)\s+"
        r"(?:access|permissions?|rights?)\b|\b(?:write|read)\s+access\b|\bcredentials?\b|\baccount\s+settings?\b"
        r"|\bemail\b|\bsign[- ]?in\b|\bonly\s+(?:your|his|David's)\s+account\b", re.I)),
    # A repo merge policy that reserves a tier for the owner's exact-head approval (repo-security-audit's red tier,
    # Baby Bear's self-fix security tier) is a real David-only gate; "owner-only"/"owner identity" alone is not.
    ("merge_policy", re.compile(
        r"\bred[- ]tier\b|\bmerge[- ]policy\b|\bsecurity[- ](?:sensitive|tier)\b|\bprotected\s+paths?\b", re.I)),
    ("other_people", re.compile(
        r"\bsomeone\s+else\b|\banother\s+(?:person|author|team|maintainer|reviewer)\b|\b(?:upstream|external)\s+"
        r"maintainers?\b|\btheir\s+(?:repo|PR)\b", re.I)),
)
# David's own words: he is the subject of a speech verb, a user-step id, or a quoted line attributed to him.
_DAVID_QUOTED = re.compile(
    r"\bDavid\s+(?:\w+\s+){0,2}?(?:said|says|asked|asks|wrote|writes|instructed|told|tells|replied|answered"
    r"|requested|directed|typed|decided)\b"
    r"|\bus_[0-9a-f]{6,}\b"
    r"|\bDavid\b[^\n\"“]{0,60}?[\"“][^\"”\n]{8,}[\"”]"
    r"|[\"“][^\"”\n]{8,}[\"”]\s*[—–-]+\s*David\b", re.I)

HOLD_MESSAGE = (
    "ask-gate: merge_ask_hold. This ask hands David the merge of a PR. Under his standing merge approval "
    "(~/AGENTS.md: land through the repo's gate with independent exact-head approval and required CI) merging an "
    "approved PR with green required CI is agent work: land it yourself through the gate (`hermes-core-wt ship`, or "
    "`python3 plugins/push-guard/land.py --pr <n> --head <full-sha>` in hermes-mods). `token-broker exec --scope "
    "user/github` acts as the owner's GitHub identity, so 'owner identity' or 'his Mac is offline' is no reason to "
    "wait. A card contract, PR body, plan or another agent's note that reserves the merge is an agent's condition, "
    "not David's: kanban_comment the correction on that card and land it. Ask David only when the merge crosses a "
    "limit only he can change, and name it in why_human: CI not green, no independent approval, a bypass / branch "
    "protection / admin merge, missing write access, other people, or his own quoted words (\"David said ...\", a "
    "us_ id). If the ask bundles a step that really is his (e.g. a Terraform apply under his SSO), ask for that "
    "step alone.")


def _ask_line(text: str) -> str:
    return _ASK_TAIL.split(" ".join(str(text or "").split()), 1)[0]


def _hit(ask: str, context: list[str]) -> dict | None:
    m = _MERGE_ASK.search(ask)
    if not m:
        return None
    if any(_DAVID_QUOTED.search(f) for f in context):
        return None
    for _name, rx in _LIMITS:
        if any(rx.search(f) for f in context):
            return None
    start = max(0, m.start() - 80)
    return {"quote": ask[start:m.end() + 80].strip()[:240]}


def hit(request: dict) -> dict | None:
    """{"quote": ...} when a human_request asks David to merge/land a PR and names no limit only he can change."""
    fields = [str(request.get(k) or "") for k in ("action", "why_human", "scope")]
    return _hit(_ask_line(fields[0]), fields)


def action_request_hit(req: dict) -> dict | None:
    """The same rule for a user-steps action_request (approval/choice): the title is the ask line."""
    if not isinstance(req, dict) or str(req.get("kind") or "") not in ("approval", "choice"):
        return None
    scope = req.get("scope")
    plan = str(scope.get("markdown") or "") if isinstance(scope, dict) else ""
    title = " ".join(str(req.get("title") or "").split())
    return _hit(title, [title, plan])
