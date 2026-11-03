"""Trigger cards: per-skill YAML with natural asks that should load the skill and near-misses that should not.

Card shape (``references/trigger-cards.yaml`` next to an owned skill, else ``cards/<name>.yaml`` here):

    skill: five-whys
    should_load:            # >= 3 natural user asks
      - ask: "why did the deploy fail again? dig into the root cause"
        source: session     # session (mined, paraphrased) | generated | hand
    near_miss:              # >= 1 ask that shares words/topic but should NOT load this skill
      - ask: "fix the typo in the deploy script"
        instead: null       # the skill that should win instead, or null for none
    reviewed: false         # true once a human/agent hand-checked the card
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
HARNESS_CARDS = HERE / "cards"
CARD_FILE = "references/trigger-cards.yaml"


class CardError(ValueError):
    pass


@dataclass
class Card:
    skill: str
    should_load: list[dict]
    near_miss: list[dict]
    reviewed: bool = False
    path: Path | None = None
    extra: dict = field(default_factory=dict)

    def asks(self):
        """(ask_id, kind, ask, instead) for every ask; ids are stable across runs."""
        for i, a in enumerate(self.should_load):
            yield f"{self.skill}#s{i}", "should", a["ask"], None
        for i, a in enumerate(self.near_miss):
            yield f"{self.skill}#n{i}", "near_miss", a["ask"], a.get("instead")


def validate(data: dict, known: set[str] | None = None, where: str = "") -> Card:
    if not isinstance(data, dict):
        raise CardError(f"{where}: card is not a mapping")
    skill = data.get("skill")
    if not isinstance(skill, str) or not skill:
        raise CardError(f"{where}: missing skill")
    should, near = data.get("should_load") or [], data.get("near_miss") or []
    if len(should) < 3:
        raise CardError(f"{where}: {skill} needs >= 3 should_load asks, has {len(should)}")
    if len(near) < 1:
        raise CardError(f"{where}: {skill} needs >= 1 near_miss")
    for a in [*should, *near]:
        if not isinstance(a, dict) or not isinstance(a.get("ask"), str) or not a["ask"].strip():
            raise CardError(f"{where}: {skill} has an ask without text: {a!r}")
    for a in near:
        inst = a.get("instead")
        if inst is not None and not isinstance(inst, str):
            raise CardError(f"{where}: {skill} near_miss instead must be a skill name or null")
        if inst == skill:
            raise CardError(f"{where}: {skill} near_miss names itself as the winner")
        if known is not None and inst is not None and inst not in known:
            raise CardError(f"{where}: {skill} near_miss instead={inst!r} is not a skill in the index")
    extra = {k: v for k, v in data.items() if k not in ("skill", "should_load", "near_miss", "reviewed")}
    return Card(skill, list(should), list(near), bool(data.get("reviewed")), None, extra)


def load_card(path: Path, known: set[str] | None = None) -> Card:
    card = validate(yaml.safe_load(path.read_text(encoding="utf-8")), known, str(path))
    card.path = path
    return card


def card_path_for(name: str, skill_md: Path | None, owned: bool) -> Path:
    if owned and skill_md is not None:
        return skill_md.parent / CARD_FILE
    return HARNESS_CARDS / f"{name}.yaml"


def load_cards(skill_paths: dict[str, Path], owned: set[str], names: list[str] | None = None,
               known: set[str] | None = None) -> tuple[dict[str, Card], list[str]]:
    """Cards for *names* (default: all of skill_paths). Returns (cards, missing names)."""
    cards, missing = {}, []
    for name in names or sorted(skill_paths):
        p = card_path_for(name, skill_paths.get(name), name in owned)
        alt = HARNESS_CARDS / f"{name}.yaml"
        if p.is_file():
            cards[name] = load_card(p, known)
        elif alt.is_file():  # an owned skill whose card has not moved next to it yet
            cards[name] = load_card(alt, known)
        else:
            missing.append(name)
    return cards, missing


def dump_card(card: dict) -> str:
    return yaml.safe_dump(card, sort_keys=False, allow_unicode=True, width=120)


# ---------------------------------------------------------------- ownership

def bundled_names(home: Path, core: Path) -> set[str]:
    """Skills upstream owns: the bundled manifest plus core's skills/ and optional-skills/ trees."""
    out: set[str] = set()
    manifest = home / "skills" / ".bundled_manifest"
    if manifest.is_file():
        out |= {ln.split(":", 1)[0].strip() for ln in manifest.read_text().splitlines() if ln.strip()}
    for sub in ("skills", "optional-skills"):
        root = core / sub
        if root.is_dir():
            out |= {p.parent.name for p in root.rglob("SKILL.md")}
    return out


from mine import mine_phrasings  # noqa: E402,F401  (re-exported)


# ---------------------------------------------------------------- drafting with the pool LLM

GEN_SYSTEM = """You write routing test cards for an AI agent's skill library. The agent sees a skills index \
(name + short description per skill) and must load the right skill for each user message.
Output ONLY a YAML document, no prose, no code fence."""

GEN_PROMPT = """Skill under test: {name}
Description: {description}
Opening of its SKILL.md:
---
{body}
---
{mined}
All skills in the index (name: description):
{index}

Write a card:
skill: {name}
should_load:   # exactly 3 asks a real user would type when this skill is the one to load
  - ask: "..."
    source: generated   # or session when adapted from a real message above
near_miss:     # exactly 1 ask that shares words or topic with this skill but where it should NOT load
  - ask: "..."
    instead: <name of the skill from the list that should load instead, or null if none should>
reviewed: false

Rules: asks are short (5-30 words), casual, first person, the way someone types to an assistant as the FIRST \
message of a new chat (no "that", "it", "both" pointing at earlier context); vary wording, don't copy the \
description's words. Adapt a real message only if it still makes sense on its own; otherwise ignore it. \
{names_rule} The near-miss must be genuinely tempting but clearly another task. \
`instead` must be an exact name from the list or null."""

NAMES_PUBLIC = ("Never include personal names, emails, hostnames, IPs, account ids or company names; replace them with "
                "generic ones (this card is published).")
NAMES_PRIVATE = ("Use the product, repo, project and company names the skill itself uses, the way its user would "
                 "(e.g. its app or repo name); never people's names, emails, IPs or account ids.")


def gen_prompt(name: str, description: str, body: str, index_rows: dict[str, str], mined: list[str],
               public: bool = True) -> str:
    mined_txt = ""
    if mined:
        mined_txt = ("Real messages that loaded this skill (paraphrase; strip personal details):\n"
                     + "\n".join(f"- {m}" for m in mined[:6]) + "\n")
    idx = "\n".join(f"{n}: {d}" for n, d in sorted(index_rows.items()))
    return GEN_PROMPT.format(name=name, description=description, body=body[:3500], mined=mined_txt, index=idx,
                             names_rule=NAMES_PUBLIC if public else NAMES_PRIVATE)


def parse_generated(text: str, name: str, known: set[str]) -> dict:
    text = re.sub(r"^```(?:yaml)?\s*|\s*```\s*$", "", text.strip())
    data = yaml.safe_load(text)
    if isinstance(data, dict):
        data["skill"] = name
        data["reviewed"] = False
        for a in data.get("near_miss") or []:
            if isinstance(a, dict) and a.get("instead") in ("null", "None", "", "none"):
                a["instead"] = None
    validate(data, known, f"generated:{name}")
    return data
