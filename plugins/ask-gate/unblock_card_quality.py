"""Validate blocked-card briefs without changing machine-contract scope grammar.

Parsing is shared with session-panel/brief.py. This gate retains its independent
acceptance rules: Tried, minimum raw section lengths, decision option counts,
and exactly one recommended choice. Callers bound holds and fail open on error.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

MAX_HOLDS = 2
MAX_CREATE_HOLDS = 4
MIN_PART_CHARS = 20
# Test isolation only, honoured only under pytest.
ENV_SWITCH = "ASK_GATE_UNBLOCK_BRIEF"


def _load_brief():
    # Plugins are loaded by file location, not as importable Python packages.
    # Resolve symlinks so separately linked deployed plugins find their sibling.
    path = Path(__file__).resolve().parents[1] / "session-panel" / "brief.py"
    key = "ask_gate_shared_session_panel_brief_" + str(path)
    mod = sys.modules.get(key)
    if mod is None:
        spec = importlib.util.spec_from_file_location(key, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load shared brief parser: {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod = sys.modules.setdefault(key, mod)
    return mod


_brief = _load_brief()
# Compatibility exports for existing gate callers and tests.
sections = _brief.sections
options = _brief.options
_clean = _brief._clean
_flat = _brief._flat
parse_brief = _brief.parse_brief

_PHYSICAL_CONTRACT = re.compile(r"^[ \t]*(?:DEVICE|HUMAN_STEP)[ \t]*:", re.M)
_PR_NUMBER = re.compile(r"#\s*\d+|/pull/\d+", re.I)
_TRIED = re.compile(r"\btried\s*:", re.I)
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
    """A physical contract or numbered PR approval target, never bare evidence."""
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
    """Names of missing parts; empty when the request carries the whole brief."""
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
    """None when complete, otherwise a bounded-hold payload for the caller."""
    if os.environ.get(ENV_SWITCH) == "0" and os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    gaps = missing(request)
    if not gaps:
        return None
    template = CONTRACT_MESSAGE if contract_scope(str(request.get("scope") or "")) else HOLD_MESSAGE
    return {"missing": gaps, "message": template.format(missing="; ".join(gaps))}
