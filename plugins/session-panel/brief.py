"""Shared deterministic brief parser, with no runtime or plugin-manager dependencies.

The raw sections/options helpers retain ask-gate's acceptance grammar; display
fields do not determine whether an ask passes that gate. No text is executed.
"""
from __future__ import annotations

import re

_NAMES = {"situation": r"situation", "options": r"options?", "recommend": r"recommend(?:ation|ed)?"}
_ANY_NAME = "|".join(f"(?:{v})" for v in _NAMES.values())
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]*(?:\*\*|__)?(" + _ANY_NAME + r")(?:\*\*|__)?[ \t]*:?(?:\*\*|__)?[ \t]*$", re.I | re.M)
_WORD_LABEL = re.compile(r"(?<![\w])(?:\*\*|__)?(" + _ANY_NAME + r")(?:\*\*|__)?[ \t]*:(?:\*\*|__)?", re.I)
_CONTRACT_LINE = re.compile(r"^[ \t]*(?:DEVICE|HUMAN_STEP|OBSERVATION|EVIDENCE)[ \t]*:", re.M)
_OPTION = re.compile(
    r"(?<!\S)(?:[-*][ \t]+)?(?:\*\*)?(?:[Oo]ption[ \t]+)?\(?"
    r"(?:[A-F](?:\)|\.(?=\s)|:|[ \t]+[-—–](?=\s))|[1-9](?:\)|\.(?=\s)))(?:\*\*)?")
_RECOMMENDED = re.compile(r"\(recommended\)", re.I)
_OWN_LETTER = re.compile(r"^\s*(?:option\s+)?\(?([A-Za-z])(?:\)|:|\.(?=\s)|\s+[-–—](?=\s))\s*", re.I)


def panel_letters(rows: list[str], extra: int) -> list[str]:
    """Choice-owned badges, then supplemental Skip/Other badges; otherwise positional."""
    choices = rows[:len(rows) - extra]
    own = [(m.group(1).upper() if (m := _OWN_LETTER.match(label)) else '') for label in choices]
    top = max((ord(c) for c in own if c), default=0)
    if not own or not all(own) or len(set(own)) != len(own) or top + extra > 90:
        return [chr(65 + i) for i in range(len(rows))]
    return own + [chr(top + 1 + i) for i in range(extra)]


def _norm(name: str) -> str:
    n = name.lower()
    return "situation" if n.startswith("situation") else "options" if n.startswith("option") else "recommend"


def _at_boundary(text: str, pos: int) -> bool:
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


def _line_start(text: str, pos: int, bullets: bool = False) -> bool:
    i = pos - 1
    while i >= 0 and text[i] in " \t":
        i -= 1
    if bullets and i >= 0 and text[i] in "-*>":
        i -= 1
        while i >= 0 and text[i] in " \t":
            i -= 1
    return i < 0 or text[i] == "\n"


def sections(scope: str) -> dict[str, str]:
    """First labelled part, ending at the next label or machine-contract line.

    Prefer real line-start labels to sentence-end mentions of that same label;
    retain sentence labels for inline and mixed layouts.
    """
    scope = _clean(scope)
    marks = [(m.start(), m.end(), _norm(m.group(1))) for m in _HEADING.finditer(scope)]
    taken = [(s, e) for s, e, _ in marks]
    words = [m for m in _WORD_LABEL.finditer(scope)
             if _at_boundary(scope, m.start()) and not any(s <= m.start() < e for s, e in taken)]
    at_line = {name for _, _, name in marks} | {_norm(m.group(1)) for m in words
                                                 if _line_start(scope, m.start(), bullets=True)}
    words = [m for m in words if _line_start(scope, m.start(), bullets=True) or _norm(m.group(1)) not in at_line]
    marks += [(m.start(), m.end(), _norm(m.group(1))) for m in words]
    marks.sort()
    stops = sorted([s for s, _, _ in marks] + [m.start() for m in _CONTRACT_LINE.finditer(scope)])
    out: dict[str, str] = {}
    for start, end, name in marks:
        if name in out:
            continue
        nxt = next((s for s in stops if s >= end), len(scope))
        out[name] = re.sub(r"[\s>*-]+$", "", scope[end:nxt]).strip()
    return out


def options(text: str) -> list[str]:
    """Raw option items, preserving numbered substeps inside lettered options."""
    text = _clean(text)
    cands = [m.start() for m in _OPTION.finditer(text)]
    line = [s for s in cands if _line_start(text, s)]
    lettered = [s for s in line if re.match(r"[-*\s]*(?:\*\*)?(?:[Oo]ption\s+)?\(?[A-F]", text[s:])]
    if len(lettered) >= 2:
        line = lettered
    starts = line if len(line) >= 2 else [s for s in cands if _at_boundary(text, s)]
    items = []
    for i, s in enumerate(starts):
        item = re.sub(r"^[-*]\s*", "", _flat(text[s: starts[i + 1] if i + 1 < len(starts) else len(text)]))
        if item:
            items.append(item)
    return items


def _option_parts(text: str) -> dict[str, str]:
    full = _flat(_RECOMMENDED.sub("", text))
    match = re.match(r"\s*(?:[Oo]ption\s+)?\(?([A-F1-9])(?:\)|\.|:|\s+[-—–])\s*", full)
    letter = match.group(1) if match else ""
    rest = full[match.end():] if match else full
    sep = re.search(r"\s*(?::\s|\s[-—–]\s)", rest)
    if sep and 0 < sep.start() <= 80:
        name, detail = rest[:sep.start()], rest[sep.end():]
    else:
        name = re.split(r"[.;,]\s", rest, maxsplit=1)[0][:80]
        detail = re.sub(r"^[.;,]\s*", "", rest[len(name):])
    def cap(value: str) -> str:
        value = value.strip()
        return value[:1].upper() + value[1:]
    return {"letter": letter, "name": cap(name), "detail": cap(detail)}


def _recommend_parts(text: str) -> tuple[str, str]:
    text = re.sub(r"^\s*recommend(?:ation|ed)?\s*:\s*", "", text, flags=re.I).strip()
    match = re.match(r"(?:[Oo]ption\s+)?\(?([A-F1-9])\)?(?=[\s:.—–-]|$)", text)
    if not match or re.match(r"\s*(?:,|and\b|or\b|/)\s*", text[match.end():], re.I):
        return "", text
    reason = re.sub(r"^[\s.:,—–-]+", "", text[match.end():])
    return match.group(1), re.sub(r"^because\s+", "", reason, flags=re.I).strip()


def _why_parts(text: str) -> tuple[str, str]:
    text = " ".join(text.split())
    tried = bool(re.match(r"tried\s*:", text, re.I))
    only = re.search(r"\bonly you can\b", text, re.I) if tried else re.match(r"only you can\b", text, re.I)
    if only:
        part = re.sub(r"^only you can\s*:?\s*", "", text[only.start():], flags=re.I).strip()
        trail = re.sub(r"^tried\s*:\s*", "", text[:only.start()], flags=re.I).strip()
        return "Only you can: " + part, trail
    if tried:
        return "Agent tried: " + re.sub(r"^tried\s*:\s*", "", text, flags=re.I), ""
    return ("Why you: " + text if text else ""), ""


def parse_brief(request: dict) -> dict:
    """Parse a human_request without inventing letters or changing its scope.

    Choice labels control display order; scope detail is joined by own letter.
    Explicit scope recommendations win over a unique recommended choice. Choice
    labels own their letters (never their list positions or opaque values).
    ``only_you`` is the display-ready reason line; ``tried`` is its details trail.
    Raw parts remain available in ``details`` for validation and diagnostics.
    """
    request = request if isinstance(request, dict) else {}
    parts = sections(str(request.get("scope") or ""))
    raw_options = options(parts.get("options", ""))
    choices = request.get("choices")
    choices = [c for c in choices if isinstance(c, dict)] if isinstance(choices, list) else []
    parsed_options = [_option_parts(item) for item in raw_options]
    if choices:
        # user-steps b262ccfb: badges and answer IDs follow choice order, not
        # scope order. Own letters are used only when ALL are present/unique.
        labels = [str(c.get('label') or '') for c in choices]
        own = [_OWN_LETTER.match(label) for label in labels]
        letters = panel_letters(labels + ['Skip', 'Other'], 2)[:len(choices)]
        use_own = all(own) and letters == [m[1].upper() for m in own if m]
        by_letter = {o['letter']: o for o in parsed_options}
        projected = []
        for i, c in enumerate(choices):
            label = re.sub(r'\s*\(recommended\)\s*$', '', str(c.get('label') or ''), flags=re.I)
            if use_own:
                label = label[own[i].end():]
            letter = letters[i] if use_own else chr(65 + i)
            projected.append({'letter': letter, 'name': label[:1].upper() + label[1:],
                              'detail': by_letter.get(letter, {}).get('detail', '') or str(c.get('explain') or '')})
        parsed_options = projected
    letter, reason = _recommend_parts(_flat(re.split(r'\n[ \t]*\n', parts.get("recommend", ""), maxsplit=1)[0]))
    if not parts.get("recommend"):
        marked = [i for i, c in enumerate(choices) if c.get('recommended') is True or _RECOMMENDED.search(str(c.get("label") or ""))]
        if len(marked) == 1 and marked[0] < len(parsed_options):
            letter = parsed_options[marked[0]]["letter"]
    only_you, tried = _why_parts(str(request.get("why_human") or ""))
    return {"situation": _flat(parts.get("situation", "")),
            "options": [item for item in parsed_options if item["name"]],
            "recommend_letter": letter, "reason": reason,
            "only_you": only_you, "tried": tried, "details": parts}
