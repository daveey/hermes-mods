"""Draft alternative index descriptions for a skill, in several formats, without seeing its test asks.

Formats (keys of the returned dict):
  a2  short:   <= 60 chars, "Use when <trigger>. <behavior>." (fits today's core limit)
  b   pdf:     output, input, 3 real phrases, 1 "Not for" in one row (needs a wider index row)
  c   triggers: the a2 short description + hidden ``triggers: {asks: [3], not_for}`` frontmatter, rendered by the
               prompt-builder prototype only when HERMES_SKILL_INDEX_TRIGGERS is set

The drafter sees the SKILL.md, the index and which skills the baseline confused it with; it never sees the
trigger card, so the eval measures the description, not a copy of the test.
"""
from __future__ import annotations

import json
import re

SHORT_LIMIT = 60
PDF_LIMIT = 320

SYSTEM = "You write skill-index descriptions that make an AI agent load the right skill. Output ONLY JSON."

PROMPT = """Skill: {name}
Current index description: {desc}
SKILL.md opening:
---
{body}
---
In a routing test, the agent confused this skill with: {confused}
Full index (name: description):
{index}

Write JSON with these keys:
"short": one line <= 60 characters, "Use when <trigger>. <behavior>." Lead with the user's situation in plain words \
(what they'd say), not internal jargon. Must distinguish it from the confused skills.
"pdf": one line <= 300 characters: "<what it produces>. Input: <what it starts from>. Asks: \\"<p1>\\"; \\"<p2>\\"; \
\\"<p3>\\". Not for: <neighbouring task> (use <other-skill>)." The three phrases are short things a user would type.
"asks": the same three phrases as a list.
"not_for": the not-for clause alone, e.g. "CI runner disk (use ci-runner-disk-recovery)".
Never include personal names, emails, hostnames or account ids."""


def prompt(name: str, desc: str, body: str, confused: list[str], index_rows: dict[str, str]) -> str:
    conf = ", ".join(f"{c} ({index_rows.get(c, '')})" for c in confused) or "none recorded"
    idx = "\n".join(f"{n}: {d}" for n, d in sorted(index_rows.items()))
    return PROMPT.format(name=name, desc=desc, body=body[:3500], confused=conf, index=idx)


def parse(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", text.strip())
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0) if m else text)
    short, pdf = str(data["short"]).strip(), str(data["pdf"]).strip()
    asks = [str(a).strip() for a in data["asks"]][:3]
    not_for = str(data.get("not_for") or "").strip()
    if len(short) > SHORT_LIMIT:
        raise ValueError(f"short is {len(short)} chars")
    if len(pdf) > PDF_LIMIT:
        raise ValueError(f"pdf is {len(pdf)} chars")
    if len(asks) < 3:
        raise ValueError("need 3 asks")
    return {"short": short, "pdf": pdf, "asks": asks, "not_for": not_for}


def overrides(drafts: dict[str, dict]) -> dict[str, dict]:
    """{format: {skill: override}} for render_index.render()."""
    return {
        "a2": {s: {"description": d["short"]} for s, d in drafts.items()},
        "b": {s: {"description": d["pdf"]} for s, d in drafts.items()},
        "c": {s: {"description": d["short"], "triggers": {"asks": d["asks"], "not_for": d["not_for"]}}
              for s, d in drafts.items()},
    }


def confused_with(metrics: dict, skill: str) -> list[str]:
    out = []
    for key in list(metrics.get("collisions", {})) + list(metrics.get("steals", {})):
        parts = re.split(r"[|>]", key)
        if skill in parts:
            out += [p for p in parts if p != skill and p not in out]
    return out
