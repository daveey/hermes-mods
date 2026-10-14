"""lessons: turn the user's corrections into candidate durable rules (memory / skill / AGENTS.md lines).

Design follows Cursor Bugbot's learned rules and Devin's knowledge suggestions: a correction never becomes a rule
directly. It becomes a CANDIDATE keyed by a short slug; a candidate is PROMOTED when it recurs (>= 2 sessions) or
the user accepts it, and dropped when the user rejects it.

- judge(prev_reply, user_text) -> {p_correction, durable, rule, slug, scope}
- LessonStore (<HERMES_HOME>/coach/lessons.json): add/merge by slug, accept/reject, promoted list.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

PREFILTER_RE = re.compile(
    r"^\s*(no\b|nope|wait\b|actually\b|that'?s (?:not|wrong)|i said|i meant|don'?t\b|stop\b|why (?:did|are|would) you|"
    r"you (?:didn'?t|forgot|missed|should|shouldn'?t|need to)|instead|not what i|wrong|use \S+ not)"
    r"|\b(i (?:told|asked) you|you should (?:already )?know|don'?t ask me|stop asking|as i said|we already|i already|"
    r"not (?:that|this) one|you have access|it'?s (?:there|in)|from (?:the )?(?:token broker|secrets manager|memory))\b",
    re.I)

JUDGE_SYS = """You turn a power user's corrections of his AI agent into durable rules. You see the agent's previous message
and the user's reply.

- p_correction (0-1): the user is correcting a wrong assumption, action, plan, or question of the agent (not merely
  giving new requirements or answering a question the agent legitimately had to ask).
- durable (0-1): the correction implies a STANDING rule that would prevent the same mistake in future sessions
  (a preference, an environment fact, a workflow rule). One-off facts about this task score low.
- rule: if durable, ONE imperative sentence a future agent should follow, general enough to reuse, specific enough to
  act on (name the tool/path/host when the user did). Empty otherwise.
- slug: 2-5 lowercase words joined by '-', naming the rule's topic so that duplicates share a slug
  (e.g. "fetch-creds-before-asking", "proceed-on-reversible-work").
- scope: "global" or "project:<name>" (a repo/system the rule only applies to).

Return ONLY JSON: {"p_correction": p, "durable": p, "rule": "...", "slug": "...", "scope": "..."}"""


SKIP_RE = re.compile(
    r"^\s*(?:/\S+.*|go|yes|y|yep|yeah|ok(?:ay)?|k|do it|done|thanks?|thank you|ty|sure|proceed|continue|resume|"
    r"lgtm|ship it|sounds good|perfect|great|nice|cool|👍|\[image\]|\[screenshot\])\W*$", re.I)


def prefilter(user_text: str) -> bool:
    """Which user messages get judged. The keyword regex alone caught only 51% of corrections in the mined history
    (they're phrased every which way: "do this yourself", "asana pat is in the secrets manager", "left frame didnt
    change"), so every real message is judged except bare approvals/acks and slash commands. The judge runs off the
    critical path (a background thread in __init__), so this costs tokens, not latency."""
    t = (user_text or "").strip()
    return bool(t) and not SKIP_RE.match(t[:200])


def keyword_hit(user_text: str) -> bool:
    return bool(PREFILTER_RE.search((user_text or "")[:800]))


def judge_prompt(prev_reply: str, user_text: str, known: Optional[list] = None) -> str:
    k = ""
    if known:
        k = ("\n\nKNOWN RULES (if the correction restates one, REUSE its slug exactly):\n"
             + "\n".join(f"- {c['slug']}: {c['rule'][:160]}" for c in known[:40]))
    return f"AGENT'S PREVIOUS MESSAGE:\n{(prev_reply or '')[-2500:]}\n\nUSER'S REPLY:\n{(user_text or '')[:1500]}{k}"


def parse(raw: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    slug = re.sub(r"[^a-z0-9-]+", "-", str(d.get("slug") or "").lower()).strip("-")[:60]
    return {"p_correction": float(d.get("p_correction") or 0), "durable": float(d.get("durable") or 0),
            "rule": str(d.get("rule") or "").strip()[:300], "slug": slug, "scope": str(d.get("scope") or "global")[:60]}


def aux_judge(system: str, prompt: str) -> str:
    from agent.auxiliary_client import call_llm  # type: ignore
    resp = call_llm(task="coach_lessons", messages=[{"role": "system", "content": system},
                                                   {"role": "user", "content": prompt}],
                    max_tokens=200, temperature=0, timeout=20)
    return (getattr(resp.choices[0].message, "content", None) or "").strip()


PROMOTE_SESSIONS = 2


class LessonStore:
    def __init__(self, root: Path, clock: Callable[[], float] = time.time):
        self.path = Path(root) / "lessons.json"
        self.clock = clock

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except Exception:
            return {}

    def _write(self, d: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1))
        os.replace(tmp, self.path)

    def add(self, v: dict, session_id: str, example: str = "") -> dict:
        d = self._read()
        c = d.get(v["slug"]) or {"slug": v["slug"], "rule": v["rule"], "scope": v["scope"], "sessions": [],
                                 "examples": [], "status": "candidate", "first": self.clock()}
        if session_id and session_id not in c["sessions"]:
            c["sessions"].append(session_id)
        if example and len(c["examples"]) < 5:
            c["examples"].append(example[:200])
        c["last"] = self.clock()
        if c["status"] == "candidate" and len(c["sessions"]) >= PROMOTE_SESSIONS:
            c["status"] = "promoted"
        d[v["slug"]] = c
        self._write(d)
        return c

    def set_status(self, slug: str, status: str) -> Optional[dict]:
        d = self._read()
        if slug not in d:
            return None
        d[slug]["status"] = status
        d[slug]["decided"] = self.clock()
        self._write(d)
        return d[slug]

    def known(self) -> list[dict]:
        """Saved/promoted rules first, then the most-seen candidates (rejected excluded)."""
        order = {"saved": 0, "promoted": 1, "candidate": 2}
        return sorted((c for c in self._read().values() if c.get("status") in order),
                      key=lambda c: (order[c["status"]], -len(c.get("sessions", []))))

    def by_status(self, status: str) -> list[dict]:
        return sorted((c for c in self._read().values() if c.get("status") == status),
                      key=lambda c: (-len(c.get("sessions", [])), -c.get("last", 0)))


def assess(prev_reply: str, user_text: str, llm: Callable[[str, str], str], th_corr: float = 0.7,
           th_durable: float = 0.7, wide: bool = False, known: Optional[list] = None) -> Optional[dict]:
    """wide=True (the background caller) judges every non-ack message; wide=False keeps the keyword gate, so a
    backend still running the old synchronous hook code doesn't pay judge latency on every turn."""
    if not prev_reply or not (prefilter(user_text) if wide else keyword_hit(user_text)):
        return None
    v = parse(llm(JUDGE_SYS, judge_prompt(prev_reply, user_text, known)))
    if not v or v["p_correction"] < th_corr or v["durable"] < th_durable or not v["rule"] or not v["slug"]:
        return None
    return v


def hint(v: dict, promoted: bool) -> str:
    base = f"[coach] The user just corrected you. Implied standing rule: \"{v['rule']}\". Follow it from here on."
    if promoted:
        base += (" This is a repeat correction across sessions: save it now with the memory tool (target 'user' for a "
                 "preference, 'memory' for an environment fact), unless memory already says it.")
    return base
