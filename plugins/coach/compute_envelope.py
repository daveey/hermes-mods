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
# A sentence that says it is not asking ("My report doesn't ask anyone to approve compute or spend").
NOT_ASKING_RE = re.compile(r"\b(?:doesn['\u2019]t|does not|don['\u2019]t|do not|didn['\u2019]t|did not|isn['\u2019]t|"
                           r"is not|no need to|nothing to)\s+(?:\w+\s+){0,2}(?:ask|approv|decid)\w*", re.I)
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

# Mention, not use: text inside quotes, multi-word inline code, fenced code and blockquotes is somebody else's words
# (a rule being proposed, a test fixture, the coach's own note), never the agent asking or declaring. Live false
# holds 2026-11-03: a review quoting proposed AGENTS.md wording ("A spend/change ask (card, clarify or reply) ..."),
# a card body describing this detector. A quote right after "reply"/"say"/"type" is the answer the agent asks for
# ('Reply "approve $20"'), and a directive attribute (t="...") is the agent's own text, so those stay.
MENTION_SPAN_RE = re.compile(r"```.*?(?:```|\Z)|^[ \t]*>[^\n]*|`[^`\n]*`|"
                             r"\"[^\"\n]{0,600}\"|\u201c[^\u201d\n]{0,600}\u201d", re.S | re.M)
ANSWER_CUE_RE = re.compile(r"\b(?:reply|say|type|answer|respond|send|write)(?:\s+(?:with|back|me|us))?[\s*_:]*$", re.I)


def unquoted(text: str) -> str:
    """`text` with quoted / code / blockquoted spans blanked, except a quoted answer the text asks the reader for."""
    text = str(text or "")

    def sub(m: re.Match) -> str:
        before = text[max(0, m.start() - 40):m.start()]
        # `::user-step{t="..."}` carries the agent's own ask as an attribute value.
        if m.group(0)[0] in "\"\u201c" and (ANSWER_CUE_RE.search(before) or before.endswith("=")):
            return m.group(0)
        # Inline code pairs left to right; a one-token span (`choices`, `$3000`) is an identifier and stays. Matching
        # only spans with whitespace paired the closing tick of `choices` with the next opening one and blanked the
        # prose between (live 2026-11-03, a card body: "(`Unblock = …`, `Archive = …`, `choices`) describe ...").
        g = m.group(0)
        if g[0] == "`" and not g.startswith("```") and not re.search(r"\s", g):
            return g
        return " " if "\n" not in m.group(0) else "\n"
    return MENTION_SPAN_RE.sub(sub, text)


def envelope_usd() -> float:
    try:
        return float(os.environ.get("COACH_COMPUTE_ENVELOPE_USD", DEFAULT_ENVELOPE_USD))
    except ValueError:
        return DEFAULT_ENVELOPE_USD


def enabled() -> bool:
    return os.environ.get("COACH_COMPUTE_ENVELOPE", "1").strip().lower() not in ("0", "off", "false", "no")


def _money_text(text: str) -> str:
    """`text` with "3,000 USD" / "USD 3,000" written as "$3,000", so MONEY_RE sees every figure."""
    text = re.sub(r"\b(\d[\d,]*(?:\.\d+)?)\s+USD\b", r"$\1", text or "", flags=re.I)
    return re.sub(r"\bUSD\s*", "$", text, flags=re.I)


def _usd(m: re.Match) -> float:
    v = float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else ""))
    return v * 1000 if m.group(3) else v


def totals(text: str) -> list[float]:
    """Dollar totals named in the text; hourly rates ($0.45/h) are not totals."""
    return [_usd(m) for m in MONEY_RE.finditer(_money_text(text)) if not m.group(4)]


# A figure is a cap the agent set only when a cap word governs it: "$60 cap", "budget: $3,000", "up to $3000",
# "spend $3000", "capped at $90", "$3000 GPU budget", "spend no more than $70", with only connecting words between.
# Sharing a sentence with a cap word is not enough. Live false holds (coach logs on both hosts, 10-31 .. 11-02):
# an account readback ("- **Spend:** credit is $103.49"), spend so far ("Spend so far is $0.56 of the $36"), a
# quoted example ("OK to spend $500/month ...?"), code being described ("the $36 check runs before the lock").
CAP_WORDS = frozenset({"cap", "caps", "capped", "budget", "budgets", "budgeted", "limit", "limits", "limited",
                       "ceiling", "upto", "spend"})
CAP_CONNECTORS = frozenset({
    "a", "an", "the", "of", "at", "to", "is", "be", "set", "it", "its", "this", "for", "run", "card", "per", "each",
    "total", "max", "maximum", "hard", "soft", "firm", "strict", "overall", "new", "gpu", "gpus", "rental", "compute",
    "about", "around", "approximately", "roughly", "under", "below", "within", "no", "more", "than", "most", "not",
    "exceed", "over"})
# "Spend:" / "spend so far" / "spend is" label a measured amount, not a commitment to spend.
SPEND_LABEL_RE = re.compile(r"\bspend(?=\s*:|\s+(?:so far|to date|is|was)\b)", re.I)
# A figure followed by these is a balance or a running total ("$40 left", "$103.49 credit remaining").
READBACK_AFTER_RE = re.compile(r"\s*(?:left|remaining|so far|to date|spent|used|available|unused|in credit|credit|"
                               r"balance)\b", re.I)
QUOTED_RE = re.compile(r"\"[^\"\n]{0,400}\"|\u201c[^\u201d\n]{0,400}\u201d")
CLAUSE_BREAK_RE = re.compile(r"[,;()\[\]|]|\s[-\u2013\u2014]\s")


def _cap_words(text: str) -> list[str]:
    """Lower-case words of `text`, "up to" as one word, a hyphenated word as its last part ("sub-cap" -> "cap")."""
    text = re.sub(r"\bup\s+to\b", "upto", text.lower())
    return [w.rsplit("-", 1)[-1] for w in re.findall(r"[a-z]+(?:-[a-z]+)*", text)]


def _governed(words) -> bool:
    """A cap word reached through connecting words only, in the order given (nearest the figure first)."""
    for w in list(words)[:6]:
        if w in CAP_WORDS:
            return True
        if w not in CAP_CONNECTORS:
            return False
    return False


def cap_amounts(sent: str) -> list[float]:
    """Dollar totals in `sent` that a cap word governs. Quoted text, readbacks and hourly rates are not caps."""
    s = QUOTED_RE.sub(" ", _money_text(sent)).replace("*", "").replace("`", "")
    s = SPEND_LABEL_RE.sub("spent", s)
    found = list(MONEY_RE.finditer(s))
    out = []
    for i, m in enumerate(found):
        if m.group(4):
            continue
        left = s[found[i - 1].end() if i else 0:m.start()]
        right = s[m.end():found[i + 1].start() if i + 1 < len(found) else len(s)]
        if READBACK_AFTER_RE.match(right):
            continue
        left = CLAUSE_BREAK_RE.split(left)[-1]
        right = CLAUSE_BREAK_RE.split(right)[0]
        if _governed(reversed(_cap_words(left))) or _governed(_cap_words(right)):
            out.append(_usd(m))
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
    for sent in SENTENCE_SPLIT_RE.split(unquoted(text)):
        if (ASK_RE.search(sent) and COMPUTE_RE.search(sent) and not CREDENTIAL_RE.search(sent)
                and not NOT_ASKING_RE.search(sent)
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
# A sentence about asking ("a real over-$25 / multi-GPU ask is still held") talks about the flow the rule wants, and
# one about the rule, its wording, its detector or its tests describes or proposes a rule. Neither is the agent
# granting itself spend; an "I/we will|set" commitment in the same sentence still counts.
ASK_TALK_RE = re.compile(r"\bask(?:s|ed|ing)?\b", re.I)
RULE_TALK_RE = re.compile(
    r"\bcoach\b|\bdetectors?\b|\bAGENTS\.md\b|\brules?\b|\bwording\b|\bsentences?\b|"
    r"\b(?:existing|new|regression|unit) tests?\b|\btests?/\S+\.py\b|\btest_\w+|\bfixtures?\b|"
    r"\b(?:is|are|still|be|gets?) held\b", re.I)
# The agent committing itself ("I will", "I'll", "I've set", "we are setting"): rule talk around it does not excuse it.
COMMITMENT_RE = re.compile(r"\b(?:I|we)(?:\s+(?:will|have|am|are)|['\u2019](?:ll|ve|m|re))?\s+(?:set|setting)\b|"
                           r"\b(?:I|we)\s+will\b|\b(?:I|we)['\u2019]ll\b", re.I)
# "the two GPU sentences", "2 GPU rules": a count of words about GPUs, not of GPUs.
GPU_NOT_MACHINE_RE = re.compile(
    r"\bgpus?(?=\s+(?:sentences?|rules?|lines?|words?|quotes?|mentions?|paragraphs?|bullets?|phrases?|clauses?|"
    r"references?|examples?|tests?|cases?|checks?|detectors?|questions?|asks?|headings?|notes?|policies)\b)", re.I)


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


def _not_answerable(row: dict) -> bool:
    """A row a typed yes cannot be answering: a compaction summary (the user never saw it as an ask, and it
    paraphrases older asks) or an observed row. Core's own summary test, with a prefix fallback outside core."""
    if row.get("observed") or row.get("_compressed_summary"):
        return True
    try:
        from agent.context_compressor import is_compaction_summary_message
        return bool(is_compaction_summary_message(row))
    except Exception:
        return _content(row).lstrip().startswith(("[CONTEXT COMPACTION", "[CONTEXT SUMMARY]:"))


def answered_asks(history, user_message=None, is_user_evidence=None) -> list[float]:
    """Dollar totals the user approved by answering a priced ask with a bare yes.

    The AGENTS.md flow is "ask for an explicit number with a blocked card"; the user's "approved" carries no figure
    of its own, so the figure comes from the ask it directly answers: the nearest assistant text before the reply
    (tool rows skipped; another user row, a compaction summary or an observed row in between breaks the pair). Only
    asking sentences count, never an assistant's declaration or a retold approval, and only a typed reply
    (`is_user_evidence`, display_kind, shape).
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
            if prev.get("role") == "user" or _not_answerable(prev):
                break
            if prev.get("role") == "assistant" and _content(prev).strip():
                out.extend(_priced_asks(_content(prev)))
                break
    return out


def persisted_asks(session_id: str, scope) -> list[float]:
    """`answered_asks` over the session's persisted display transcript (`scope` is the coach's goal_scope module).

    Compaction moves an answered ask out of the live window the hook sees, but the approval it recorded does not
    expire with the context: the pair is still in what the user saw. Only typed replies count, as in the window.
    """
    return answered_asks(scope.display_transcript(session_id), None, is_user_evidence=scope.is_evidence_row)


def authority(text: str, user_texts=(), asked=(), persisted=None) -> Optional[dict]:
    """Detect declarations; user-only scope is supplied by the native capture hook.

    This is a one-shot coaching check, not a purchasing authorization mechanism.
    Remote/worker briefs and unknown provenance cannot grant an exemption. `asked` holds figures the user approved
    by answering a priced ask with a bare yes (`answered_asks`). `persisted` (callable -> more such figures, from
    the persisted transcript) is read only for a would-be hold: most replies never get that far, and the read
    costs a transcript projection. A failed read credits nothing.
    """
    hit = _authority(text, user_texts, asked)
    if hit and persisted is not None:
        try:
            more = [float(v) for v in persisted() or ()]
        except Exception:
            more = []
        if more:
            hit = _authority(text, user_texts, [*(asked or ()), *more])
    return hit


def _authority(text: str, user_texts=(), asked=()) -> Optional[dict]:
    if not TOPIC_RE.search(text or ""):
        return None
    users = [part for u in user_texts for t in [typed_text(u)] if t
             for part in re.split(r"(?<=[.?!;])\s+|\n+", t)
             if not DENY_RE.search(part) and not re.search(r"\?|\b(?:why|whether)\b", part, re.I)]
    approved = [v for user in users for v in totals(user)] + [float(v) for v in asked or ()]
    multi_approved = any(AUTH_MULTI_RE.search(u) and re.search(
        r"\b(?:approve|allow|go ahead|you may|rent|use)\b", u, re.I) for u in users)
    # A cap label followed by its value on the next line is one declaration.
    text = re.sub(r"\b(cap|budget|limit)\s*\n\s*(?=USD|\$)", r"\1 ", unquoted(text), flags=re.I)
    for sent in SENTENCE_SPLIT_RE.split(text):
        commitment = COMMITMENT_RE.search(sent)
        if not commitment and (REQUEST_RE.search(sent) or PAST_RE.search(sent) or DENY_RE.search(sent)
                or ASK_TALK_RE.search(sent) or RULE_TALK_RE.search(sent)
                or (re.search(r"\bneeds?\b", sent, re.I) and REQUEST_RE.search(text))):
            continue
        amounts = cap_amounts(sent)
        count_text = MONEY_RE.sub("money", re.sub(r"\bUSD\s*", "$", sent, flags=re.I))
        count_text = re.sub(r"\b(?:not|no|without)\s+(?:gpus?\s+or\s+)?multi[- ]?(?:gpu|node)s?\b", "single GPU",
                            count_text, flags=re.I)
        count_text = GPU_NOT_MACHINE_RE.sub("gpuword", count_text)
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


def _hr_text(a: dict) -> str:
    hr = a.get("human_request")
    # Plain "field: value" lines, not JSON: JSON quotes every value, and quoted text is a mention (`unquoted`).
    return "\n".join(f"{k}: {v}" for k, v in hr.items() if isinstance(v, (str, int, float))) \
        if isinstance(hr, dict) else ""


# A human_request action's consequence clause: what David's answer does ("Unblock = run ...", "archive: stop",
# "Yes = rent ..."). It runs to the next ";", sentence end or the next such clause.
CONSEQUENCE_RE = re.compile(
    r"\b(?:unblock(?:ing)?|archiv(?:e|ing)|yes|no|approv(?:e|al|ing)|declin(?:e|ing)|deny|silence)\s*"
    r"(?:=|:|\u2192|->|means\b)\s*(.*?)(?=;|(?<=[.?!])\s|\n|\b(?:unblock|archive|yes|no)\s*(?:=|:|\u2192|->)|$)",
    re.I | re.S)


def _decides_over_envelope(hr: Any) -> bool:
    """True when the human_request's answer decides over-envelope compute: its consequence clauses (or `choices`)
    name a figure above the envelope or multi-GPU. Then that compute is the question put to David, not an agent
    declaration. Live false hold 2026-11-03 (hub, kanban_block): "Unblock = run the full 44K SFT on 8xH100
    (~$150-250)" was read as the agent setting itself a multi-GPU run."""
    if not isinstance(hr, dict):
        return False
    said = [m.group(1) for m in CONSEQUENCE_RE.finditer(str(hr.get("action") or ""))]
    for c in hr.get("choices") or ():
        if isinstance(c, dict):
            said += [str(c.get("label") or ""), str(c.get("value") or "")]
    for s in said:
        if any(v > envelope_usd() for v in totals(s)) or AUTH_MULTI_RE.search(GPU_HOURS_RE.sub("gpuhours", s)):
            return True
    return False


def _addressed_to_human(a: dict) -> bool:
    """A kanban_create a human reads as an ask: created blocked, or carrying a human_request. Any other card runs at
    once and its body is a worker's brief, never a message to David."""
    return str(a.get("initial_status") or "").strip().lower() == "blocked" or bool(a.get("human_request"))


def block_text(tool_name: str, args: Any, human_only: bool = False) -> str:
    """The text of a kanban_block / kanban_create call (direct, or through the deferred `tool_call`), else ''.

    `human_only` keeps only text addressed to a human: the under-use check (`classify`) reads that, because only a
    human can be asked to approve. Live false hold 2026-11-02: a running plan-only card's body listed PLAN.md
    sections ("compute quote and approval stages A/B/C") and was refused as an in-envelope spend ask.
    compute_authority reads every card body (human_only False): a worker brief can still declare a cap. A create's
    human_request is read only as an ask: it is the ask itself, and its action line says what unblock does
    ("Unblock = run the full SFT on 8xH100"), which compute_authority would read as the agent declaring multi-GPU.
    A block's human_request is read for compute_authority unless its answer decides over-envelope compute
    (`_decides_over_envelope`): then it is that ask, and only the reason is authority text.
    """
    a = args if isinstance(args, dict) else {}
    if tool_name == "kanban_create":
        if not human_only:
            return str(a.get("body") or "")
        if not _addressed_to_human(a):
            return ""
        return " \n".join(s for s in (str(a.get("body") or ""), _hr_text(a)) if s)
    if tool_name == "kanban_block":
        # compute_authority skips a human_request whose answer decides over-envelope compute (it is the ask);
        # the reason is always read, and so is a human_request whose answer decides no compute.
        hr = "" if not human_only and _decides_over_envelope(a.get("human_request")) else _hr_text(a)
        return " \n".join(s for s in (str(a.get("reason") or ""), hr) if s)
    if tool_name == "tool_call":
        parts = [block_text(str((c or {}).get("name") or ""), (c or {}).get("arguments"), human_only)
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
