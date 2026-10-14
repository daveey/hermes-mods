"""fix_loop: notice when the user reports that the agent's last fix didn't work, across turns.

The per-turn guards (repeat_failure, tool_guardrails) reset every user turn, so a loop of
"fixed!" -> "still broken" -> "fixed!" -> "still broken" is invisible to them. Anthropic's guidance:
after two failed corrections, stop and start fresh. Here:

- Layer 1: a broad regex prefilter on the user's message (cheap, recall-oriented).
- Layer 2: aux judge `coach_fixloop` sees the agent's previous final message + the user's message and
  scores p(failed_fix): the user reports that what the agent said it fixed/did is still wrong.
- 1st confirmed failure in a window of turns -> hint: reproduce on the user's path, root-cause, verify.
- 2nd+ -> hint: stop patching; list what was tried + hypotheses; test against raw evidence; second opinion;
  offer a clean restart brief.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Optional

PREFILTER_RE = re.compile(
    r"\b(still|again|same (?:error|issue|problem|thing|bug)|didn'?t (?:work|fix|help|change)|doesn'?t (?:work|help)|"
    r"not (?:working|fixed|loading|showing|updating|there)|isn'?t (?:working|fixed|there)|nope|broken|no change|"
    r"nothing (?:changed|happens|happened)|wrong|i don'?t see|can'?t see|doesn'?t show|not showing|"
    r"try again|failed|fails|error|crash(?:es|ed)?|regress)", re.I)
IMAGE_ONLY_RE = re.compile(r"^\s*(\[(?:image|screenshot)\]\s*)+$|^\s*\[Image attached at:[^\]]*\]\s*(\[screenshot\])?\s*$", re.I)

WINDOW_TURNS = 8  # failures count toward a loop within this many user turns


def prefilter(user_text: str) -> bool:
    t = (user_text or "")[:1500]
    return bool(PREFILTER_RE.search(t) or IMAGE_ONLY_RE.match(t))


JUDGE_SYS = """You watch a coding/ops agent's conversation for FAILED-FIX LOOPS. You see the agent's previous final message
and the user's next message. Score p_failed_fix (0-1): the user is reporting that something the agent said it had
fixed, built, changed, deployed or made work is in fact still broken, unchanged or wrong when the user tries it.

High (0.8+): "still broken", "same error", "I don't see it", "nothing changed", a screenshot of the same failure, right
after the agent claimed it was done/fixed/live/working.
Low: a NEW problem in a different area; a new request; feedback on taste/design (not a failure); the agent had said
it was NOT done yet or not verified; the user reporting results of an experiment the agent didn't claim would pass;
questions; approvals.

Return ONLY JSON: {"p_failed_fix": p, "issue": "<=8 words naming what is still broken>"}"""


def judge_prompt(prev_reply: str, prev_log: list[str], user_text: str) -> str:
    log = "\n".join(prev_log[-25:]) or "(none recorded)"
    return (f"AGENT'S PREVIOUS FINAL MESSAGE:\n{(prev_reply or '')[-3000:]}\n\n"
            f"AGENT'S TOOL CALLS IN THAT TURN (truncated):\n{log[:3000]}\n\n"
            f"USER'S NEW MESSAGE:\n{(user_text or '')[:1500]}")


def parse(raw: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return {"p": float(d.get("p_failed_fix") or 0), "issue": str(d.get("issue") or "")[:80]}
    except Exception:
        return None


HINT_1 = ("[coach] The user reports your last fix didn't work ({issue}). Before patching again: reproduce it on the user's "
          "actual path (same command/app/URL/device), find the root cause from that evidence, then fix and verify it the "
          "same way the user will see it.")
HINT_2 = ("[coach] This is failed fix #{n} in a row ({issue}). Stop iterating on patches. Write down (1) what you've tried, "
          "(2) 2-3 hypotheses for the cause, (3) the raw evidence that would distinguish them; check that evidence first. "
          "Consider a read-only second opinion (delegate_task to a reviewer with the transcript) or offer the user a "
          "clean restart with a brief (Goal / Tried so far / Repro / Done when). Don't claim fixed without reproducing "
          "the user's exact failure going away.")


def settings() -> dict:
    """COACH_FIXLOOP (1/0, default on) and COACH_FIXLOOP_THRESHOLD (default 0.8)."""
    on = os.environ.get("COACH_FIXLOOP", "1").strip().lower() not in ("0", "off", "false", "no")
    try:
        th = float(os.environ.get("COACH_FIXLOOP_THRESHOLD", "0.8"))
    except ValueError:
        th = 0.8
    return {"on": on, "threshold": th}


def aux_judge(system: str, prompt: str) -> str:
    """Aux task `coach_fixloop` (auxiliary.coach_fixloop.{provider,model})."""
    from agent.auxiliary_client import call_llm  # type: ignore
    resp = call_llm(task="coach_fixloop", messages=[{"role": "system", "content": system},
                                                   {"role": "user", "content": prompt}],
                    max_tokens=120, temperature=0, timeout=20)
    return (getattr(resp.choices[0].message, "content", None) or "").strip()


def assess(state: dict, turn: int, user_text: str, llm: Callable[[str, str], str],
           threshold: float = 0.8) -> Optional[dict]:
    """state: per-session dict with last_reply, last_log, fails (list of turn numbers).
    Returns {"n", "issue", "p", "hint"} when this message confirms a failed fix, else None."""
    if not state.get("last_reply") or not prefilter(user_text):
        return None
    v = parse(llm(JUDGE_SYS, judge_prompt(state["last_reply"], state.get("last_log") or [], user_text)))
    if not v or v["p"] < threshold:
        return None
    fails = [t for t in state.get("fails", []) if turn - t < WINDOW_TURNS] + [turn]
    state["fails"] = fails
    n = len(fails)
    hint = (HINT_1 if n == 1 else HINT_2).format(n=n, issue=v["issue"] or "see the user's message")
    return {"n": n, "issue": v["issue"], "p": v["p"], "hint": hint}
