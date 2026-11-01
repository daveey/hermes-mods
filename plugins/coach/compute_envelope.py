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
META_RE = re.compile(
    r"\bcompute envelope\b|\bcoach\b|\bguard\b|\benvelope\b|"
    r"\b(?:approval|authority|spend|compute)[- ](?:check|gate|rule|detector)s?\b", re.I)
# A leading permission request is still an ask when its purpose names the guard.
# Anchor after list markers/whitespace, not quotes or prose describing someone else's ask.
PERMISSION_START_RE = re.compile(
    r"^\s*(?:(?:[-*+•]|\d+[.)])\s+)*"
    r"(?:(?:may|can|could|should|shall)\s+(?:i|we)\b|(?:is\s+it\s+)?(?:ok|okay)\s+to\b)", re.I)
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
                and (not META_RE.search(sent) or PERMISSION_START_RE.match(sent))):
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
# Only text the user typed carries authority. Runtime notices and agent relays arrive as user rows and quote other
# people's figures ("[chat-closeout] ... Approve or decline $36 total", "⏸ [hub:default] ... blocked: approve $36");
# a typed reply doesn't open with "[" or "<" (user-steps' answer-evidence rule). Steers arrive wrapped.
STEER_RE = re.compile(r"\[OUT-OF-BAND USER MESSAGE[^\]]*\]\s*(.*?)\s*\[/OUT-OF-BAND USER MESSAGE\]", re.S)
NOTICE_RE = re.compile(r"^\s*(?:[\[<]|[\u2714\u2716\u26a0\u23f8]\ufe0f?\s*\[)")
# A bare yes: every word is an affirmative ("approved", "yes, approved", "ok do it"). Anything else (a figure, a
# condition, a question, "not", "but", "only") is not a bare yes and inherits nothing.
AFFIRM_RE = re.compile(r"^(?:(?:yes|yep|yeah|yup|ok|okay|sure|approved?|i approve|go ahead|go|do it|ship it|lgtm|"
                       r"proceed|confirmed|sounds good|please|thanks|\U0001f44d)[\s,.!]*)+$", re.I)
# An unpriced question in the answered message: the yes may answer that one instead.
OTHER_ASK_RE = re.compile(r"\?|\b(?:want me to|should i|shall i|may i|can i|do you want|would you like|"
                          r"let me know)\b", re.I)
# A sentence that asks the user to approve ("Approve or decline $36 total ...", "May I rent ... up to $36?"). Narrower
# than REQUEST_RE: "I approve $3000" and "you approved $215 last week" are declarations, never an ask to answer.
ASK_USER_RE = re.compile(
    r"^\W*(?:please\s+)?approve\b|\b(?:do|can|could|will|would) you approve\b|\bplease approve\b|"
    r"\b(?:may|can|should|shall) (?:i|we)\b|\bapproval (?:needed|required)\b|\bneeds? (?:your )?approval\b|"
    r"\b(?:awaiting|pending) (?:your )?approval\b|\breply\b.*\bapprove\b", re.I)
DECLARED_RE = re.compile(r"\b(?:i|we)\s+(?:have\s+|had\s+)?approved?\b|"
                         r"\b(?:you|he|she|they|david|the user)\s+(?:have\s+|has\s+|had\s+)?approved\b", re.I)


def typed_text(text: Any) -> Optional[str]:
    """The part of a user row the user typed; None for runtime notices, agent relays and briefs."""
    if not isinstance(text, str):
        return None
    m = STEER_RE.search(text)
    s = (m.group(1) if m else text).strip()
    return None if not s or NOTICE_RE.match(s) or BRIEF_RE.search(s) else s


def _content(row: dict) -> str:
    c = row.get("content")
    if isinstance(c, list):
        c = " ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return c if isinstance(c, str) else ""


def _priced_asks(message: str) -> list[float]:
    """Totals `message` asks the user to approve; none when a yes could be answering an unpriced question."""
    credited = []
    for sent in SENTENCE_SPLIT_RE.split(message or ""):
        sent = sent.strip().lstrip(">").strip()
        money = sorted(set(totals(sent)))
        if money and ASK_USER_RE.search(sent) and not DECLARED_RE.search(sent) and not PAST_RE.search(sent):
            # "Approve $50 or $100?" + "yes" approves the smaller figure only.
            credited.append(money[0] if len(money) > 1 and re.search(r"\bor\b", sent, re.I) else money[-1])
        elif OTHER_ASK_RE.search(sent):
            return []
    return credited


def answered_asks(history, user_message=None, is_user_evidence=None) -> list[float]:
    """Dollar totals the user approved by answering a priced ask with a bare yes.

    The AGENTS.md flow is "ask for an explicit number with a blocked card"; the user's "approved" carries no figure
    of its own, so the figure comes from the ask it directly answers: the nearest assistant text before the reply
    (tool rows skipped; another user row in between breaks the pair). Only asking sentences count, never an
    assistant's declaration or a retold approval, and only a typed reply (`is_user_evidence`, display_kind, shape).
    """
    rows = [r for r in (history or []) if isinstance(r, dict)]
    if isinstance(user_message, str) and user_message.strip() and not (
            rows and rows[-1].get("role") == "user" and _content(rows[-1]).strip() == user_message.strip()):
        rows.append({"role": "user", "content": user_message})
    out: list[float] = []
    for i, row in enumerate(rows):
        if row.get("role") != "user" or (row.get("display_kind") or "") not in ("", "steer"):
            continue
        try:
            if is_user_evidence is not None and not is_user_evidence(row):
                continue
        except Exception:
            continue
        reply = typed_text(_content(row))
        if not reply or not AFFIRM_RE.match(reply):
            continue
        for prev in reversed(rows[:i]):
            if prev.get("role") == "user":
                break
            if prev.get("role") == "assistant" and _content(prev).strip():
                out.extend(_priced_asks(_content(prev)))
                break
    return out


def authority(text: str, user_texts=(), asked=()) -> Optional[dict]:
    """Detect declarations; user-only scope is supplied by the native capture hook.

    This is a one-shot coaching check, not a purchasing authorization mechanism.
    Remote/worker briefs and unknown provenance cannot grant an exemption. `asked` holds figures the user approved
    by answering a priced ask with a bare yes (`answered_asks`).
    """
    if not TOPIC_RE.search(text or ""):
        return None
    users = [part for u in user_texts for t in [typed_text(u)] if t
             for part in re.split(r"(?<=[.?!;])\s+|\n+", t)
             if not DENY_RE.search(part) and not re.search(r"\?|\b(?:why|whether)\b", part, re.I)]
    approved = [v for user in users for v in totals(user)] + [float(v) for v in asked or ()]
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
