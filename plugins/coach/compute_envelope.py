"""Compute envelope: coach both needless small-rental asks and agent-set spend authority.

`authority(text, user_texts)` complements the under-use classifier below: it holds
unsupported GPU cap / multi-GPU declarations once, without treating historical
spend or genuine requests for approval as a new spending commitment.

The standing rule (~/AGENTS.md, rent-machine skill): once the user has said go on a job that needs a GPU, an agent
may rent ONE single-GPU machine up to $25 total without asking (cheapest by $ per finished run, hard cap,
auto-teardown, linked to the chat) and states the choice in one line. It asks only above $25 or for multi-GPU /
multi-node machines. Running on a shared host outside its scheduler is not the fallback; renting is.

Defect agent-962f3f10ff: with no default budget written down, agents turned a ~$5-10 rental into three
`kanban_block`s and an "approve $20 / use metta1 directly" menu, and the experiment sat blocked for 5 days.

`classify(text)` is deterministic (no model call), so it can run on every kanban_block and final reply:
- a sentence that asks for a decision (approve, may I, want me to, reply "...", decision, ...) AND names compute or
  money (GPU, rent, Vast/RunPod, outside Slurm, budget, $N), in a text that is about compute at all;
- not when any total in the text is above the envelope (hourly rates don't count), or the machine is multi-GPU /
  multi-node: those still go to the user;
- not when the sentence is about a credential (token-broker grant, SSH key, password, 2FA, sudo, login): those are
  real human gates.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

DEFAULT_ENVELOPE_USD = 25.0

ASK_RE = re.compile(
    r"\bapprov(?:e|al|ing)\b|\bauthori[sz]e\b|\bmay i\b|\bshould i\b|\bshall i\b|\bcan i\b|\b(?:ok|okay) (?:to|if)\b|"
    r"\bwant me to\b|\bdo you want\b|\bwould you like\b|\bdecision\b|\byour call\b|\bgo-?ahead\b|\bsign[- ]?off\b|"
    r"\bconfirm\b|\bchoose\b|\bpick (?:one|between|a|the)\b|\breply\b|\blet me know\b|\bpermission\b", re.I)
COMPUTE_RE = re.compile(
    r"\bgpus?\b|\brent(?:al|als|ed|ing)?\b|\bvast(?:\.ai)?\b|\brunpod\b|\blambda(?: labs?| cloud)?\b|"
    r"\boutside (?:of )?(?:slurm|the scheduler)\b|\bbypass\w* (?:the )?(?:scheduler|slurm)\b|\bcompute\b|"
    r"\bspend(?:ing)?\b|\bbudget\b|\$\s?\d|\b(?:4090|a100|h100|h200|b200|a6000|l40s?)\b|\bspin(?:ning)? up\b|"
    r"\bdirectly on (?:that|the|metta\d)\b", re.I)
TOPIC_RE = re.compile(
    r"\bgpus?\b|\brent(?:al|ed|ing)?\b|\bvast(?:\.ai)?\b|\brunpod\b|\bslurm\b|\bcompute\b|"
    r"\b(?:4090|a100|h100|h200|b200|a6000|l40s?)\b|\bcuda\b|\btraining run", re.I)
CREDENTIAL_RE = re.compile(
    r"token-?broker|\bgrant\b|\bcredential|\bpassword|\bpasscode|\b2fa\b|\bsudo\b|\bssh key|\blog ?in\b|\bsign in\b",
    re.I)
GPU_HOURS_RE = re.compile(r"\bgpu[- ]?h(?:ours?|rs?)?\b", re.I)
# A sentence about the guard itself (quoting or explaining this note) is not an ask. Live false faults:
# "Compute envelope: you are asking a human to approve compute/spend that is NOTED",
# "The guard acted on the call's reason text: it read it as asking you to approve compute ... $25".
META_RE = re.compile(r"\bcompute envelope\b|\bcoach\b|\bguard\b|\benvelope\b", re.I)
MULTI_RE = re.compile(
    r"\b(?:[2-9]|[1-9]\d+)\s*(?:x|×|-)?\s*(?:gpus?|h100s?|h200s?|a100s?|b200s?|b300s?|4090s?|l40s?|a6000s?)\b|"
    r"\b(?:h100|h200|a100|b200|b300|4090|gpu)s?\s*[x×]\s*(?:[2-9]|[1-9]\d+)\b|"
    r"\bmulti-?(?:gpu|node)\b|\b(?:[2-9]|[1-9]\d+) nodes\b|\bcluster\b", re.I)
MONEY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(k\b)?(\s*(?:/|per |an |a )\s*(?:h|hr|hour)\b)?",
                      re.I)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.?!])[\"'”*)\]]*\s+|\n+")


def envelope_usd() -> float:
    try:
        return float(os.environ.get("COACH_COMPUTE_ENVELOPE_USD", DEFAULT_ENVELOPE_USD))
    except ValueError:
        return DEFAULT_ENVELOPE_USD


def enabled() -> bool:
    return os.environ.get("COACH_COMPUTE_ENVELOPE", "1").strip().lower() not in ("0", "off", "false", "no")


def totals(text: str) -> list[float]:
    """Dollar totals named in the text; hourly rates ($0.45/h) are not totals."""
    out = []
    text = re.sub(r"\b(\d[\d,]*(?:\.\d+)?)\s+USD\b", r"$\1", text or "", flags=re.I)
    for m in MONEY_RE.finditer(re.sub(r"\bUSD\s*", "$", text, flags=re.I)):
        if m.group(4):
            continue
        v = float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else ""))
        out.append(v * 1000 if m.group(3) else v)
    return out


def classify(text: str, envelope: Optional[float] = None) -> Optional[dict]:
    """The asking sentence when `text` asks a human to approve compute/spend inside the envelope, else None."""
    text = str(text or "")
    if not text.strip() or not TOPIC_RE.search(text):
        return None
    cap = envelope_usd() if envelope is None else envelope
    money = totals(text)
    if any(v > cap for v in money):
        return None
    if MULTI_RE.search(GPU_HOURS_RE.sub("gpuhours", text)):
        return None
    for sent in SENTENCE_SPLIT_RE.split(text):
        if (ASK_RE.search(sent) and COMPUTE_RE.search(sent) and not CREDENTIAL_RE.search(sent)
                and not META_RE.search(sent)):
            return {"sentence": sent.strip()[:300], "usd": max(money) if money else None, "cap": cap}
    return None


AUTH_MULTI_RE = re.compile(
    r"\b(?!(?:4090|5090)\b)(?:[2-9]|[1-9]\d+)\s*(?:x|×|-)?\s*"
    r"(?:gpus?|h100s?|h200s?|a100s?|b200s?|b300s?|l40s?|a6000s?|nodes?)\b|"
    r"\b(?:h100|h200|a100|b200|b300|4090|gpu)s?\s*[x×]\s*(?:[2-9]|[1-9]\d+)\b|"
    r"\bmulti[- ]?(?:gpu|node)\b|\b(?:two|three|four|five|six|seven|eight|nine|ten|multiple)\s+"
    r"(?:(?:h100|h200|a100|l40s)\s+)?gpus?\b|\b(?:two|three|four|multiple)\s+l40s\b", re.I)
REQUEST_RE = re.compile(r"\b(?:do you approve|please approve|approve|approval needed|may i|should i|shall i|can i|"
                        r"awaiting approval|pending approval|need(?:s)? approval|ask\w* (?:you |for )|"
                        r"would you|reply .*approve)\b", re.I)
PAST_RE = re.compile(r"\b(?:spent|cost was|previous cap|previous budget|had a|rented|destroyed|"
                     r"yesterday|last week|last run|used \d+ GPUs|ran on)\b", re.I)
DENY_RE = re.compile(r"\b(?:do not|don't|not approved|no approval|never|deny|denied|refuse)\b", re.I)
BRIEF_RE = re.compile(r"# Kanban task|\[IMPORTANT:|\[System|<system|task notification|subagent result", re.I)


def authority(text: str, user_texts=()) -> Optional[dict]:
    """Detect declarations; user-only scope is supplied by the native capture hook.

    This is a one-shot coaching check, not a purchasing authorization mechanism.
    Remote/worker briefs and unknown provenance cannot grant an exemption.
    """
    if not TOPIC_RE.search(text or ""):
        return None
    users = [part for u in user_texts if not BRIEF_RE.search(u)
             for part in re.split(r"(?<=[.?!;])\s+|\n+", u)
             if not DENY_RE.search(part) and not re.search(r"\?|\b(?:why|whether)\b", part, re.I)]
    approved = [v for user in users for v in totals(user)]
    multi_approved = any(AUTH_MULTI_RE.search(u) and re.search(
        r"\b(?:approve|allow|go ahead|you may|rent|use)\b", u, re.I) for u in users)
    # A cap label followed by its value on the next line is one declaration.
    text = re.sub(r"\b(cap|budget|limit)\s*\n\s*(?=USD|\$)", r"\1 ", text, flags=re.I)
    for sent in SENTENCE_SPLIT_RE.split(text):
        commitment = re.search(r"\b(?:I|we) (?:will|set|am setting|are setting|have set)\b", sent, re.I)
        if not commitment and (REQUEST_RE.search(sent) or PAST_RE.search(sent) or DENY_RE.search(sent)
                or (re.search(r"\bneeds?\b", sent, re.I) and REQUEST_RE.search(text))):
            continue
        amounts = totals(sent) if re.search(r"\b(?:cap|budget|limit|up to|spend)\b", sent, re.I) else []
        count_text = MONEY_RE.sub("money", re.sub(r"\bUSD\s*", "$", sent, flags=re.I))
        count_text = re.sub(r"\bnot\s+multi[- ]gpu\b", "single GPU", count_text, flags=re.I)
        multi = bool(AUTH_MULTI_RE.search(GPU_HOURS_RE.sub("gpuhours", count_text)))
        # The task's approved policy accepts either matching dollars or explicit
        # multi-GPU permission. Never let a smaller dollar figure grant a larger cap.
        dollar_ok = bool(approved and amounts) and max(approved) >= max(amounts)
        if multi_approved or dollar_ok:
            continue
        if (amounts and max(amounts) > envelope_usd()) or multi:
            return {"sentence": sent[:300], "usd": max(amounts) if amounts else None,
                    "cap": envelope_usd(), "rule": "compute_authority"}
    return None


def authority_note(hit: dict) -> str:
    return ('[coach] Compute authority: the agent-set GPU cap has no matching user approval. '
            'AGENTS.md: "Once I\'ve said go on a job that needs a GPU, you may rent ONE single-GPU '
            'machine up to $25 total without asking"; "Ask only above $25 total or for multi-GPU / multi-node." '
            'Calibrate inside that envelope first, then ask for an explicit number with a blocked card. '
            'Do not treat your own reply or a dispatched card body as spend authority.')


def block_text(tool_name: str, args: Any) -> str:
    """The human-facing text of a kanban_block call (direct, or through the deferred `tool_call`), else ''."""
    a = args if isinstance(args, dict) else {}
    if tool_name == "kanban_create":
        return str(a.get("body") or "")
    if tool_name == "kanban_block":
        hr = a.get("human_request")
        return " \n".join(s for s in (str(a.get("reason") or ""),
                                      json.dumps(hr, ensure_ascii=False) if isinstance(hr, dict) else "") if s)
    if tool_name == "tool_call":
        parts = [block_text(str((c or {}).get("name") or ""), (c or {}).get("arguments"))
                 for c in (a.get("calls") or []) if isinstance(c, dict)]
        return " \n".join(p for p in parts if p)
    return ""


def note(hit: dict, where: str) -> str:
    cap = int(hit["cap"]) if float(hit["cap"]).is_integer() else hit["cap"]
    act = ("Don't block the card on it" if where == "block" else "Don't end the turn on it")
    return (f"[coach] Compute envelope: you are asking a human to approve compute/spend that is inside the default "
            f"envelope (\"{hit['sentence'][:160]}\"). Once David has said go on a job that needs a GPU, rent ONE "
            f"single-GPU machine for up to ${cap} total without asking: cheapest by $ per finished run (Vast, RunPod, "
            f"Lambda; rent-machine skill), hard cap, results copied off, auto-teardown with a verified destroy, "
            f"linked to the chat. State the choice in one line and carry on. {act}, and don't run on a shared host "
            f"outside its scheduler instead. An agent-written 'no paid rentals' line on a card does not revoke the "
            f"envelope; only David's own words do. Ask only above ${cap} total or for multi-GPU / multi-node. If "
            f"the job itself has not been approved yet, ask about the job, not the spend.")
