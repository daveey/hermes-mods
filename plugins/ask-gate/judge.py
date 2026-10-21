"""ask-gate judge: decides, per ask sentence, whether David really has to be asked.

Verdicts: ok | already_answered | already_done | unverified | agent_can_do.
Deterministic checks run first (live card status); the aux model only sees what is left. The model call is
injectable (``llm=``) so tests need no network.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

MIN_CONFIDENCE = 0.8
VERDICTS = ("ok", "already_answered", "already_done", "unverified", "agent_can_do")

SYSTEM = """You audit a draft chat reply BEFORE the user (David) sees it. It contains sentences that ask David \
to do, decide or provide something, or say something is waiting on him. A wrong hold is costly (it delays and \
confuses the reply); a wrong pass is also costly (David gets asked something that is stale, already answered, \
unchecked, or that the agent could do itself). Default to ok, but check each item against the verdict tests below before settling on it. Return a non-ok verdict ONLY when you can point at \
specific evidence in the material below, and give a concrete `how`.

Verdicts:
- already_done: live card status says the card/thing is done/archived/completed, or a tool result this turn shows \
it is done, while the reply says it is still waiting on him.
- already_answered: the answered-asks ledger or David's recent messages show he already answered, dismissed or did \
exactly this thing. Asking again is a repeat. (A different, new question on the same topic is ok.)
- unverified: the reply asserts, as the reason David must act, a checkable fact about an account, permission, card, \
subscription, install or setting (e.g. "you are signed out", "the token lacks X", "it is not revoked", "the bot is \
not in the channel") that NO tool call this turn verified and that one cheap tool call could verify. `how` = the \
exact check. Not unverified: facts the tool log shows, facts from live card status, and anything about David's own \
body, home, phone, devices in his hands or what he wants.
- agent_can_do: the capability sheet names a specific scope, host or login that would directly do exactly what the \
reply asks David to do (e.g. a *-minter scope for a missing API permission; ssh to a named host for a command he is \
asked to run). `how` must name that scope/host/command. A vague "a browser is signed in" is not enough. Typical \
case: the reply says an API token lacks a permission and asks David to add it in a dashboard, and the sheet has a \
minter scope (name ends in "-minter") or a broader credential scope for that vendor: agent_can_do, always.
- ok: everything else. In particular ALWAYS ok: physical actions (plug in, press, hold, say a phrase, walk to a \
device), clicking Allow / typing a password or code into a prompt only he can see, approving an OAuth consent screen for the \
agent, spending money or any irreversible/destructive action, choices between options, preferences, optional \
offers ("Want me to look into X?"), "tell me when X is done" (waiting on a person or on time), UI legends and \
explanations that merely describe a state, hypotheticals, and facts already verified this turn.

An ask sentence can bundle several items ("Needs you: a | b | c"): check every item and return the first non-ok \
verdict among them, naming that item in the reason.

Output ONLY JSON: {"asks":[{"i":<index>,"verdict":"<verdict>","confidence":<0..1>,"reason":"<one short sentence naming \
the evidence>","how":"<concrete tool/command for agent_can_do/unverified, else empty>"}]}"""


def build_user_prompt(asks: list[str], tools_text: str, user_msgs: list[str], card_text: str,
                      ledger_text: str, caps: str, reply: str = "") -> str:
    asks_t = "\n".join(f"[{i}] {a}" for i, a in enumerate(asks))
    users_t = "\n".join(f"- {m[:240]}" for m in user_msgs[-30:]) or "(none)"
    return (f"ASK SENTENCES:\n{asks_t}\n\nFULL DRAFT REPLY (context):\n{reply[:2500] or '(n/a)'}\n\n"
            f"THIS TURN'S TOOL CALLS AND RESULTS:\n{tools_text}\n\nLIVE CARD STATUS (read from the database now):\n"
            f"{card_text or '(no cards mentioned)'}\n\nANSWERED-ASKS LEDGER:\n{ledger_text or '(empty)'}\n\n"
            f"DAVID'S LAST MESSAGES (oldest first):\n{users_t}\n\n{caps}")


def parse_verdicts(raw: str, n: int) -> list[dict]:
    """Parse the model output into exactly ``n`` verdict dicts; anything unparsable becomes ok (fail open)."""
    out = [{"i": i, "verdict": "ok", "reason": "", "how": ""} for i in range(n)]
    if not raw:
        return out
    s = raw.strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    m = re.search(r"\{.*\}", s, re.S)
    try:
        data = json.loads(m.group(0) if m else s)
    except ValueError:
        return out
    for item in data.get("asks", []) if isinstance(data, dict) else []:
        try:
            i = int(item.get("i"))
        except (TypeError, ValueError, AttributeError):
            continue
        v = str(item.get("verdict", "ok")).strip().lower()
        try:
            conf = float(item.get("confidence", 1.0))
        except (TypeError, ValueError):
            conf = 1.0
        how = str(item.get("how", "")).strip()
        # a non-ok verdict must be confident, and unverified/agent_can_do must say what to do instead
        if v != "ok" and (conf < MIN_CONFIDENCE or (v in ("unverified", "agent_can_do") and len(how) < 8)):
            v = "ok"
        if 0 <= i < n and v in VERDICTS:
            out[i] = {"i": i, "verdict": v, "reason": str(item.get("reason", ""))[:240], "how": how[:240]}
    return out


_DONE = {"done", "completed", "archived"}


def deterministic(asks: list[str], card_status: dict[str, dict]) -> dict[int, dict]:
    """Verdicts that need no model: an ask naming a card that is already done/archived."""
    res: dict[int, dict] = {}
    for i, a in enumerate(asks):
        for cid in re.findall(r"\bt_[0-9a-f]{8}\b", a):
            st = card_status.get(cid)
            if st and st["status"] in _DONE:
                res[i] = {"i": i, "verdict": "already_done", "how": f"kanban_show {cid}",
                          "reason": f"{cid} is {st['status']} in the database (live), so nothing is waiting on David."}
                break
    return res


def judge(asks: list[str], *, tools_text: str, user_msgs: list[str], card_status: dict[str, dict],
          ledger_text: str, caps: str, reply: str = "", llm: Callable[[str, str], str] | None = None) -> list[dict]:
    """Verdict per ask. ``llm(system, user) -> raw text``. Raises nothing the caller must handle except llm errors."""
    res = deterministic(asks, card_status)
    todo = [i for i in range(len(asks)) if i not in res]
    if todo and llm is not None:
        sub = [asks[i] for i in todo]
        card_text = "\n".join(f"{c['id']}: {c['status']}" + (f" (result: {c['result'][:150]})" if c.get("result") else "")
                              for c in card_status.values())
        raw = llm(SYSTEM, build_user_prompt(sub, tools_text, user_msgs, card_text, ledger_text, caps, reply))
        for j, v in enumerate(parse_verdicts(raw, len(sub))):
            res[todo[j]] = {**v, "i": todo[j]}
    return [res.get(i, {"i": i, "verdict": "ok", "reason": "", "how": ""}) for i in range(len(asks))]


def bad(verdicts: list[dict]) -> list[dict]:
    return [v for v in verdicts if v["verdict"] != "ok"]


_ADVICE = {
    "already_done": "It is already done",
    "already_answered": "David already answered this",
    "unverified": "Nothing this turn verified the premise",
    "agent_can_do": "You can do this yourself",
}


def nudge(asks: list[str], verdicts: list[dict]) -> str:
    lines = ["[ask-gate, private: David has NOT seen your draft.] Your draft asked David for something that fails "
             "the pre-send check:"]
    for v in bad(verdicts):
        lines.append(f"- \"{asks[v['i']][:160]}\" -> {v['verdict']}. {_ADVICE.get(v['verdict'], '')}: "
                     f"{v.get('reason', '')}" + (f" Do this: {v['how']}." if v.get("how") else ""))
    lines.append("Verify the premise with a tool, or do the work yourself, then write the reply again. Do not mention "
                 "this check or the earlier draft. Only keep an ask that is still true after you checked, and say "
                 "what you checked.")
    return "\n".join(lines)


def delivery_note(asks: list[str], verdicts: list[dict]) -> str:
    """Appended when the nudge budget is spent: tell David plainly which ask is unverified."""
    items = "; ".join(f"\"{asks[v['i']][:80]}\" ({v['verdict'].replace('_', ' ')}: {v.get('reason', '')[:120]})"
                      for v in bad(verdicts))
    return f"_ask-gate: I could not verify these asks, treat them as possibly stale: {items}_"


# ---- the real model call (Hermes auxiliary client; rotating Claude pool, no new keys) -----------------

def aux_llm(system: str, user: str, *, task: str = "ask_gate", timeout: float = 25.0, max_tokens: int = 700,
            provider: str | None = None, model: str | None = None) -> str:
    from agent.auxiliary_client import call_llm  # type: ignore
    kw: dict[str, Any] = {}
    if provider:
        kw["provider"] = provider
    if model:
        kw["model"] = model
    resp = call_llm(task=task, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    max_tokens=max_tokens, temperature=None, timeout=timeout, reasoning_config={"enabled": False}, **kw)
    msg = resp.choices[0].message
    return (getattr(msg, "content", None) or "").strip()


CLASSIFY_SYSTEM = """You read David's newest chat message and the asks the assistant had open. Decide whether his \
message ANSWERS or DISMISSES any of them (he did it, said no, said the assistant can do it itself, told it to stop \
asking, said it is already handled). Output ONLY JSON: {"answered":[{"i":<ask index>,"topic":"<5-12 word topic of the \
ask>","answer":"<what he said, <=15 words>"}]}. If he is merely starting a new topic, output {"answered":[]}. If he \
dismisses asking in general ("stop asking me things") with no specific ask, use i=-1 and topic from the last ask."""


def classify_answer(user_msg: str, open_asks: list[str], llm: Callable[[str, str], str] | None) -> list[dict]:
    if llm is None or not user_msg:
        return []
    asks_t = "\n".join(f"[{i}] {a}" for i, a in enumerate(open_asks)) or "(none recorded)"
    raw = llm(CLASSIFY_SYSTEM, f"OPEN ASKS:\n{asks_t}\n\nDAVID'S MESSAGE:\n{user_msg[:800]}")
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        return []
    out = []
    for it in data.get("answered", []) if isinstance(data, dict) else []:
        if isinstance(it, dict) and it.get("topic"):
            out.append({"i": it.get("i", -1), "topic": str(it["topic"])[:200], "answer": str(it.get("answer", ""))[:200]})
    return out
