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
Options: with two or more line-start markers only those count; a single-line part splits at sentence ends.

Scope that is already a machine contract keeps its grammar and is not asked for the brief parts: a PR approval
scope naming a PR number (pr_approval_request.SCOPE, e.g. ``owner/repo#12 head abc1234``) or a physical-ask scope
(``DEVICE:`` or ``HUMAN_STEP:`` lines). ``Tried:`` and the single ``(recommended)`` choice still apply there, and
the hold message (CONTRACT_MESSAGE) says to keep the scope.

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
# Blocked creates are keyed by title/idempotency key, which a rewrite changes: cap them per (session, tool) too.
MAX_CREATE_HOLDS = 4
MIN_PART_CHARS = 20
# Test isolation only, honoured only under pytest: older fixtures predate this rule (their conftest sets it).
ENV_SWITCH = "ASK_GATE_UNBLOCK_BRIEF"

_NAMES = {"situation": r"situation", "options": r"options?", "recommend": r"recommend(?:ation|ed)?"}
_ANY_NAME = "|".join(f"(?:{v})" for v in _NAMES.values())
# Label candidates: a markdown heading (colon optional), or the word + colon. A word + colon counts only at line
# start (after a bullet/quote) or after a sentence end (single-line scopes, any number of spaces); mid-sentence
# "the options: ..." is prose. The context filter is _at_boundary (JS: atBoundary), not a lookbehind.
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]*(?:\*\*|__)?(" + _ANY_NAME + r")(?:\*\*|__)?[ \t]*:?(?:\*\*|__)?[ \t]*$",
                      re.I | re.M)
_WORD_LABEL = re.compile(r"(?<![\w])(?:\*\*|__)?(" + _ANY_NAME + r")(?:\*\*|__)?[ \t]*:(?:\*\*|__)?", re.I)
# Physical-ask contract lines (physical_asks.py): any of them ends a brief part.
_CONTRACT_LINE = re.compile(r"^[ \t]*(?:DEVICE|HUMAN_STEP|OBSERVATION|EVIDENCE)[ \t]*:", re.M)
# A physical-ask contract scope names the device or the human step, not just an EVIDENCE: line.
_PHYSICAL_CONTRACT = re.compile(r"^[ \t]*(?:DEVICE|HUMAN_STEP)[ \t]*:", re.M)
_PR_NUMBER = re.compile(r"#\s*\d+|/pull/\d+", re.I)
_TRIED = re.compile(r"\btried\s*:", re.I)
# Option marker candidates: A) / (A) / A. / A: / A - / 1) / 1. / Option A: , optionally bulleted and bold.
_OPTION = re.compile(
    r"(?<!\S)(?:[-*][ \t]+)?(?:\*\*)?(?:[Oo]ption[ \t]+)?\(?"
    r"(?:[A-F](?:\)|\.(?=\s)|:|[ \t]+[-—–](?=\s))|[1-9](?:\)|\.(?=\s)))(?:\*\*)?")
_PICK = re.compile(r"\b(?:[A-F]\s*(?:,\s*[A-F]\s*)*\s+or\s+[A-F]|yes\s+or\s+no|choose|pick|which\s+option)\b", re.I)
_RECOMMENDED = re.compile(r"\(recommended\)", re.I)

HOLD_MESSAGE = (
    "ask-gate: unblock_card_hold. This blocked card would reach David without the brief he needs to answer it "
    "from the panel ({missing}). Load the try-to-unblock skill and check whether you can clear it yourself; if "
    "David is truly needed, load generate-unblock-card and rewrite the human_request: why_human starts "
    "'Tried: ...'; scope has 'Situation:' (2-3 plain sentences, no internal jargon), 'Options:' followed by one "
    "line per option starting 'A)', 'B)' (what happens, cost, reversible?) and 'Recommend: <option> - <reason>', "
    "each label at the start of its own line; choices mark exactly one label '(recommended)'. Then file it again."
)
CONTRACT_MESSAGE = (
    "ask-gate: unblock_card_hold. This blocked card's scope is a machine contract (a PR approval target or "
    "physical-ask DEVICE:/HUMAN_STEP: lines): keep scope exactly as it is. Missing: {missing}. Load "
    "try-to-unblock and check whether you can clear it yourself; if David is truly needed, fix only why_human "
    "(start it 'Tried: ...') and choices (exactly one label '(recommended)'), and put the situation and the "
    "options' costs briefly in action/why_human (generate-unblock-card, 'Scope that is already a contract'). "
    "Then file it again."
)


def _norm(name: str) -> str:
    n = name.lower()
    return "situation" if n.startswith("situation") else "options" if n.startswith("option") else "recommend"


def _at_boundary(text: str, pos: int) -> bool:
    """True when pos starts a line (after spaces, a bullet/quote) or follows a sentence end (. ! ? ;) and spaces."""
    i = pos - 1
    while i >= 0 and text[i] in " \t":
        i -= 1
    if i < 0 or text[i] == "\n":
        return True
    if text[i] in "-*>":
        j = i - 1
        while j >= 0 and text[j] in " \t":
            j -= 1
        if j < 0 or text[j] == "\n":
            return True
    return text[i] in ".!?;" and i < pos - 1


def _flat(text: str) -> str:
    return " ".join(text.replace("**", "").replace("__", "").split())


def _clean(scope: str) -> str:
    return scope.replace("\r\n", "\n").replace("\r", "\n")


def sections(scope: str) -> dict[str, str]:
    """First occurrence of each labelled part -> its raw text, up to the next label or contract line."""
    scope = _clean(scope)
    marks = [(m.start(), m.end(), _norm(m.group(1))) for m in _HEADING.finditer(scope)]
    taken = [(s, e) for s, e, _ in marks]
    words = [m for m in _WORD_LABEL.finditer(scope)
             if _at_boundary(scope, m.start()) and not any(s <= m.start() < e for s, e in taken)]
    # With two or more line-start labels (headings count) the scope is laid out line by line, and a sentence-end
    # "Recommended:" inside an option's text is prose; a single-line scope keeps its sentence-end labels.
    if len(marks) + sum(_line_start(scope, m.start(), bullets=True) for m in words) >= 2:
        words = [m for m in words if _line_start(scope, m.start(), bullets=True)]
    marks += [(m.start(), m.end(), _norm(m.group(1))) for m in words]
    marks.sort()
    stops = sorted([s for s, _, _ in marks] + [m.start() for m in _CONTRACT_LINE.finditer(scope)])
    out: dict[str, str] = {}
    for start, end, name in marks:
        if name in out:
            continue
        nxt = next((s for s in stops if s >= end), len(scope))
        out[name] = re.sub(r"[\s>*-]+$", "", scope[end:nxt]).strip()  # the next label's bullet/quote marker
    return out


def _line_start(text: str, pos: int, bullets: bool = False) -> bool:
    """pos starts a line (after spaces; with bullets=True also after a bullet/quote marker)."""
    i = pos - 1
    while i >= 0 and text[i] in " \t":
        i -= 1
    if bullets and i >= 0 and text[i] in "-*>":
        i -= 1
        while i >= 0 and text[i] in " \t":
            i -= 1
    return i < 0 or text[i] == "\n"


def options(text: str) -> list[str]:
    """Option items in an Options part: text from each marker to the next; text before the first is dropped.
    With two or more line-start markers only those count ("A) Use plan B: restart" stays one option); otherwise
    (a single-line part) a marker counts at a line start or after a sentence end."""
    text = _clean(text)
    cands = [m.start() for m in _OPTION.finditer(text)]
    line = [s for s in cands if _line_start(text, s)]
    lettered = [s for s in line if re.match(r"[-*\s]*(?:\*\*)?(?:[Oo]ption\s+)?\(?[A-F]", text[s:])]
    if len(lettered) >= 2:
        line = lettered  # numbered sub-steps under lettered options stay inside their option
    starts = line if len(line) >= 2 else [s for s in cands if _at_boundary(text, s)]
    items = []
    for i, s in enumerate(starts):
        item = re.sub(r"^[-*]\s*", "", _flat(text[s: starts[i + 1] if i + 1 < len(starts) else len(text)]))
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
    """True when scope is a machine contract: a physical ask (DEVICE:/HUMAN_STEP: lines) or a PR approval target
    (pr_approval_request.SCOPE naming a PR number). A bare owner/repo or an EVIDENCE: line is not one."""
    scope = _clean(scope)
    if _PHYSICAL_CONTRACT.search(scope):
        return True
    try:
        return bool(_PR_NUMBER.search(scope)) and bool(_pr_scope().fullmatch(scope))
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
        if len(_flat(parts.get("situation", ""))) < MIN_PART_CHARS:
            out.append("scope has no 'Situation:' (at least a sentence)")
        if len(_flat(parts.get("recommend", ""))) < MIN_PART_CHARS:
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
    template = CONTRACT_MESSAGE if contract_scope(str(request.get("scope") or "")) else HOLD_MESSAGE
    return {"missing": gaps, "message": template.format(missing="; ".join(gaps))}
