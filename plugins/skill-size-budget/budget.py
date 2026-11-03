"""Pure checks for skill-size-budget (stdlib only): SKILL.md body budget and the references/ one-hop rule.

The hook, the tests and the inventory script share this module.

Budget: an automatic (curator-actor) edit may not leave a SKILL.md BODY (text after the frontmatter) over
``max_lines`` lines or ``max_chars`` characters while growing it. Growth that stays allowed over budget:

* a one-line pointer to a ``references/`` file (the redirect this plugin asks for),
* one ``- [ ]`` checklist line inside the skill's ``## Done means`` section (the preferred home for a correction),
* a reword that adds no line and at most ``REWORD_SLACK`` chars while the body stays under ``max_chars``.

One hop: a ``references/*.md`` file may not link to another reference file. A reader follows SKILL.md -> reference
and stops; a rule that sits two hops deep is never read.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

MAX_LINES = 300
MAX_CHARS = 30_000
REWORD_SLACK = 120
POINTER_MAX_CHARS = 300

_FRONTMATTER = re.compile(r"\A---\s*\n.*?\n---[ \t]*(?:\n|\Z)", re.S)
_DONE_HEADING = re.compile(r"^#{2,3}\s+done means\b", re.I)
_HEADING = re.compile(r"^#{1,3}\s+\S")
_CHECKBOX = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s+\S")
_REF_PATH = re.compile(r"references/[\w./-]+\.md")
# A markdown link to a sibling .md ("](other.md)", "](./other.md)", "](../references/x.md)").
_MD_LINK = re.compile(r"\]\(\s*(?:\./|\.\./references/)?([\w.-]+\.md)(?:#[^)]*)?\s*\)")


def body(text: str) -> str:
    """SKILL.md text after the YAML frontmatter (the whole text when there is none)."""
    m = _FRONTMATTER.match(text or "")
    return (text or "")[m.end():] if m else (text or "")


def measure(text: str) -> tuple[int, int]:
    b = body(text)
    return len(b.splitlines()), len(b)


def over_budget(text: str, max_lines: int = MAX_LINES, max_chars: int = MAX_CHARS) -> bool:
    lines, chars = measure(text)
    return lines > max_lines or chars > max_chars


def has_done_means(text: str) -> bool:
    return any(_DONE_HEADING.match(line) for line in body(text).splitlines())


def _section_of(lines: list[str]) -> list[str]:
    """Heading line (lowercased) governing each line index ('' before the first heading)."""
    cur, out, fence = "", [], False
    for line in lines:
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        if not fence and _HEADING.match(line):
            cur = line.strip().lower()
        out.append(cur)
    return out


def added_lines(before: str, after: str) -> list[tuple[int, str]]:
    """(index in after-body, line) for every line the edit inserted or rewrote."""
    a, b = body(before).splitlines(), body(after).splitlines()
    out = []
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("insert", "replace"):
            out.extend((j, b[j]) for j in range(j1, j2))
    return out


def _allowed_line(line: str, section: str, skill_name: str = "") -> str:
    """'pointer' | 'done-means' | '' for one added line of an over-budget edit."""
    s = line.strip()
    if not s:
        return "blank"
    if len(s) > POINTER_MAX_CHARS:
        return ""
    if _REF_PATH.search(s):
        return "pointer"
    if _DONE_HEADING.match(section) and _CHECKBOX.match(line):
        return "done-means"
    return ""


@dataclass
class Verdict:
    over: bool = False  # the edit leaves the body over budget AND grows it in a way not allowed over budget
    before: tuple[int, int] = (0, 0)
    after: tuple[int, int] = (0, 0)
    reason: str = ""
    added: list[str] = field(default_factory=list)


def check_skill_md(before: str, after: str, max_lines: int = MAX_LINES, max_chars: int = MAX_CHARS) -> Verdict:
    """Verdict for one SKILL.md write. ``before`` is '' for a create."""
    bl, bc = measure(before)
    al, ac = measure(after)
    v = Verdict(before=(bl, bc), after=(al, ac))
    if al <= max_lines and ac <= max_chars:
        return v
    if al <= bl and ac <= bc:
        return v  # shrinking or equal: always fine (a split in progress)
    ins = added_lines(before, after)
    sections = _section_of(body(after).splitlines())
    kinds = [_allowed_line(line, sections[j]) for j, line in ins]
    nonblank = [k for k in kinds if k != "blank"]
    # A pointer or a Done-means line: at most one new non-blank line, net growth at most one line (+ a blank).
    if nonblank and all(nonblank) and len(nonblank) <= 1 and al - bl <= 2:
        return v
    # A reword: no new line, a few chars, and the body stays under the char budget.
    if al <= bl and ac - bc <= REWORD_SLACK and ac <= max_chars:
        return v
    v.over = True
    v.added = [line for (_j, line), k in zip(ins, kinds) if k != "blank"][:5]
    parts = []
    if al > max_lines:
        parts.append(f"{al} lines (budget {max_lines})")
    if ac > max_chars:
        parts.append(f"{ac:,} chars (budget {max_chars:,})")
    v.reason = ", ".join(parts)
    return v


def cross_reference_links(before: str, after: str, own_path: str = "") -> list[str]:
    """Links to OTHER reference files that this write to a references/*.md file adds (one-hop rule)."""
    own = own_path.rsplit("/", 1)[-1]
    out = []
    for _j, line in added_lines(before, after) if before else [(i, l) for i, l in enumerate(after.splitlines())]:
        for m in _REF_PATH.finditer(line):
            if m.group(0).rsplit("/", 1)[-1] != own:
                out.append(m.group(0))
        for m in _MD_LINK.finditer(line):
            if m.group(1) != own and m.group(1).lower() != "skill.md":
                out.append(m.group(1))
    return sorted(set(out))


def is_reference_path(file_path: str) -> bool:
    return bool(file_path) and file_path.replace("\\", "/").lstrip("./").startswith("references/") \
        and file_path.endswith(".md")
