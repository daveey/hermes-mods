"""Bounded evidence-quality nudges, never an execution/authorization decision.

A region-scoped AWS error cannot establish that all SSM access is unavailable.
Only inspect observed tool results and a new card's present-tense premise; do not
probe AWS, infer grants, or widen permissions. Unmatched prose goes to existing gates.
"""
from __future__ import annotations

import re

MAX_HOLDS = 2
_REGION = re.compile(r"\b[a-z]{2}(?:-gov)?-[a-z]+-\d\b")
_DENIED = re.compile(r"AccessDenied|UnauthorizedOperation|not authorized to perform", re.I)
_CLAIM = re.compile(
    r"\bSSM\b[^.!?\n]{0,90}\b(?:refused|denied|unavailable|blocked)\b"
    r"|\b(?:cannot|can't|couldn't|no)\b[^.!?\n]{0,90}\bSSM\b", re.I)


def regional_claim(text: str, tools: list[dict]) -> dict | None:
    regions = set()
    for tool in tools:
        args, result = str(tool.get('args') or ''), str(tool.get('result') or '')
        # A quoted card/skill containing an old denial isn't a current observation.
        if tool.get('tool') not in {'terminal', 'execute_code'}:
            continue
        if not re.search(r'\baws\b.*\bssm\b', args, re.I | re.S) or not _DENIED.search(result):
            continue
        regions.update(_REGION.findall(args + '\n' + result))
    if not regions:
        return None
    for sentence in re.split(r'(?<=[.!?])\s+|\n+', text):
        if not _CLAIM.search(sentence):
            continue
        if (re.search(r'\bSSM\s+was\s+(?:previously|earlier)\b', sentence, re.I)
                or re.search(r'\b(?:premise|claim)\s+was\s+(?:wrong|incorrect)\b', sentence, re.I)):
            continue  # historical correction, not a new capability premise
        stated = set(_REGION.findall(sentence))
        if stated and stated <= regions:
            continue
        return {'quote': sentence[:240], 'regions': sorted(regions)}
    return None


_WORKER_POLICY = re.compile(
    r'\bworker\b[^.!?\n]{0,90}\b(?:protocol|instructions?|rules?)\b[^.!?\n]{0,90}'
    r'\b(?:prohibits?|forbids?|disallows?)\b[^.!?\n]{0,90}\bCLI\b', re.I)
_HUMAN_BOUNDARY = re.compile(
    r'\b(?:password|authenticate|authentication|sudo|sign.in|log.in|OAuth|consent|credential|'
    r'physical|destructive|irreversible|spend|budget|user said|user requested)\b', re.I)


def tool_policy_request(request: dict) -> dict | None:
    if request.get('interaction') != 'user_terminal':
        return None
    why = str(request.get('why_human') or '')
    if not _WORKER_POLICY.search(why):
        return None
    # Even a mixed request still goes through the ordinary human/authorization gates.
    text = ' '.join(str(request.get(k) or '') for k in ('action', 'why_human', 'scope', 'evidence'))
    if _HUMAN_BOUNDARY.search(text):
        return None
    return {'quote': why[:240]}


POLICY_MESSAGE = (
    'ask-gate: tool_policy_escalation. A missing worker tool or the worker CLI prohibition is not evidence '
    'that the user must authenticate. Keep that prohibition: do not bypass it via shell, SSH or SSM. '
    'Use a supported supervisor/operator handoff or document the missing operation for its owner; '
    'do not turn agent routing work into a user-terminal ask. If a real human-only boundary also exists, '
    'state it with evidence and retain the supported user-terminal/approval flow. '
    'This hold grants no execution authority. A notification, unblock, archive or request withdrawal '
    'is not execution consent; legitimate in-scope agent recovery needs its own existing authority.'
)


def regional_message(hit: dict) -> str:
    return (
        'ask-gate: regional_access_scope. Observed SSM denial is scoped to '
        + ', '.join(hit['regions']) + ', not blanket loss of access. '
        'State the failed principal/action/resource/region precisely. Discover the intended target region '
        'from trusted configuration or inventory using read-only checks before escalating; do not try arbitrary '
        'regions or send-command as an access probe. This correction grants no execution authority: keep '
        'user-terminal, approval, worker-protocol and remote-write gates. A notification, unblock or archive '
        'is not execution consent.'
    )
