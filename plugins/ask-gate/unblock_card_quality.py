"""unblock_card_quality: a blocked card's human_request must carry the decision brief David needs.

Why: session summaries kept showing blocked cards David could not act on (2026-11-20, card t_01a1f8b56d1a69fc:
"Reply A or B in this chat. A = authorize one bounded repair run against the current Mac Desktop (PID 5198 ...)":
jargon, no situation, no consequences per option, no recommendation). The ask-gate judge checks whether an ask
should exist, not whether it is answerable. The skills try-to-unblock and generate-unblock-card hold the reasoning;
this rule only checks that their output is present, so the agent cannot skip them.

Deterministic, no model call. Checked, on kanban_block / blocked kanban_create with a human_request:
- why_human names what was tried (``Tried:``): try-to-unblock ran.
- scope has ``Situation:`` and ``Recommend:`` (or ``Recommendation:``) parts with real text (>= MIN_PART_CHARS).
- a decision also has ``Options:`` with two or more options. A decision is interaction ``approval``, any request
  with ``choices``, or a ``reply`` whose action asks for a pick ("A or B", "yes or no", "choose"). Options count as
  lettered/numbered items (``A)``, ``- B:``, ``1.``, ``Option C:``) under the label, on their own lines or inline,
  or as ``choices`` when given.
- when ``choices`` are given, exactly one label carries ``(recommended)``.
Labels may be bold, bulleted, a markdown heading (``## Situation``) or follow a sentence end on the same line.

Scope that is already a machine contract keeps its grammar and is not asked for the brief parts: a PR approval
scope (pr_approval_request.SCOPE, e.g. ``owner/repo#12 head abc1234``) or a physical-ask scope (``DEVICE:``,
``HUMAN_STEP:``, ``OBSERVATION:``, ``EVIDENCE:`` lines). ``Tried:`` and the single ``(recommended)`` choice still
apply there.

The parse mirrors desktop-plugins/session-tasks ``briefOf`` (what the panel renders); change both together.
Callers bound holds (MAX_HOLDS per session, tool and card) and fail open on error.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

MAX_HOLDS = 2
MIN_PART_CHARS = 20
# Test isolation only, honoured only under pytest: older fixtures predate this rule (their conftest sets it).
ENV_SWITCH = "ASK_GATE_UNBLOCK_BRIEF"

_NAMES = {"situation": r"situation", "options": r"options?", "recommend": r"recommend(?:ation|ed)?"}
_ANY_NAME = "|".join(f"(?:{v})" for v in _NAMES.values())
# A label: a markdown heading (colon optional), or the word + colon at line start (after a bullet) or after a
# sentence end (single-line scopes). Mid-sentence "the options: ..." is prose, not a label.
_LABEL = re.compile(
    r"(?:^[ \t]*#{1,6}[ \t]*(?:\*\*|__)?(" + _ANY_NAME + r")(?:\*\*|__)?[ \t]*:?(?:\*\*|__)?[ \t]*$"
    r"|(?:^[ \t]*(?:[-*>][ \t]*)?|(?<=[.!?;][ \t]))(?:\*\*|__)?(" + _ANY_NAME + r")(?:\*\*|__)?[ \t]*:(?:\*\*|__)?)",
    re.I | re.M)
# A line in a machine-contract label block (physical asks); it ends a brief part.
_CONTRACT_LINE = re.compile(r"^[ \t]*(?:DEVICE|HUMAN_STEP|OBSERVATION|EVIDENCE)[ \t]*:", re.M)
_TRIED = re.compile(r"\btried\s*:", re.I)
# One option marker: A) / (A) / A. / A: / A - / 1) / 1. / Option A: , at line start (after a bullet) or after space.
_OPTION = re.compile(
    r"(?:^|(?<=\s))(?:[-*][ \t]+)?(?:\*\*)?(?:[Oo]ption[ \t]+)?\(?"
    r"(?:[A-F](?:\)|\.(?=\s)|:|[ \t]+[-\u2014\u2013](?=\s))|[1-9](?:\)|\.(?=\s)))(?:\*\*)?",
    re.M)
_PICK = re.compile(r"\b(?:[A-F]\s*(?:,\s*[A-F]\s*)*(?:or|/)\s*[A-F]|yes\s+or\s+no|choose|pick|which\s+option)\b", re.I)
_RECOMMENDED = re.compile(r"\(recommended\)", re.I)

HOLD_MESSAGE = (
    "ask-gate: unblock_card_hold. This blocked card would reach David without the brief he needs to answer it "
    "from the panel ({missing}). Load the try-to-unblock skill and check whether you can clear it yourself; if "
    "David is truly needed, load generate-unblock-card and rewrite the human_request: why_human starts "
    "'Tried: ...'; scope has 'Situation:' (2-3 plain sentences, no internal jargon), 'Options:' followed by one "
    "line per option starting 'A)', 'B)' (what happens, cost, reversible?) and 'Recommend: <option> - <reason>', "
    "each label at the start of its own line; choices mark exactly one label '(recommended)'. Then file it again."
)


def _norm(name: str) -> str:
    n = name.lower()
    return "situation" if n.startswith("situation") else "options" if n.startswith("option") else "recommend"


def sections(scope: str) -> dict[str, str]:
    """First occurrence of each labelled part -> its text, up to the next label or contract line."""
    marks = [(m.start(), m.end(), _norm(m.group(1) or m.group(2))) for m in _LABEL.finditer(scope)]
    stops = sorted([s for s, _, _ in marks] + [m.start() for m in _CONTRACT_LINE.finditer(scope)])
    out: dict[str, str] = {}
    for start, end, name in marks:
        if name in out:
            continue
        nxt = next((s for s in stops if s >= end), len(scope))
        out[name] = scope[end:nxt].strip()
    return out


def options(text: str) -> list[str]:
    """Option items in an Options part: text from each marker to the next; text before the first is dropped."""
    starts = [m.start() for m in _OPTION.finditer(text)]
    items = []
    for i, s in enumerate(starts):
        item = text[s: starts[i + 1] if i + 1 < len(starts) else len(text)]
        item = " ".join(item.replace("**", "").replace("__", "").split()).lstrip("-* ").strip()
        if item:
            items.append(item)
    return items


_PR_SCOPE = None


def _pr_scope():
    global _PR_SCOPE
    if _PR_SCOPE is None:
        key = "unblock_card_quality_pr_approval_request"
        mod = sys.modules.get(key)
        if mod is None:
            spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("pr_approval_request.py"))
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            mod = sys.modules.setdefault(key, mod)
        _PR_SCOPE = mod.SCOPE
    return _PR_SCOPE


def contract_scope(scope: str) -> bool:
    """True when scope is a machine contract (PR approval target, physical-ask labels), not a brief."""
    if _CONTRACT_LINE.search(scope):
        return True
    try:
        return bool(scope.strip()) and bool(_pr_scope().fullmatch(scope))
    except Exception:
        return False


def is_decision(request: dict) -> bool:
    interaction = str(request.get("interaction") or "").strip()
    if interaction == "approval" or request.get("choices"):
        return True
    return interaction == "reply" and bool(_PICK.search(str(request.get("action") or "")))


def missing(request: dict) -> list[str]:
    """Names of the missing parts; empty when the request carries the whole brief."""
    if not isinstance(request, dict):
        return []
    scope = str(request.get("scope") or "")
    choices = request.get("choices") if isinstance(request.get("choices"), list) else None
    out: list[str] = []
    if not _TRIED.search(str(request.get("why_human") or "")):
        out.append("why_human has no 'Tried:'")
    if not contract_scope(scope):
        parts = sections(scope)
        if len(parts.get("situation", "")) < MIN_PART_CHARS:
            out.append("scope has no 'Situation:' (at least a sentence)")
        if len(parts.get("recommend", "")) < MIN_PART_CHARS:
            out.append("scope has no 'Recommend:' with a reason")
        if is_decision(request):
            n = len(options(parts["options"])) if "options" in parts else 0
            if n < 2 and not (choices and len(choices) >= 2):
                out.append("scope has no 'Options:' with two or more lettered options (A), B), ...)")
    if choices:
        marked = [c for c in choices if isinstance(c, dict) and _RECOMMENDED.search(str(c.get("label") or ""))]
        if len(marked) != 1:
            out.append(f"{len(marked)} choices are labelled '(recommended)', expected exactly one")
    return out


def hit(request: dict) -> dict | None:
    """None when the brief is complete, else {'missing': [...], 'message': str}."""
    if os.environ.get(ENV_SWITCH) == "0" and os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    gaps = missing(request)
    if not gaps:
        return None
    return {"missing": gaps, "message": HOLD_MESSAGE.format(missing="; ".join(gaps))}
