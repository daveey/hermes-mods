"""self_heal: notice an agent/Hermes defect as early as possible: at the end of the turn that caused it, or on the
user's first message that reveals it.

Two entry paths, one schema:
- detect_complaint(prev_reply, user_text, tool_log=...) — pre_llm_call. Every real user message except pure acks
  goes to the aux judge `coach_defect` with the agent's previous reply and that turn's tool log: corrections, failed
  fixes, needless handoffs, frustration, Hermes/product misbehaviour, and nudges after a stalled turn.
- detect_admission(final_reply, tool_log=..., ask=...) — post_llm_call. Machine signals first, no judge (provider
  error reply, interrupted turn, one tool error repeated 3x); then, when the reply admits a mistake, hands the user a
  step, or the tool log shows errors, a turn-end audit by the same judge.
Both judges append the private user overlay (COACH_REPLY_PROFILE or <HERMES_HOME>/coach/reply_profile.md, shared
with reply_check) so they know the user's standing rules.

Both return {p_defect, kind, component, symptom, fingerprint, source} when p_defect >= threshold, else None.
The fingerprint is a stable hash of component + the symptom's content words, so one defect reported several ways
in one day is filed once (`Fingerprints.record`). Eval: eval/selfheal_incident_replay.py.

Mode: COACH_SELFHEAL=off|shadow|live (default shadow). shadow logs `selfheal_detect` and `selfheal_would_file` (the
full card it would file) to coach log.jsonl; live files the investigation card on the hub (see "Lane" below; the
README's "Self-heal lane" section). Fail-open everywhere.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Callable, Optional

COMPONENTS = ("agent", "desktop", "plugin", "cron", "kanban", "bot", "gateway", "provider", "browser", "bb", "other")

OOB_RE = re.compile(r"^\s*\[OUT-OF-BAND USER MESSAGE[^\]]*\]\s*(.*?)\s*\[/OUT-OF-BAND USER MESSAGE\]", re.S)
# Hermes-written user-role text: "[IMPORTANT: ...", "[System note: ...", kanban completion notices.
# Keep coordination notices out of the user-complaint lane. Their underlying failures
# belong to the machine-signal collector, not a second complaint filed by the recipient.
SYNTHETIC_RE = re.compile(
    r"^\s*(?:\[Message from another Hermes session(?:\s|\])|\[chat-closeout\]|"
    r"⏸\ufe0f?\s+\[[^\]\n]+\]\s+(?:@\S+\s+)?Kanban\s+t_[0-9a-f]+\b|"
    r"\[[A-Z][A-Za-z -]{2,40}(?::|\]| —)|(?i:\[coach\])|You've reached the maximum|[✔✓✗✖⚠] )")
# `[coach]` nudges are stored as user-role turns but are the coach's own text (agent-3ba1979f34): whether the nudge
# fixed the reply is judged by its reply_nudge_outcome (signals.collect_coach), never by the complaint backstop.
# Plugin-appended blocks that are not the user's words.
TRAILER_RE = re.compile(r"\n?\s*(?:\[user-steps\]|--- Attached Context ---|Session link\(s\) @session).*", re.S)

ADMIT_RE = re.compile(
    r"\b(my mistake|my (?:bad|fault|error)|that was (?:my|a) mistake|i (?:was wrong|missed|forgot|skipped|"
    r"overlooked|misread|didn'?t (?:check|load|verify|notice|read))|you'?re right[,:.]? (?:and|that|i|it)|i (?:should|"
    r"shouldn'?t) have|(?:a|the) bug in|is a bug|was a bug|bug (?:in|where)|work ?around|regress\w*|i told you "
    r"(?:otherwise|wrong)|i (?:said|reported|claimed) .{0,60} (?:but|when) (?:it|that) (?:wasn'?t|was not|isn'?t)|"
    r"(?:that|this) (?:was|is) (?:wrong|incorrect)|sorry)\b", re.I)

THRESHOLD = 0.6

# Pure acknowledgements: never a defect report, never judged.
ACK_RE = re.compile(
    r"^\s*(?:/\S+.*|go|yes|y|yep|yeah|ok(?:ay)?|k|do it|do both|done|thanks?|thank you|ty|sure|proceed|lgtm|"
    r"ship it|sounds good|perfect|great|nice|cool|approved?|go ahead|yes,? do it|👍)\W*$", re.I)

# Turn-end machine signals, computed from the reply and the coach's tool log (no judge). Same definitions as the
# replay's telemetry (api_err / interrupted / repeat): a provider error reply, an interrupted turn, or the same tool
# error three times in one turn.
API_ERR_RE = re.compile(r"not processed|API call failed|provider error|rate limit(?:ed)? .{0,40}(?:retry|exhaust)", re.I)
# Core's interrupt placeholders, matched at the START of the reply only (core writes them as the whole reply; a
# finished reply that quotes one mid-sentence is not an interruption). The tool log carries every command's arguments
# (a grep for "Turn stopped" is not an interruption) and errors of cancelled tools the agent recovered from, so it is
# read only for a turn that produced no reply and ended on a cancelled tool (gateway-a8b8d01aa4).
INTERRUPT_RE = re.compile(r"^[\W_]{0,8}(?:No reply:\s*)?(?:Operation interrupted|(?:Session )?turn lease lost|"
                          r"Turn stopped by|the turn stopped while a tool result was still pending)", re.I)
CANCEL_LINE_RE = re.compile(r"^\s*<-\s*ERROR\b.*(?:Tool execution cancelled|Operation interrupted|turn lease lost)",
                            re.I)
# Core's crash-recovery note (tui_gateway/session_history.py _AUTO_CONTINUE_NOTE_PREFIX): the next user turn after a
# backend stopped mid-turn. The only trace such a stop leaves, since the cut turn never reaches post_llm_call.
RESUME_NOTE_PREFIX = "[System note: Your previous turn was interrupted mid-run"
# Core's update-pause note (tui_gateway/update_pause.py pause_note) shares that prefix but marks a PLANNED pause: the
# turn stopped at a step boundary so a Hermes update could land, then continued by itself (gateway-9aaf151a15).
PAUSE_NOTE_MARK = "paused at a step boundary for a Hermes update"
# The update's land target: a pause that continued within this is working as designed; a longer one failed.
PAUSE_LAND_S = 30 * 60
ERR_LINE_RE = re.compile(r"(?:<-|->)\s*ERROR\b\s*(.*)")

COMPLAINT_SYS = """You watch a power user's chats with his AI agent system "Hermes" (the coding/ops agent, its Desktop
app, plugins, skills, cron jobs, Kanban task cards and workers, chat bots, gateways, model providers) and the products
Hermes builds and runs for him (e.g. a home assistant). You see the agent's previous final message, the tool log of
its previous turn, and the user's new message.

Score p_defect (0-1): the user's message reveals that the agent or Hermes did something it should not have, or failed
to do something it should have known to do, so a self-correcting system should root-cause it. Count:
- corrections of a wrong action, assumption, claim, interpretation, scope or choice (including "use X not Y", "you can
  do X via Y", "I said Z", "don't do that", "that's not what I asked"),
- reports that a claimed fix/build/deploy/change doesn't work, isn't visible, or regressed ("still broken", "nope",
  "I don't see it", "it's back"),
- objections to asks or handoffs the agent could have done itself ("you have ssh", "you buy it", "run it for me", "stop
  asking"), to re-asked questions, or to manual steps it gave him,
- frustration or "why did you / what went wrong / again / I told you",
- reports that Hermes itself (Desktop app, sidebar, panels, plugin, tool, cron, Kanban card state, session status,
  gateway, bot) or a product Hermes built and runs for him (his home assistant, its apps, pods, watch/phone clients,
  hub, feeds, Inbox) misbehaved, lost data, shows wrong state or did the wrong thing,
- requests to fix or root-cause the agent's or Hermes's own behavior,
- a bare nudge ("resume", "what's going on", "are you stuck", "status?") when the previous turn did not end in a real
  answer: it was interrupted, errored, stopped mid-work, went silent after many tool calls, or promised work that never
  came. A nudge after a complete answer is low. A previous message that merely looks cut off may have been clipped
  for length before you saw it; that alone is not a defect.
Do NOT count (low, <0.3): brand-new requests or feature asks with no failure; design taste or wording preferences the
agent could not have predicted; answers to the agent's legitimate questions; approvals; status questions with no sign
of a problem; problems in third-party services or the outside world that Hermes neither caused nor should have
prevented (kind "external"); bugs in research or demo code that ARE the task he just gave the agent.

kind: agent_fault (how the agent worked: claims, asks, verification, instruction-following, scope), hermes_bug
(Hermes or a product it built/runs misbehaved), external, none.
component: one of agent, desktop, plugin, cron, kanban, bot, gateway, provider, browser, bb (his home-assistant
product), other.
symptom: <=10 words naming what is wrong, neutral words, no names (e.g. "asked user to run terminal command").

Return ONLY JSON: {"p_defect": p, "kind": "...", "component": "...", "symptom": "..."}"""

# Second first-stage opinion: the narrower wording (only Hermes itself, product bugs reported as new issues score low).
# Averaging it with COMPLAINT_SYS cuts false alarms on product bug reports without losing the agent-fault catches.
_STRICT_TEMPLATE = """You watch a power user's chats with his AI agent system "Hermes" (the coding/ops agent, its Desktop
app, plugins, skills, cron jobs, Kanban task cards and workers, chat bots, gateways, model providers) and the products
Hermes builds and runs for him (e.g. a home assistant). You see the agent's previous final message, the tool log of
its previous turn, and the user's new message.

Score p_defect (0-1): the user's message reveals that the agent or Hermes did something it should not have, or failed
to do something it should have known to do, so a self-correcting system should root-cause it. Count:
- corrections of a wrong action, assumption, claim, interpretation, scope or choice (including "use X not Y", "you can
  do X via Y", "I said Z", "don't do that", "that's not what I asked"),
- reports that a claimed fix/build/deploy/change doesn't work, isn't visible, or regressed ("still broken", "nope",
  "I don't see it", "it's back"),
- objections to asks or handoffs the agent could have done itself ("you have ssh", "you buy it", "run it for me", "stop
  asking"), to re-asked questions, or to manual steps it gave him,
- frustration or "why did you / what went wrong / again / I told you",
- reports that Hermes itself (Desktop app, sidebar, panels, plugin, tool, cron, Kanban card state, session status,
  gateway, bot) misbehaved, lost data or shows wrong state,
- requests to fix or root-cause the agent's or Hermes's own behavior,
{strict_extra}- a bare nudge ("resume", "what's going on", "are you stuck", "status?") when the previous turn did not end in a real
  answer: it was interrupted, errored, stopped mid-work, went silent after many tool calls, or promised work that never
  came. A nudge after a complete answer is low.
Do NOT count (low, <0.3): brand-new requests or feature asks; design taste or wording preferences the agent could not
have predicted; answers to the agent's legitimate questions; approvals; status questions with no sign of a problem;
bugs in his products or the outside world that he reports as a NEW issue with no sign the agent's or Hermes's own work
caused them or should have prevented them (score those p_defect low and set kind "product_bug").{strict_product}

kind: agent_fault (how the agent worked: claims, asks, verification, instruction-following, scope), hermes_bug
(Hermes software/infra misbehaved), product_bug (see above), none.
component: one of agent, desktop, plugin, cron, kanban, bot, gateway, provider, browser, bb (his home-assistant
product), other.
symptom: <=10 words naming what is wrong, neutral words, no names (e.g. "asked user to run terminal command").

Return ONLY JSON: {"p_defect": p, "kind": "...", "component": "...", "symptom": "..."}"""

# Candidate 2 (ask-gate wk 10-12): "root cause it, and fix the pluggin" after a reply that described the account_usage
# plugin bug scored 0 on 3/3 strict runs (read as a new request); the Alpha rename that left the bot named Hermes was
# called product_bug although the agent's own rename caused it.
STRICT_FIX_REQUEST = """- a request to fix or root-cause a Hermes defect that the agent's previous message (or its reply to this
  message) describes, even when worded as a new task ("root cause it, and fix the plugin"),
- a problem in his product that the agent's own earlier work in this session caused or left half-done (e.g. a rename
  it made that missed a place),
"""
# Candidate A (t_eedc20dc): the strict stage labelled red-mark sessions and 'fix the pluggin' product_bug.
STRICT_NEVER_PRODUCT = """
Hermes itself (Desktop app, sidebar, panels, plugins, Kanban, gateway, bots, cron) is never a product_bug: a report
that it misbehaves is hermes_bug."""


def strict_sys(fix_request: bool = True, never_product: bool = True) -> str:
    return (_STRICT_TEMPLATE.replace("{strict_extra}", STRICT_FIX_REQUEST if fix_request else "")
            .replace("{strict_product}", STRICT_NEVER_PRODUCT if never_product else ""))


COMPLAINT_STRICT_SYS = strict_sys()

AUDIT_SYS = """You audit an AI agent's turn right as it ends, for a demanding power user who wants the agent to
self-serve, verify on his real path, and fix its own mistakes. You see his request, the turn's tool log (errors marked
ERROR) and the agent's final message. Judge only from that evidence.

Score p_defect (0-1): this turn shows a defect in the agent or its system that should be root-caused. High (0.8+):
- self-admission: the agent says it was wrong, made a mistake, missed or skipped something, told him something untrue,
  or found a bug in Hermes or its tools while doing something else (even if it fixed the symptom);
- unhandled_failure: the log shows errors, aborts, repeated failures or an empty/partial result that the final message
  hides, glosses over or leaves unresolved, or the message itself is an error / empty after many tool calls;
- needless_handoff: it asks him to do or decide something it could do itself (shell, ssh, sudo via a user-terminal
  tool, browser, credential stores), re-asks something answered, or hands him an infeasible step;
- contradiction: the message contradicts the tool log or itself (claims a check that never ran).
Low (<0.3): honest status reports that state plainly what is unverified or failed and what happens next; a bug that IS
the task he gave (root cause + fix report); apologies with no defect; external outages it surfaces clearly; legitimate
questions about his preferences.

component: agent, desktop, plugin, cron, kanban, bot, gateway, provider, browser, bb, other.
symptom: <=10 words naming what is wrong, neutral words, no names.

Return ONLY JSON: {"p_defect": p, "kind": "admission|unhandled_failure|needless_handoff|contradiction|none",
"component": "...", "symptom": "..."}"""


def clean_user(text) -> str:
    if isinstance(text, list):
        parts = []
        for p in text:
            if isinstance(p, dict):
                if p.get("type") in ("image_url", "image", "input_image"):
                    parts.append("[screenshot]")
                elif p.get("text"):
                    parts.append(str(p["text"]))
        text = " ".join(parts)
    t = str(text or "")
    m = OOB_RE.match(t)
    if m:
        t = m.group(1)
    t = TRAILER_RE.sub("", t)
    return t.strip()


HANDOFF_RE = re.compile(
    r"\b(?:you(?:'ll| will)? (?:need|have) to|run (?:this|these|the following)|paste|in your terminal|can you|could you|"
    r"please (?:run|open|tap|click|log ?in|sign ?in|approve|check|confirm|enable|install)|let me know|once you|"
    r"reply with|do you want|should i|want me to|on your (?:phone|mac|iphone|end)|your call|needs? you|"
    r"for you to|waiting on you|manual step)\b", re.I)


def prefilter(user_text) -> bool:
    """Worth judging: any real user message except pure acknowledgements, slash commands and Hermes-written text.
    (The v1 complaint regex missed corrections like "you buy the credits" and nudges like "whats going on"; one cheap
    judge call per real user turn is the price of recall.)"""
    t = clean_user(user_text)[:2000]
    if not t or SYNTHETIC_RE.match(t):
        return False
    return not ACK_RE.match(t[:200])


def _err_lines(tool_log) -> list:
    return [ln for ln in (tool_log or []) if isinstance(ln, str) and ERR_LINE_RE.search(ln)]


def admission_prefilter(reply: str, tool_log=None) -> bool:
    r = (reply or "")[:6000]
    return bool(ADMIT_RE.search(r) or HANDOFF_RE.search(r) or _err_lines(tool_log)
                or (not r.strip() and tool_log))


def machine_signal(final_reply: str, tool_log=None) -> Optional[dict]:
    """Turn-end telemetry, no judge: a provider-error reply, an interrupted turn, or one tool error repeated 3x."""
    r = (final_reply or "")[:400]
    log = [str(x) for x in (tool_log or [])]
    if API_ERR_RE.search(r):
        v = {"p_defect": 0.9, "kind": "api_error", "component": "provider", "symptom": "turn failed with provider error"}
    elif INTERRUPT_RE.search(r) or (not r.strip() and log and CANCEL_LINE_RE.search(log[-1])):
        v = {"p_defect": 0.9, "kind": "interrupted", "component": "gateway",
             "symptom": "turn interrupted before finishing"}
    else:
        rep = repeated_tool_error(log)
        if not rep:
            return None
        # The id is the tool plus the normalized error (agent-6eb3a51e94: one fixed label lumped every repeated error
        # into one defect, and the card only saw the reply tail).
        v = {"p_defect": 0.85, "kind": "repeat_failure", "component": "agent",
             "symptom": f"repeated {rep['tool']} error: {rep['error'][:60]}".rstrip(),
             "tool": rep["tool"], "error": rep["error"], "evidence": rep["evidence"], "details": rep["details"]}
    return {**v, "fingerprint": fingerprint(v["component"], v["symptom"]), "source": "signal"}


_TOOL_CALL_RE = re.compile(r"^\s*->\s*([\w.:-]+)\(")
_ERR_NORM = [
    (re.compile(r"\bt_[0-9a-f]{6,}\b", re.I), " "),  # card ids
    (re.compile(r"\b\d{8}_\d{6}_[0-9a-z]+\b", re.I), " "),  # session ids
    (re.compile(r"(?<![\w/])(?:~|\.{1,2})?/[\w.@+~-]+(?:/[\w.@+~-]*)*"), " "),  # paths (not "I/O")
    (re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{7,}\b", re.I), " "),  # hex shas/ids
    (re.compile(r"\d+"), " "),
    (re.compile(r"\(\s*\)|\[\s*\]"), " "),
    (re.compile(r"[\\\"'`]+\s*[\\\"'`]*"), " "),  # quotes left around a stripped path
    (re.compile(r"\s+"), " "),
]


_JSON_FIELD_RE = re.compile(r'"(error|message|stderr|output)"\s*:\s*"((?:[^"\\]|\\.)*)')


def unwrap_tool_error(err: str) -> str:
    """The error text inside a tool's JSON result (`{"output": "Error: ...", "exit_code": 1}`, often truncated by the
    turn log so json.loads fails): the first non-empty error/message/stderr/output string, else the text itself."""
    t = str(err or "").strip()
    if not t.startswith("{"):
        return t
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            for k in ("error", "message", "stderr", "output"):
                if isinstance(obj.get(k), str) and obj[k].strip():
                    return obj[k].strip()
    except ValueError:
        pass
    found = {k: v for k, v in reversed(_JSON_FIELD_RE.findall(t)) if v.strip()}
    for k in ("error", "message", "stderr", "output"):
        if k in found:
            return found[k].replace('\\"', '"').replace("\\n", " ").strip()
    return t


def normalize_error(err: str) -> str:
    """Error text with the parts that vary between occurrences of one error (ids, paths, hex, numbers) removed."""
    t = unwrap_tool_error(err)
    for rx, sub in _ERR_NORM:
        t = rx.sub(sub, t)
    return t.strip(" :,;.-")


def repeated_tool_error(log) -> Optional[dict]:
    """The first (tool, normalized error) pair seen 3+ times in one turn's tool log, with the call and error lines.
    -> {tool, error, count, call, line, evidence, details} or None."""
    seen: dict = {}
    tool, call = "tool", ""
    for ln in log or []:
        ln = str(ln)
        m = _TOOL_CALL_RE.match(ln)
        if m:
            tool, call = m.group(1), ln.strip()
            continue
        e = ERR_LINE_RE.search(ln)
        if not e:
            continue
        norm = normalize_error(e.group(1)[:200])
        if not norm:
            continue
        rec = seen.setdefault((tool, norm), {"tool": tool, "error": norm, "count": 0, "calls": []})
        rec["count"] += 1
        rec["calls"].append((call, e.group(1).strip()))
        rec["call"], rec["line"] = call, e.group(1).strip()
    rep = next((r for r in seen.values() if r["count"] >= 3), None)
    if not rep:
        return None
    rep["evidence"] = (f"{rep['tool']} failed {rep['count']}x with the same error: {rep['line'][:160]} "
                       f"(last call: {rep['call'][:120]})")
    rep["details"] = "\n".join(f"{c[:200]}\n   <- ERROR {x[:200]}" for c, x in rep.pop("calls")[-4:])
    return rep


# Words too generic to tie a card to a signal hit, and tools too generic to count on their own.
_RELEVANCE_STOP = set("error errors failed failure refused cannot could unable found exit code tool repeated turn "
                      "with before after while finishing stopped open card file line none null true false call "
                      "result".split())
_GENERIC_TOOLS = {"tool", "terminal", "execute_code", "read_file", "write_file", "patch", "search_files", "tool_call",
                  "delegate_task", "browser_exec", "process_manage"}


def signal_terms(d: dict) -> set:
    """Words a card must mention to own this machine-signal defect: the tool (unless generic) and the error's
    content words, else the symptom's content words."""
    text = d.get("error") if d.get("kind") == "repeat_failure" else d.get("symptom")
    terms = {w for w in re.findall(r"[a-z][a-z0-9_]+", str(text or "").lower())
             if len(w) >= 4 and w not in _RELEVANCE_STOP and w not in _STOP}
    if d.get("tool") and str(d["tool"]).lower() not in _GENERIC_TOOLS:
        terms.add(str(d["tool"]).lower())
    return terms


def card_relevant(card: dict, terms: set) -> bool:
    text = " ".join(str(card.get(k) or "") for k in ("title", "body")).lower()
    return bool(text) and any(t in text for t in terms)


def is_resume_note(user_text) -> bool:
    """Core's resume note (crash recovery or update pause): a machine-written turn, never a user complaint."""
    return clean_user(user_text).lstrip().startswith(RESUME_NOTE_PREFIX)


def resume_signal(user_text, gap_s: Optional[float] = None) -> Optional[dict]:
    """A user turn that is core's crash-recovery note: the backend stopped mid-turn and Hermes resumed the chat.
    `gap_s` = seconds since the session's last recorded activity (None when unknown)."""
    t = clean_user(user_text)
    if not is_resume_note(t):
        return None
    if PAUSE_NOTE_MARK in t[:400]:
        if gap_s is None or gap_s <= PAUSE_LAND_S:
            return None  # a planned update pause that continued in time
        v = {"p_defect": 0.85, "kind": "interrupted", "component": "gateway",
             "symptom": "update pause did not continue in time"}
    else:
        v = {"p_defect": 0.9, "kind": "interrupted", "component": "gateway",
             "symptom": "turn interrupted (backend stopped)"}
    return {**v, "fingerprint": fingerprint(v["component"], v["symptom"]), "source": "signal",
            "gap_s": None if gap_s is None else int(gap_s)}


_STOP = set("a an the to of in on for and or is are was were be been it its this that with not no by at as from "
            "user users his her he she they them i you we me my your our does do did doesnt didnt isnt wasnt "
            "after before when while again still".split())


def _stem(w: str) -> str:
    s = re.sub(r"(?:ing|ed|s)$", "", w) if len(w) > 4 else w
    return s.rstrip("e") or w


def fingerprint(component: str, symptom: str) -> str:
    """Stable id for one defect: component + sorted content words of the symptom (stemmed crudely), so rewordings
    like "sidebar sessions reshuffle" / "session sidebar reshuffles" collide."""
    words = re.findall(r"[a-z0-9]+", (symptom or "").lower())
    stems = sorted({_stem(w) for w in words if w not in _STOP and len(w) > 1})
    comp = component if component in COMPONENTS else "other"
    key = comp + ":" + " ".join(stems)
    return comp + "-" + hashlib.sha1(key.encode()).hexdigest()[:10]


def _json_objects(raw: str):
    """Every top-level JSON object in `raw`, in order (a flash answer can carry two, or wrap one in a fence)."""
    dec, i, s = json.JSONDecoder(), 0, raw or ""
    while True:
        i = s.find("{", i)
        if i < 0:
            return
        try:
            obj, end = dec.raw_decode(s, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict):
            yield obj
        i = end


def parse(raw: str) -> Optional[dict]:
    """The first JSON object in the answer that carries `p_defect`. An answer in another judge's schema (e.g. the
    reply-check keys) is no opinion (None), never a 0 vote: counting it as 0 sank real catches to the cascade mean."""
    d = next((o for o in _json_objects(raw) if "p_defect" in o), None)
    if d is None:
        return None
    try:
        comp = str(d.get("component") or "other").strip().lower()
        return {"p_defect": max(0.0, min(1.0, float(d.get("p_defect") or 0))),
                "component": comp if comp in COMPONENTS else "other",
                "symptom": str(d.get("symptom") or "").strip()[:120],
                "kind": str(d.get("kind") or "").strip().lower()[:30]}
    except Exception:
        return None


def profile_text() -> str:
    """The private user-environment overlay shared with reply_check (COACH_REPLY_PROFILE or
    <HERMES_HOME>/coach/reply_profile.md): his standing rules and what his agents can reach, so the judge can tell a
    lazy handoff or a rule break from a legitimate one. '' when absent."""
    p = os.environ.get("COACH_REPLY_PROFILE")
    if not p:
        try:
            from hermes_constants import get_hermes_home  # type: ignore
            home = Path(get_hermes_home())
        except Exception:
            home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
        p = str(home / "coach" / "reply_profile.md")
    try:
        return strip_output_spec(Path(p).read_text())[:6000]
    except Exception:
        return ""


_OUTPUT_SPEC = re.compile(r"(?im)^[ \t]*(?:return|respond|reply|answer|output)\b[^\n]*\bjson\b[^\n]*(?:\n|$)")


def strip_output_spec(text: str) -> str:
    """The overlay holds rules and environment only. An output-format line in it ("Return ONLY JSON {...}" left over
    from another judge) is appended after the judge's own schema and wins: the self-heal judge answered in the
    reply-check keys and its stages parsed as 0."""
    return _OUTPUT_SPEC.sub("", text or "")


def _with_profile(system: str) -> str:
    prof = profile_text()
    return system + (f"\n\nUSER'S STANDING RULES AND ENVIRONMENT (breaking these is a defect):\n{prof}"
                     if prof.strip() else "")


def _log_text(tool_log, n=25, chars=2500) -> str:
    return "\n".join(str(x)[:200] for x in (tool_log or [])[-n:])[:chars] or "(no tool calls)"


def complaint_prompt(prev_reply: str, user_text, tool_log=None, context: str = "") -> str:
    """`context` (digest backstop only): the session title and earlier user messages, for turns whose previous agent
    reply is unknown. Without it a context-free report (\"why didnt this work [screenshot]\") reads as a new issue."""
    ctx = f"SESSION CONTEXT (from a digest):\n{context[:2200]}\n\n" if context else ""
    prev = prev_reply or ("(not available)" if context else "(none: first turn)")
    return (f"{ctx}AGENT'S PREVIOUS FINAL MESSAGE:\n{prev[-3000:]}\n\n"
            f"TOOL LOG OF THE AGENT'S PREVIOUS TURN:\n{_log_text(tool_log)}\n\n"
            f"USER'S NEW MESSAGE:\n{clean_user(user_text)[:2000]}")


def admission_prompt(final_reply: str, tool_log=None, ask="") -> str:
    return (f"USER'S REQUEST:\n{clean_user(ask)[:1500] or '(unknown)'}\n\n"
            f"TOOL LOG THIS TURN:\n{_log_text(tool_log, 30, 3500)}\n\n"
            f"AGENT'S FINAL MESSAGE:\n{(final_reply or '(empty)')[-4000:]}")


def _verdict(v: Optional[dict], source: str, threshold: float) -> Optional[dict]:
    """A hit is an agent/Hermes defect at or above threshold; outside-world problems (kind external) are not."""
    if not v or v["p_defect"] < threshold or v.get("kind") in ("external", "product_bug", "none"):
        return None
    return {**v, "fingerprint": fingerprint(v["component"], v["symptom"]), "source": source}


GATE = 0.6            # first-stage score that escalates to the second opinion and the confirm model
AUDIT_THRESHOLD = 0.9  # turn-end audit admissions file only at this (confirmed) score


def _score(v: Optional[dict]) -> float:
    return 0.0 if not v or v.get("kind") in ("external", "product_bug", "none") else v["p_defect"]


RESAMPLE_LO = 0.3     # a stage-1 score in [RESAMPLE_LO, GATE) gets one more sample (candidate 1)
FIX_RESAMPLE = True   # so does any stage-1 miss whose message asks to fix / root-cause something (candidate B)
FIX_RE = re.compile(r"\b(?:fix(?:ed)?|root[- ]?caus\w*|figure (?:it )?(?:ou?t|ut) (?:why|what)|investigate|debug|"
                    r"broken|why (?:is|are|did|didn'?t|does|doesn'?t|isn'?t|aren'?t))\b", re.I)


def resample_stage1(s1: float, user_text) -> bool:
    if RESAMPLE_LO is not None and RESAMPLE_LO <= s1 < GATE:
        return True
    return bool(FIX_RESAMPLE and s1 < GATE and FIX_RE.search(clean_user(user_text)))


def _ask(llm: Callable[[str, str], str], system: str, prompt: str, tries: int = 2) -> Optional[dict]:
    """One judge stage, retried once when the call fails or the answer does not parse (an empty flash answer is
    common). None = no opinion, which the cascade leaves out; it is never a 'no' vote."""
    for _ in range(tries):
        try:
            v = parse(llm(system, prompt))
        except Exception:
            v = None
        if v:
            return v
    return None


def complaint_verdict(prev_reply: str, user_text, llm: Callable[[str, str], str], tool_log=None,
                      confirm: Optional[Callable[[str, str], str]] = None, context: str = "") -> Optional[dict]:
    """The complaint cascade, before the threshold: judge (COMPLAINT_SYS); below GATE stop there. Otherwise a second
    flash opinion (COMPLAINT_STRICT_SYS) and, when configured, the confirm model on COMPLAINT_SYS; p_defect = the mean
    of the stage scores (a non-defect kind scores 0; a stage with no answer after a retry is left out, since counting
    it as 0 halved real catches). Component/symptom come from the strongest stage."""
    prompt = complaint_prompt(prev_reply, user_text, tool_log, context)
    system = _with_profile(COMPLAINT_SYS)
    v1 = _ask(llm, system, prompt)
    if not v1:
        return None
    s1 = _score(v1)
    if s1 < GATE and resample_stage1(s1, user_text):
        # Flash at temp 0 is not deterministic: "why are all the dots red? [screenshot]" scored 0.7, 0.6 and 0.05 on
        # three identical calls. One more sample on a borderline score or an explicit fix request; the max counts.
        v1b = _ask(llm, system, prompt)
        if v1b and _score(v1b) > s1:
            v1, s1 = v1b, _score(v1b)
    if s1 < GATE:
        return {**v1, "p_defect": s1, "stages": [s1]}
    v2 = _ask(llm, _with_profile(COMPLAINT_STRICT_SYS), prompt)
    stages, best = [s1] + ([_score(v2)] if v2 else []), v1
    if confirm:
        v3 = _ask(confirm, _with_profile(COMPLAINT_SYS), prompt)
        if v3:
            stages.append(_score(v3))
        best = v3 if v3 and _score(v3) > 0 else best
    p = round(sum(stages) / len(stages), 3)
    return {**best, "kind": best.get("kind") if _score(best) > 0 else v1.get("kind"), "p_defect": p,
            "stages": stages}


def detect_complaint(prev_reply: str, user_text, llm: Optional[Callable[[str, str], str]] = None,
                     threshold: float = THRESHOLD, tool_log=None,
                     confirm: Optional[Callable[[str, str], str]] = None, context: str = "",
                     gap_s: Optional[float] = None) -> Optional[dict]:
    """pre_llm_call path: core's resume note (backend stopped mid-turn) is a machine signal; otherwise prefilter,
    then the cascade (complaint_verdict) on (previous reply + its turn's tool log, user message). Returns
    {p_defect, kind, component, symptom, fingerprint, source, stages} or None. Fail-open."""
    try:
        if is_resume_note(user_text):
            return resume_signal(user_text, gap_s)  # machine-written: never sent to the complaint judge
        if not prefilter(user_text):
            return None
        return _verdict(complaint_verdict(prev_reply, user_text, llm or aux_judge, tool_log, confirm, context),
                        "complaint", threshold)
    except Exception:
        return None


def audit_verdict(final_reply: str, llm: Callable[[str, str], str], tool_log=None, ask="",
                  confirm: Optional[Callable[[str, str], str]] = None) -> Optional[dict]:
    """The turn-end audit, before the threshold: judge; an admission at >= GATE goes to the confirm model (when
    configured), whose verdict replaces it. Only AUDIT_KINDS count; other kinds score 0."""
    system, prompt = _with_profile(AUDIT_SYS), admission_prompt(final_reply, tool_log, ask)
    v = parse(llm(system, prompt))
    if v and confirm and v.get("kind") in AUDIT_KINDS and v["p_defect"] >= GATE:
        v = parse(confirm(system, prompt))
    if not v:
        return None
    return v if v.get("kind") in AUDIT_KINDS else {**v, "p_defect": 0.0}


def detect_admission(final_reply: str, llm: Optional[Callable[[str, str], str]] = None,
                     threshold: float = AUDIT_THRESHOLD, tool_log=None, ask="",
                     confirm: Optional[Callable[[str, str], str]] = None) -> Optional[dict]:
    """post_llm_call path: machine signals first (no judge), then a turn-end audit when the reply admits a mistake or
    hands the user a step, or the turn's tool log shows errors. Only self-admissions count from the audit: the other
    audit kinds (unhandled failure, handoff, contradiction) cost more false alarms than they catch on the replay set.
    Fail-open."""
    try:
        if not isinstance(final_reply, str):
            return None
        sig = machine_signal(final_reply, tool_log)
        if sig:
            return sig
        if not admission_prefilter(final_reply, tool_log):
            return None
        return _verdict(audit_verdict(final_reply, llm or aux_judge, tool_log, ask, confirm), "admission", threshold)
    except Exception:
        return None


AUDIT_KINDS = ("admission",)


def confirm_judge() -> Optional[Callable[[str, str], str]]:
    """The optional second-stage judge: aux task `coach_defect_confirm` when configured
    (auxiliary.coach_defect_confirm.{provider,model}) or COACH_DEFECT_CONFIRM_MODEL is set; else None."""
    model = os.environ.get("COACH_DEFECT_CONFIRM_MODEL", "")
    provider = os.environ.get("COACH_DEFECT_CONFIRM_PROVIDER", "")
    if not model:
        try:
            from hermes_cli.config import load_config  # type: ignore
            c = (load_config().get("auxiliary") or {}).get("coach_defect_confirm") or {}
            model, provider = c.get("model") or "", c.get("provider") or ""
        except Exception:
            return None
    if not model:
        return None

    def judge(system: str, prompt: str) -> str:
        from agent.auxiliary_client import call_llm  # type: ignore
        resp = call_llm(task="coach_defect_confirm", provider=provider or None, model=model,
                        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                        max_tokens=400, temperature=0, timeout=60)
        return (getattr(resp.choices[0].message, "content", None) or "").strip()
    return judge


def user_facing(platform: str = "", parent_session_id: str = "") -> bool:
    """Only the user's own chats: not cron runs, subagents (their 'user' is the parent agent) or Kanban workers
    (their first message is a card body written by an agent)."""
    if (platform or "").lower() in ("cron", "kanban", "webhook", "batch"):
        return False
    if parent_session_id or os.environ.get("HERMES_KANBAN_TASK"):
        return False
    return True


def mode() -> str:
    """COACH_SELFHEAL env, else `mode` in <coach>/selfheal-config.json (so a cron scheduler or gateway needs no env
    change), else shadow."""
    m = os.environ.get("COACH_SELFHEAL", "").strip().lower()
    if not m:
        try:
            m = str(json.loads((coach_root() / "selfheal-config.json").read_text()).get("mode") or "").lower()
        except Exception:
            m = ""
    return m if m in ("off", "shadow", "live") else "shadow"


def threshold() -> float:
    try:
        return float(os.environ.get("COACH_SELFHEAL_THRESHOLD", THRESHOLD))
    except ValueError:
        return THRESHOLD


def audit_threshold() -> float:
    try:
        return float(os.environ.get("COACH_SELFHEAL_AUDIT_THRESHOLD", AUDIT_THRESHOLD))
    except ValueError:
        return AUDIT_THRESHOLD


def aux_judge(system: str, prompt: str) -> str:
    """Aux task `coach_defect` (auxiliary.coach_defect.{provider,model})."""
    from agent.auxiliary_client import call_llm  # type: ignore
    kw = {}
    if os.environ.get("COACH_DEFECT_MODEL"):
        kw = {"provider": os.environ.get("COACH_DEFECT_PROVIDER") or None, "model": os.environ["COACH_DEFECT_MODEL"]}
    resp = call_llm(task="coach_defect", messages=[{"role": "system", "content": system},
                                                  {"role": "user", "content": prompt}],
                    max_tokens=1500, temperature=0, timeout=30, **kw)
    return (getattr(resp.choices[0].message, "content", None) or "").strip()


class Fingerprints:
    """<coach>/selfheal.json: fingerprint -> {first, last, count, sessions, day_count}. `seen` = already recorded
    today (UTC), so one defect becomes one filing per day however many times it is reported."""

    def __init__(self, root: Path, clock: Callable[[], float] = time.time):
        self.path = Path(root) / "selfheal.json"
        self.clock = clock

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except Exception:
            return {}

    def record(self, hit: dict, session_id: str = "") -> bool:
        """Record a hit; True when it is the first time this fingerprint is seen today."""
        d = self._read()
        now = self.clock()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        fp = hit["fingerprint"]
        e = d.get(fp) or {"first": now, "count": 0, "sessions": [], "component": hit.get("component"),
                          "symptom": hit.get("symptom")}
        new = e.get("day") != day
        e.update(last=now, day=day, count=e["count"] + 1)
        if session_id and session_id not in e["sessions"]:
            e["sessions"] = (e["sessions"] + [session_id])[-20:]
        d[fp] = e
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1))
        os.replace(tmp, self.path)
        return new


def handle(hit: Optional[dict], session_id: str, store, evidence: str = "", m: Optional[str] = None,
           filer: Optional[Callable] = None, platform: str = "") -> Optional[dict]:
    """Log a detection and hand a new fingerprint (first time today) to the lane: `file_defect` logs
    `selfheal_would_file` in shadow mode and files the investigation card on the hub in live mode."""
    if not hit:
        return None
    m = m or mode()
    # A machine signal carries what failed; lead with it here, not only in the caller, because a long-running gateway
    # re-execs this module on change but keeps the coach __init__ it booted with (agent-6eb3a51e94 live check).
    hit_ev = str(hit.get("evidence") or "")
    if hit_ev and not str(evidence or "").startswith(hit_ev[:60]):
        evidence = f"{hit_ev[:300]} | reply: {str(evidence or '')[-180:]}"
    new = Fingerprints(store.root).record(hit, session_id)
    rec = {"event": "selfheal_detect", "mode": m, "session_id": session_id, "source": hit["source"],
           "p": hit["p_defect"], "component": hit["component"], "symptom": hit["symptom"],
           "fingerprint": hit["fingerprint"], "new": new, "evidence": (evidence or "")[:300]}
    store.log(rec)
    if m == "live" and new:
        q = Path(store.root) / "selfheal_queue.jsonl"
        with open(q, "a") as fh:
            fh.write(json.dumps({**rec, "ts": time.time()}) + "\n")
    if new and m in ("shadow", "live"):
        d = {**hit, "session_id": session_id, "quote": (evidence or "")[:500], "host": host_name(),
             "platform": platform, "at": time.time(), "kind": hit.get("kind") or hit["source"]}
        try:
            (filer or file_defect)(d, store=store, m=m)
        except Exception as exc:  # fail-open: a filing problem never breaks a turn
            store.log({"event": "selfheal_file_error", "fingerprint": hit["fingerprint"], "error": str(exc)[:300]})
    return rec


# ════════════════════════════════════════════════════════════════════════════════════════════════════════════
# Lane: defect -> investigation card (hub) -> proposal file (What happened / Why / Plan) -> auto-approve -> fix card
#       -> result stored quietly (selfheal-results/<fp>.md, no Inbox card). auto_approve false restores the Inbox proposal + Do it gate.
#
#   file_defect(d)    any entry path (complaint, admission, machine signal, digest backstop, monitors). On the hub it
#                     decides locally; elsewhere (live) it pipes `d` to `self_heal.py file` on the hub over ssh, so
#                     dedupe, recurrence, suppression and budget are decided in one place. Shadow logs
#                     `selfheal_would_file` with the full card args and creates nothing.
#   investigation     card `selfheal:<fp>` for generation 1, `selfheal:<fp>:r<gen>` after (one monotonic counter per
#                     fingerprint over refiles AND recurrences; kanban idempotency returns done cards too), model
#                     claude-opus-5-5, 1 h. Investigates LIVE (kanban.db, logs, processes, git log), writes the
#                     five-whys chain and proposal.md, records `event proposed` (posts nothing to the Inbox; the
#                     inbox plugin's post() refuses self-heal posts while auto_approve is on).
#   auto-approve      sync files the fix card for every `proposed` fingerprint (manual mode: the Inbox outcome on key
#                     selfheal:<fp> decides). Old cards still telling the worker to `hermes inbox post` get a one-time
#                     "ignore that step" comment (sweep_inbox_steps).
#   Do it             -> file_fix (hub cron sync, or `self_heal.py fix <fp>`): fix card `selfheal:<fp>[:r<gen>]:fix` (red-then-green regression test, ship,
#                     verify live, `event fixed --result result.md` stores the proof; nothing is posted to the Inbox).
#   sync (cron)       reads Inbox outcomes every 5 min: Do it (a resolve) files the fix card, Not now snoozes the
#                     fingerprint 14 days, Never suppresses it. It also reconciles against the board: a `filed`/`approved`
#                     fingerprint whose card is done/archived/gone with no proposed/fixed event becomes `stale`
#                     (selfheal_stale), so the next detection refiles it under a new generation.
#   backstop (cron)   runs the complaint detector over new user turns in the coach digests (chats where the
#                     in-process hook did not fire).
#   budget            engine_budget lane `selfheal`: $200/UTC day (its own cap, outside the $20 engine total), <= 3
#                     running cards, <= 1 filing per fingerprint per UTC day. Known cap denials never create a card;
#                     investigations persist for admission retry, and fixes keep the Do-it outcome retryable.
# Events (coach log.jsonl, read by coach_weekly's self-heal section): selfheal_would_file, selfheal_filed,
# selfheal_recurrence, selfheal_recurrence_held (an occurrence inside `deploy_grace_hours` after fixed: hosts may
# still run old code; the 3rd held occurrence files anyway), selfheal_skip, selfheal_proposed, selfheal_approved,
# selfheal_fixed, selfheal_reverted, selfheal_not_defect, selfheal_declined.
# ════════════════════════════════════════════════════════════════════════════════════════════════════════════

MODEL = "claude-opus-5-5"
PROVIDER = "anthropic"  # pinned with MODEL: a host whose default provider is another (hub: openai-codex) refuses the bare model (model_provider_mismatch)
INVESTIGATION_RUNTIME = 3600
FIX_RUNTIME = 7200
INVESTIGATION_BUDGET = 8
FIX_BUDGET = 15
USD_DAY = 200.0
MAX_RUNNING = 3
SNOOZE_DAYS = 14
NOT_DEFECT_DAYS = 7
SKILLS = ("five-whys", "hermes-mods-repo")
BB_SKILLS = ("five-whys", "bb-self-fix")
FIX_SKILLS = ("five-whys", "hermes-mods-repo", "systematic-debugging")
CREATED_BY = "self-heal"
INBOX_TYPE = "selfheal"
RESULT_TYPE = "selfheal-result"  # legacy Inbox card type; results are no longer posted (see RESULTS_DIR)
RESULTS_DIR = "selfheal-results"  # <coach>/selfheal-results/<fp>.md: finished-work proof, never an Inbox card
SELF = "~/code/hermes-mods/plugins/coach/self_heal.py"  # how cards and Inbox actions call this file on the hub
# The event commands briefs print pin HERMES_HOME: a card's terminal keeps env between calls, and a repro that ran
# `export HERMES_HOME=$(mktemp -d)` sent `event proposed` to a scratch lane that reported success (plugin-129c2f2518).
SELF_EVENT = f'HERMES_HOME="$HOME/.hermes" python3 {SELF} event'
FP_RE = re.compile(r"^[a-z]+-[0-9a-f]{8,}$")


def host_name() -> str:
    return os.uname().nodename.split(".")[0]


HANDOFF_GRACE_S = 15 * 60  # same as signals.HANDOFF_GRACE_S


def handoff_nudge_counts(root: Optional[Path] = None, now: Optional[float] = None, days: int = 7) -> dict:
    """Handoff reply_nudges in <coach>/log.jsonl over `days`, by what their reply_nudge_outcome said: resolved (the
    revised reply no longer hands off: not filed), still_handed_off and no_outcome (past the grace window: both
    filed by the signals collector), waiting (inside the grace window). For `status`: a judge that never resolves
    anything, or resolves everything, shows here."""
    root = Path(root) if root is not None else coach_root()
    now = time.time() if now is None else now
    since = now - days * 86400
    nudges, outcomes = [], []
    try:
        with (root / "log.jsonl").open(encoding="utf-8", errors="replace") as f:
            for ln in f:
                if '"reply_nudge' not in ln or '"handoff"' not in ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("rule") != "handoff" or float(r.get("ts") or 0) < since:
                    continue
                (nudges if r.get("event") == "reply_nudge" else
                 outcomes if r.get("event") == "reply_nudge_outcome" else []).append(r)
    except OSError:
        pass
    c = {"days": days, "nudges": len(nudges), "resolved": 0, "still_handed_off": 0, "no_outcome": 0, "waiting": 0}
    for n in nudges:
        t = float(n.get("ts") or 0)
        o = next((o for o in outcomes if o.get("session_id") == n.get("session_id")
                  and not (n.get("draft_id") and o.get("draft_id") and n["draft_id"] != o["draft_id"])
                  and -5 <= float(o.get("ts") or 0) - t <= HANDOFF_GRACE_S), None)
        if o is not None:
            outcomes.remove(o)
            c["resolved" if o.get("resolved") is True else "still_handed_off"] += 1
        else:
            c["waiting" if now - t <= HANDOFF_GRACE_S else "no_outcome"] += 1
    c["filed"] = c["still_handed_off"] + c["no_outcome"]
    return c


# reply_check rules whose nudges get a reply_nudge_outcome (same as signals.OUTCOME_RULES).
OUTCOME_RULES = ("handoff", "confirm", "unverified", "unwatched", "report_only", "status_only", "denied_ask",
                 "credit_ask", "laptop_job")


def nudge_counts(root: Optional[Path] = None, now: Optional[float] = None, days: int = 7) -> dict:
    """reply_nudges per rule over `days`, by their reply_nudge_outcome (agent-3ba1979f34): resolved (not filed),
    still_failing / no_outcome (filed by signals.collect_coach), waiting (inside the grace window), untracked (a
    non-handoff nudge from a backend on the old plugin, no `outcome` marker: never filed). For `status`: a rule whose
    nudges never get outcomes, or never resolve, shows here."""
    root = Path(root) if root is not None else coach_root()
    now = time.time() if now is None else now
    since = now - days * 86400
    nudges, outcomes = [], []
    try:
        with (root / "log.jsonl").open(encoding="utf-8", errors="replace") as f:
            for ln in f:
                if '"reply_nudge' not in ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("rule") not in OUTCOME_RULES or float(r.get("ts") or 0) < since:
                    continue
                (nudges if r.get("event") == "reply_nudge" else
                 outcomes if r.get("event") == "reply_nudge_outcome" else []).append(r)
    except OSError:
        pass
    out: dict = {}
    for n in nudges:
        rule = n["rule"]
        c = out.setdefault(rule, {"nudges": 0, "resolved": 0, "still_failing": 0, "no_outcome": 0, "waiting": 0,
                                  "untracked": 0, "filed": 0})
        c["nudges"] += 1
        t = float(n.get("ts") or 0)
        o = next((o for o in outcomes if o.get("session_id") == n.get("session_id") and o.get("rule") == rule
                  and not (n.get("draft_id") and o.get("draft_id") and n["draft_id"] != o["draft_id"])
                  and -5 <= float(o.get("ts") or 0) - t <= HANDOFF_GRACE_S), None)
        if o is not None:
            outcomes.remove(o)
            k = "resolved" if o.get("resolved") is True else "still_failing"
        elif rule != "handoff" and not n.get("outcome"):
            k = "untracked"
        else:
            k = "waiting" if now - t <= HANDOFF_GRACE_S else "no_outcome"
        c[k] += 1
        c["filed"] = c["still_failing"] + c["no_outcome"]
    return out


def coach_root() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home()) / "coach"
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / "coach"


def lane_config() -> dict:
    """<coach>/selfheal-config.json (private, per host): {"hub_host": "<hostname of the hub>", "hub_ssh":
    "<user@addr>", "board": "<hub board>", "bb_board": "bb", "deploy_grace_hours": 6}. Env overrides:
    COACH_SELFHEAL_HUB (ssh target), COACH_SELFHEAL_IS_HUB=1. With no hub configured every host files locally.
    `deploy_grace_hours` (hub side): occurrences this soon after `fixed` are held, not filed as came-back."""
    try:
        cfg = json.loads((coach_root() / "selfheal-config.json").read_text())
    except Exception:
        cfg = {}
    if os.environ.get("COACH_SELFHEAL_HUB"):
        cfg["hub_ssh"] = os.environ["COACH_SELFHEAL_HUB"]
    return cfg


def hub_ssh_argv(cfg: dict) -> list:
    """ssh argv prefix to the hub. Pins the key (`hub_ssh_key` in the config, default ~/.ssh/id_ed25519 when it
    exists) with IdentitiesOnly: a launchd job's default identities differ from the shell's (no agent, other keys
    offered first), and the hub then refuses it preauth while the same host's interactive ssh works."""
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    kp = Path(os.path.expanduser(cfg.get("hub_ssh_key") or "~/.ssh/id_ed25519"))
    if cfg.get("hub_ssh_key") or kp.exists():
        argv += ["-i", str(kp), "-o", "IdentitiesOnly=yes"]
    return argv


def is_hub(cfg: Optional[dict] = None) -> bool:
    cfg = lane_config() if cfg is None else cfg
    if os.environ.get("COACH_SELFHEAL_IS_HUB") in ("1", "true"):
        return True
    if not cfg.get("hub_ssh"):
        return True  # no hub configured: file locally
    return bool(cfg.get("hub_host")) and host_name() == cfg["hub_host"]


def route(d: dict, cfg: Optional[dict] = None) -> dict:
    """Board + skills. Baby Bear product defects go to the hub bb board (cfg ``bb_board``, default ``bb``) with
    bb-self-fix and assignee ``default``; everything else to the hub's default board (Hermes / harness). A missing
    named board is not created here: live filing defers the payload instead of dropping it."""
    cfg = lane_config() if cfg is None else cfg
    if d.get("component") == "bb":
        return {"board": cfg.get("bb_board") or "bb", "skills": list(BB_SKILLS), "bb": True}
    return {"board": cfg.get("board") or None, "skills": list(SKILLS), "bb": False}


_BOARD_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,63}$")
_MISSING_BOARD_RE = re.compile(r"board\s+['\"]?[\w.-]+['\"]?\s+does not exist", re.I)
MISSING_BOARD_RETRY_S = 900  # cron is every 5 min; don't hammer a board that is not there yet


def _kanban_root() -> Path:
    """Same root Hermes uses for boards: ``HERMES_KANBAN_HOME``, else the default Hermes root."""
    override = os.environ.get("HERMES_KANBAN_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from hermes_constants import get_default_hermes_root  # type: ignore
        return Path(get_default_hermes_root())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def board_present(board: Optional[str], home: Optional[Path] = None) -> bool:
    """Whether ``hermes kanban --board <slug> create`` has a board to land on.

    None and ``default`` always exist (DB at ``<root>/kanban.db``). A named board exists only when
    ``<root>/kanban/boards/<slug>/board.json`` is present and not archived — Hermes's identity marker.
    ``home`` overrides the root (tests)."""
    if not board or board == "default":
        return True
    if not _BOARD_SLUG_RE.match(board):
        return False
    root = Path(home) if home is not None else _kanban_root()
    try:
        data = json.loads((root / "kanban" / "boards" / board / "board.json").read_text())
    except Exception:
        return False
    return not data.get("archived")


def _board_missing_output(err: str, out: str) -> bool:
    return bool(_MISSING_BOARD_RE.search(f"{err or ''}\n{out or ''}"))


def _pt(ts: float) -> str:
    import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.fromtimestamp(ts or 0, ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts or 0))


# Where to look, per component. The replay of 202 incidents showed a transcript-only investigator almost never fixes
# surface provenance (C10), Kanban state/routing (C11) or process lifecycle (C12): those need the live systems.
CHECKLIST = {
    "kanban": ("C11 Kanban state machine / routing",
               "- `sqlite3 -readonly ~/.hermes/kanban.db` (and `~/.hermes/kanban/boards/*/kanban.db`): the card's "
               "`tasks` row (status, assignee, board, idempotency_key, created_by), `task_events` in order "
               "(kind + payload: claimed/blocked/scheduled/unblocked/reclaimed/timed_out and their reasons), "
               "`task_runs` (outcome, error, worker pid, session_id).\n"
               "- Compare what the user was told (quote) with the event log at that minute: which writer moved the "
               "state, and which session/board it routed to.\n"
               "- Cross-session leaks: is the card/notice the chat talked about OWNED by this chat (tasks.session_id, "
               "created_by) or did it arrive via a subscription (kanban_notify_subs) or a board-wide query? "
               "Duplicate cards (same title, no idempotency_key)? A worker that parked David-facing steps in a "
               "comment or a timer instead of a needs_input block?\n"
               "- `git log -S'<event kind or state>' -- hermes_cli/kanban*` in ~/.hermes/hermes-agent for the code "
               "that writes it; the dispatcher log `~/.hermes/logs/kanban*.log` / `agent.log` at that time."),
    "desktop": ("C10 surface provenance / duplicated state",
                "- Which surface showed it (sidebar row, Inbox card, toast, panel, status chip) and which writer "
                "produced it: grep the exact string in ~/.hermes/hermes-agent/apps/desktop and "
                "~/code/hermes-mods/desktop-plugins; `git log --since` on that file.\n"
                "- The backing state: the REST endpoint or db row the surface renders (state.db sessions row, inbox "
                "items, kanban task) at the reported time; two writers or a stale cache = provenance gap.\n"
                "- Do not stop at the renderer: the wrong row/badge/card is usually right for the data it got. Follow "
                "the field it keys on (source, owner, status, origin/test flag) back to the code path that WROTE it "
                "(e.g. a create path that hardcodes `source`, test traffic with no origin tag) and cite that file:line.\n"
                "- On a Mac, the Desktop log (`~/Library/Logs/Hermes/` / `~/.hermes/logs/desktop.log`) via the "
                "mac diag command if configured; else the coach digest of the session."),
    "plugin": ("C10/C12 plugin behaviour",
               "- `git -C ~/code/hermes-mods log --since=7.days -- plugins/<name> desktop-plugins/<name>`: what "
               "changed just before the report.\n"
               "- `grep -n '<plugin>' ~/.hermes/logs/errors.log` around the time; hook exceptions; the plugin's "
               "own log/state under ~/.hermes/<plugin>/ or ~/.hermes/cache/<plugin>/.\n"
               "- Reproduce by calling the plugin function directly under the Hermes interpreter."),
    "gateway": ("C12 process lifecycle / environment isolation",
                "- `ps -eo pid,lstart,command | grep -i hermes`, `systemctl --user status` / `launchctl print "
                "gui/$UID/<label>`: what was running, who started it, restarts (`journalctl --user -u ...`).\n"
                "- `~/.hermes/logs/{gateway,agent,errors}.log` and `hermes-sync.log` (a `hermes update` at that "
                "minute stops servers) in a +-10 min window.\n"
                "- Environment: HERMES_HOME, profile, interpreter/venv of each process (`/proc/<pid>/environ` or "
                "`ps -E`). Guards keyed on inherited env vars (HERMES_DELEGATED_CHILD_CONTEXT, HERMES_SESSION_*, "
                "HERMES_KANBAN_*) misfire when a launcher (update/relaunch/sync, execute_code's env scrub) passes or "
                "drops the parent env: grep the guard, then every spawn path that sets or strips the var.\n"
                "- Who ended or orphaned it: session reapers / LRU eviction and agent close tearing down owned "
                "background processes; checkpoints (processes.json) written before the notify/watch flags, so a "
                "restart re-adopts the job without them. A wrong status chip is the symptom; the lifecycle writer is "
                "the root."),
    "cron": ("C12/C15 scheduled jobs",
             "- `hermes cron list` and `~/.hermes/cron/output/<job>/` for the failing run; its script under "
             "~/.hermes/scripts; the delivery target; `errors.log` at run time."),
    "agent": ("agent behaviour (C03-C09)",
              "- The session transcript: `sqlite3 -readonly ~/.hermes/state.db \"select role, substr(content,1,800) "
              "from messages where session_id='<sid>' order by id\"` on the host that ran it, else the coach digest "
              "`~/code/hermes-coach-data/<host>/<profile>/<sid>.json`.\n"
              "- What instruction pointed the agent there: the skill(s) loaded, ~/AGENTS.md rules, tool descriptions; "
              "`git log` of the skill. The fix is usually a skill/tool/AGENTS.md change plus a check that fires at "
              "the failure point (a coach rule or a tool-result hint), not more prose."),
}
CHECKLIST["bot"] = CHECKLIST["gateway"]
CHECKLIST["provider"] = ("provider / model calls",
                         "- `~/.hermes/logs/agent.log` + `errors.log` at the time: provider, status code, retries, "
                         "fallback chain; `hermes config get model` and the fallback plugin's state.")
CHECKLIST["browser"] = CHECKLIST["plugin"]


def investigation_brief(d: dict, previous: Optional[dict] = None, cfg: Optional[dict] = None) -> str:
    fp = d["fingerprint"]
    comp = d.get("component") or "other"
    when = _pt(d.get("at") or time.time())
    sid = d.get("session_id") or ""
    title, checks = CHECKLIST.get(comp, CHECKLIST["agent"])
    extra = "" if comp == "agent" else "\n" + CHECKLIST["agent"][1]
    rec = ""
    if previous:
        rec = (f"\n## Recurrence\nThis fingerprint was fixed before by card {previous.get('fix_card') or '?'} "
               f"({_pt(previous.get('fixed_at') or 0)}). The previous fix {previous.get('fix_card') or ''} didn't "
               "hold. Say so in the proposal's first line, find why the earlier fix and its test didn't catch the "
               "return (five-whys step 5), and the Plan MUST add a detection-level guard (test, assertion, alert or "
               "gate) that fails if it comes back.\n")
        held = [h for h in (previous.get("held") or []) if h.get("at")]
        if held:
            rec += ("\nHeld inside the rollout window (seen soon after the fix was pushed, so possibly a host still "
                    "on old code; filed now because it kept happening or came after the window):\n" +
                    "".join(f"- {_pt(h['at'])} on {h.get('host') or '?'} ({h.get('source') or '?'})\n" for h in held))
    proposal = f"~/.hermes/coach/selfheal/{fp}/proposal.md"
    return f"""Self-heal investigation for defect `{fp}`. Nothing here needs a human; David's standing approval of this
lane covers the investigation AND the fix. You do NOT ship the fix yourself: you write the proposal, and the lane's
next pass auto-approves it and starts the fix card. Post NOTHING to the Inbox (David only wants finished fixes there).
Budget for this card <= ${INVESTIGATION_BUDGET}.

## Evidence (from the detector)
- Symptom: {d.get('symptom') or '?'}
- Component: {comp} · detector: {d.get('source') or '?'} ({d.get('kind') or '?'}, p={d.get('p_defect', '?')})
- When: {when} · host: {d.get('host') or '?'} · session: @session:{d.get('profile') or 'default'}/{sid}
- Quote: "{(d.get('quote') or '').strip()[:500]}"
{('- Extra: ' + str(d.get('details'))[:1500]) if d.get('details') else ''}
{rec}
## Do this
1. Reproduce or confirm it LIVE, not from the transcript alone. Scope it from the user's own words and the session's
   open work (its cards, background processes, earlier turns), not only the detector's symptom: the trigger can point
   at a different event than the one that hurt him. Checklist for {title}:
{checks}{extra}
   Other hosts: the coach digests in ~/code/hermes-coach-data/<host>/ (git pull first) carry every session's user
   turns, tool counts and last reply. A host's state.db / kanban.db / logs are only readable ON that host: if the
   incident ran elsewhere, use what reaches it (ssh diag, digests, git) and list in the proposal what you could not
   read.
2. Before writing the chain, name 2-3 candidate roots and run the one check that tells them apart. The root is the
   writer of the wrong state (code path, env, config, skill rule), not the surface that displayed it or the turn the
   detector fired on. Then write the five-whys chain from evidence (five-whys skill): one causal step per why, each
   with a file:line, log line, db row or quote; label guesses `(hypothesis)`; branch "why it happened" and "why
   nothing caught it"; stop at a process/guard/design gap Hermes controls. Post the chain as a comment on this card.
3. A real defect already owned by another card is a duplicate, NOT not_defect. Record
   `{SELF_EVENT} duplicate {fp} --owner <owner-card> --note "<evidence>"`,
   then complete this card as a duplicate. Post nothing to the Inbox; the owner retains the real defect.
   Not a real defect (expected behaviour, user's own setting, outside world with nothing to guard)? Only after you
   checked the user's complaint itself, not just the detector's trigger. Run
   `{SELF_EVENT} not_defect {fp} --note "<why>"` and complete this card with a
   result that starts with "not a defect:". Post nothing to the Inbox.
4. Otherwise write the proposal to {proposal} (markdown, <= 3500 chars, plain words, no jargon) with exactly these
   sections:
   **What happened**: the symptom, when (PT), the `@session:<profile>/<id>` link, his quote.
   **Why**: the five-whys chain, one line per why, each with its evidence.
   **Plan**: the exact changes (files/skills/config), the regression test (fails before, passes after), the
   detection guard, and what gets verified live after shipping.
   **Cost / risk**, then **Undo**: how to revert. The first line is a one-line title: what is broken.
   Do NOT post it to the Inbox. Record it: `{SELF_EVENT} proposed {fp}`
   (the lane auto-approves it within 5 minutes and files the fix card from this proposal).
5. Complete this card with the proposal's first line as the summary. Do not start the fix.
"""


def fix_brief(fp: str, st: dict) -> str:
    proposal = coach_root() / "selfheal" / fp / "proposal.md"
    try:
        text = proposal.read_text()[:6000]
    except Exception:
        text = f"(proposal file {proposal} missing: read the investigation card {st.get('card') or '?'} comments)"
    bb = st.get("component") == "bb"
    ship = ("Ship it through the bb-self-fix procedure (PR, merge, deploy) under David's standing approval."
            if bb else
            "Ship it under David's standing merge/deploy approval: hermes-mods commit + push (pre-commit hook, never "
            "--no-verify), core changes through a hermes-core-wt worktree + ship, settings through shared-config.json "
            "(hermes-mods-repo skill).")
    return f"""Self-heal FIX for defect `{fp}`. The proposal below is approved (David's standing self-heal approval;
the lane auto-approves every proposal). Post nothing to the Inbox: the result is recorded quietly (step 6).
Investigation card: {st.get('card') or '?'}. Budget for this card <= ${FIX_BUDGET}.

## Approved proposal
{text}

## Acceptance (all of it, in order)
1. Re-check the defect still reproduces on the current code (it may have been fixed meanwhile: then say so, run
   `{SELF_EVENT} not_defect {fp} --note "already fixed by <sha>"` and stop).
2. Write the regression test FIRST and show it FAILING on the current code (paste the failing output in a card
   comment).
3. Make the fix from the plan (root level, not just the symptom) and the detection guard. Show the same test
   PASSING, plus the module's existing tests.
4. {ship}
5. Verify live on the real path (the surface/command/session type where it happened), and capture the proof.
6. Record the result QUIETLY. The Inbox is for decisions only: do NOT run `hermes inbox post` for a finished fix
   (David archived those unread and the inbox muted the poster). Write `result.md` (what changed with commits,
   the red/green test output, the live proof, and the undo command) and run
   `{SELF_EVENT} fixed {fp} --note "<commit sha>" --result result.md`.
   That stores the result under <coach>/selfheal-results/{fp}.md and logs selfheal_fixed; the card's
   kanban_complete summary carries it too. Nothing reaches the Inbox.
7. Complete the card. If something irreversible or external blocks the fix, kanban_block with the exact ask instead.
"""


class LaneState:
    """<coach>/selfheal_lane.json: fingerprint -> {status, card, fix_card, component, symptom, filed_day, snooze_until,
    suppressed, fixed_at, recurrences, refiles, gen, cards, history, held}. `held` = occurrences seen inside the
    rollout window after fixed_at ([{at, host, source, seen}]), cleared by the next filing or fix.
    Statuses: filed, proposed, approved, fixed,
    not_defect, declined, suppressed, reverted, stale."""

    def __init__(self, root: Path):
        self.path = Path(root) / "selfheal_lane.json"

    def read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except Exception:
            return {}

    def get(self, fp: str) -> dict:
        return dict(self.read().get(fp) or {})

    def update(self, fp: str, **kw) -> dict:
        d = self.read()
        e = d.get(fp) or {}
        hist = e.get("history") or []
        if "status" in kw and kw["status"] != e.get("status"):
            hist = (hist + [[round(time.time()), kw["status"]]])[-30:]
        e.update(kw, history=hist)
        d[fp] = e
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1))
        os.replace(tmp, self.path)
        return e


def _utc_day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _next_utc_midnight(ts: float) -> str:
    return time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime((int(ts) // 86400 + 1) * 86400))


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _budget_mod():
    """engine_budget from the hermes-coach skill (same repo), or None."""
    import importlib.util
    import sys
    p = Path(__file__).resolve().parents[2] / "skills" / "productivity" / "hermes-coach" / "scripts" / "engine_budget.py"
    if not p.exists():
        return None
    name = "_selfheal_engine_budget"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod = sys.modules.setdefault(name, mod)
    return mod


@contextlib.contextmanager
def _admission_lock(store, live: bool = True, timeout: float = 180.0):
    """One lock across every admission path (lane cron, signals cron, Do-it sync, CLI): budget read + card
    create is a single transaction, so two concurrent admissions cannot both see the same headroom. A lock
    that cannot be taken in `timeout` seconds fails closed (RuntimeError) instead of admitting unserialized."""
    if not live:
        yield
        return
    import fcntl
    Path(store.root).mkdir(parents=True, exist_ok=True)
    with (Path(store.root) / "selfheal-admission.lock").open("a") as lock:
        end = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= end:
                    raise RuntimeError("self-heal admission lock busy")
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def budget(board: Optional[str] = None, now: Optional[float] = None,
           new_card_usd: float = INVESTIGATION_BUDGET) -> dict:
    """{"usd", "cap", "running", "ok_usd", "ok_running", "why"} for the selfheal lane today (UTC).
    ok_usd reserves in-flight work: every running/ready/todo selfheal card's unspent card budget plus
    `new_card_usd` for the card being admitted must still fit under the cap (spend alone is a lagging
    measure; the Oct 28 overshoot was $268 against $200)."""
    now = now or time.time()
    out = {"usd": None, "cap": USD_DAY, "running": None, "ok_usd": False,
           "ok_running": False, "available": False, "why": "", "reserved": None}
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    b = board or os.environ.get("COACH_KANBAN_BOARD") or "default"
    try:
        eb = _budget_mod()
        if eb is None:
            raise RuntimeError("accounting module unavailable")
        snap = eb.snapshot(home=home, board=b, now=now)
        usd = snap["lanes"]["selfheal"]
        cap = snap["caps"]["selfheal"]
        import math
        if (not isinstance(usd, (int, float)) or not isinstance(cap, (int, float))
                or not math.isfinite(usd) or not math.isfinite(cap) or usd < 0 or cap <= 0):
            raise ValueError("invalid self-heal spend or cap")
        inflight = [c for c in eb.engine_cards(home, b, ("running", "ready", "todo")) if c["lane"] == "selfheal"]
        running = sum(1 for c in inflight if c.get("status", "running") == "running")
        reserved = float(new_card_usd)
        for c in inflight:
            cb = eb.card_budget(c, snap) if hasattr(eb, "card_budget") else float(INVESTIGATION_BUDGET)
            spent = eb.card_cost(home, b, c["id"]) if hasattr(eb, "card_cost") else 0.0
            reserved += max(0.0, float(cb) - float(spent))
        ok, why = eb.check(snap, "selfheal", reserved)
        if not ok and usd < cap:
            why = f"{why} (spend ${usd:.2f} + ${reserved:.2f} reserved for in-flight and the new card)"
        # Commit the observation only after ALL reads succeed. Unknown is not zero spend.
        out.update(usd=usd, cap=cap, running=running, ok_usd=ok, reserved=round(reserved, 2),
                   ok_running=running < MAX_RUNNING, available=True, why=why)
    except Exception as exc:
        out["why"] = f"budget read failed: {exc}"
    return out


def _budget_unavailable(b: dict, fp: str, store) -> Optional[dict]:
    """Refuse creation, not create-then-schedule, when accounting cannot be read."""
    if b.get("available", True):  # legacy injected budget functions retain their contract
        return None
    why = b.get("why") or "budget unavailable"
    store.log({"event": "selfheal_budget_unavailable", "fingerprint": fp, "why": why})
    return {"action": "error", "why": why}


def _admission_denied(b: dict, now: float) -> Optional[dict]:
    """A known denial must never create a dispatchable card, even briefly."""
    if not b["ok_usd"]:
        return {"action": "deferred", "retry_at": _next_utc_midnight(now),
                "why": b.get("why") or "self-heal daily cap reached"}
    if not b["ok_running"]:
        return {"action": "deferred", "retry_at": _iso(now + 3600),
                "why": "self-heal concurrency limit reached"}
    return None


def _pending_db(store):
    import sqlite3
    store.root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(Path(store.root) / "selfheal_pending.sqlite", timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS pending (fp TEXT PRIMARY KEY, payload TEXT NOT NULL, "
               "retry REAL NOT NULL, version TEXT NOT NULL)")
    return db


def _defer_investigation(d: dict, result: dict, store):
    """Persist before acknowledging a denial; all producers may advance their cursors afterward."""
    import datetime
    import uuid
    retry = datetime.datetime.fromisoformat(result["retry_at"].replace("Z", "+00:00")).timestamp()
    db = _pending_db(store)
    try:
        with db:
            db.execute("INSERT INTO pending VALUES (?, ?, ?, ?) ON CONFLICT(fp) DO UPDATE SET "
                       "payload=excluded.payload, retry=excluded.retry, version=excluded.version",
                       (d["fingerprint"], json.dumps(d), retry, uuid.uuid4().hex))
    finally:
        db.close()
    store.log({"event": "selfheal_deferred", "fingerprint": d["fingerprint"], **result})
    return result


def _defer_missing_board(d: dict, board: str, store, now: float) -> dict:
    """Hold the defect for retry. A missing board must not be a silent drop: the payload stays in
    ``selfheal_pending.sqlite`` and the cron refiles it. ``stop_pass`` is false so this does not halt
    other pending admissions (it is not a cap denial)."""
    result = {"action": "deferred", "why": f"kanban board {board!r} does not exist",
              "retry_at": _iso(now + MISSING_BOARD_RETRY_S), "board": board, "stop_pass": False}
    return _defer_investigation(d, result, store)


PENDING_MAX_AGE_S = 7 * 86400  # detections waiting longer than this are dropped, not filed late
_SOURCE_RANK = {"complaint": 0, "admission": 0, "signal": 1}  # in-chat judges, then machine signals (9/10 real in
# the Oct 2026 shadow sample), then the digest backstop (3/6) and anything else


def _detected_at(d: dict) -> Optional[float]:
    at = d.get("at")
    if isinstance(at, (int, float)):
        return float(at)
    if isinstance(at, str) and at:
        import datetime
        try:
            return datetime.datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _pending_priority(d: dict) -> tuple:
    """Drain order: complaints/admissions, then machine signals, then the digest backstop; within a class the
    likelier defect, then the newest. Input (~75 new fingerprints a day in the Oct 2026 shadow log) far exceeds the
    $200/day lane, so this order decides which defects get investigated at all."""
    try:
        p = float(d.get("p_defect") or 0)
    except (TypeError, ValueError):
        p = 0.0
    return (_SOURCE_RANK.get(d.get("source") or "", 2), -p, -(_detected_at(d) or 0))


CLOSED_CARD_STATUSES = ("archived", "done")
_KANBAN_DETAILS_RE = re.compile(r"\bboard (\S+) cards? ((?:t_[0-9a-zA-Z]+(?:,\s*)?)+)")
_BOARD_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def kanban_trigger(d: dict) -> Optional[tuple]:
    """(board, [card ids]) a kanban signal is about: the structured ``kanban`` key (signals.to_defect), else the
    ``board <b> card <id>`` details line that older pending payloads carry. None for other defects."""
    if d.get("kind") != "kanban" and d.get("component") != "kanban":
        return None
    k = d.get("kanban") or {}
    if k.get("board") and k.get("cards"):
        return str(k["board"]), [str(c) for c in k["cards"]]
    m = _KANBAN_DETAILS_RE.search(d.get("details") or "")
    if not m:
        return None
    return m.group(1), [c.strip() for c in m.group(2).split(",") if c.strip()]


def _kanban_board_path(board: str, home: Path) -> Optional[Path]:
    """signals.board_dbs labels: ``<board>`` on the default home, ``<profile>/<board>`` on a profile."""
    prof, _, name = board.rpartition("/")
    if not _BOARD_PART_RE.match(name) or (prof and not _BOARD_PART_RE.match(prof)):
        return None
    h = home / "profiles" / prof if prof else home
    return h / "kanban.db" if name == "default" else h / "kanban" / "boards" / name / "kanban.db"


HOLD_STALE_S = 6 * 3600  # a systemic hold filed later than this after it fired, with every held card closed, is history


def _cards_closed(board: str, cards: list, home: Optional[Path]) -> Optional[list]:
    """["<id> now <status>", ...] when every card is archived/done on the local board; None when any is open or the
    board/card cannot be read."""
    path = _kanban_board_path(board, Path(home) if home else _kanban_home())
    if not path or not path.is_file():
        return None
    import sqlite3
    try:
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            db.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        except sqlite3.Error:
            db = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5)
            db.execute("PRAGMA query_only=ON")
        try:
            got = []
            for c in cards:
                row = db.execute("SELECT status FROM tasks WHERE id=?", (c,)).fetchone()
                if not row or row[0] not in CLOSED_CARD_STATUSES:
                    return None
                got.append(f"{c} now {row[0]}")
        finally:
            db.close()
    except sqlite3.Error:
        return None
    return got


def kanban_trigger_closed(d: dict, home: Optional[Path] = None, now: Optional[float] = None) -> Optional[str]:
    """Why to drop a kanban signal: every card it names is now archived/done on its (local) board, e.g. the owner
    closed it while the detection waited in the pending queue. A systemic hold (``kanban_hold``, signals.py
    _systemic_holds) is dropped only when it is also older than HOLD_STALE_S: a fresh hold is filed even when its
    held cards rerun and finish, since the crash cause may still be live (kanban-754a3e3cf1 filed a 2-day-old hold
    whose cards were archived). None (= file as before) when the signal is not a kanban one, came from another
    host's board, or the board/card cannot be read."""
    if d.get("host") and d["host"] != host_name():
        return None
    hold = d.get("kanban_hold")
    if isinstance(hold, dict) and hold.get("board") and hold.get("cards"):
        at = _detected_at(d)
        now = time.time() if now is None else now
        if at is None or now - at <= HOLD_STALE_S:
            return None
        got = _cards_closed(str(hold["board"]), [str(c) for c in hold["cards"]], home)
        if not got:
            return None
        return (f"stale systemic hold: cards {', '.join(got)} closed (hold {_iso(at)}, "
                f"> {HOLD_STALE_S // 3600}h before filing)")
    trig = kanban_trigger(d)
    if not trig:
        return None
    board, cards = trig
    got = _cards_closed(board, cards, home)
    return "trigger card " + ", ".join(got) if got else None


def retry_deferred(store=None, now: Optional[float] = None, filer=None) -> list:
    """Recheck due investigations through normal admission, never automatically release a card.

    Highest priority first (`_pending_priority`); the pass stops at the first renewed denial, since every later row
    would hit the same cap. Rows whose detection is older than PENDING_MAX_AGE_S are dropped (`selfheal_skip`,
    why `stale pending`), which bounds the queue when input outruns the budget."""
    store = _store(store)
    now = now or time.time()
    if not (Path(store.root) / "selfheal_pending.sqlite").exists():
        return []
    db = _pending_db(store)
    out = []
    try:
        rows = db.execute("SELECT fp, payload, version FROM pending WHERE retry <= ?", (now,)).fetchall()
        due = []
        for fp, payload, version in rows:
            d = json.loads(payload)
            at = _detected_at(d)
            if at is not None and now - at > PENDING_MAX_AGE_S:
                with db:
                    db.execute("DELETE FROM pending WHERE fp=? AND version=?", (fp, version))
                store.log({"event": "selfheal_skip", "mode": "live", "fingerprint": fp,
                           "why": f"stale pending (detected {_iso(at)}, > {PENDING_MAX_AGE_S // 86400}d)"})
                out.append({"action": "skip", "why": "stale pending"})
                continue
            closed = kanban_trigger_closed(d, now=now)
            if closed:  # the owner closed the card while this waited: nothing is broken any more
                with db:
                    db.execute("DELETE FROM pending WHERE fp=? AND version=?", (fp, version))
                store.log({"event": "selfheal_skip", "mode": "live", "fingerprint": fp, "why": closed})
                out.append({"action": "skip", "why": closed})
                continue
            due.append((_pending_priority(d), fp, d, version))
        due.sort(key=lambda r: r[0])
        for _, fp, d, version in due:
            try:
                result = (filer or file_defect)(d, store=store, m="live", now=now)
            except Exception as exc:
                result = {"action": "error", "why": str(exc)[:300]}
            out.append(result)
            if result.get("action") in ("filed", "skip"):
                with db:
                    # Do not erase a newer detection written while this retry was executing.
                    db.execute("DELETE FROM pending WHERE fp=? AND version=?", (fp, version))
            elif result.get("action") == "deferred" and result.get("stop_pass", True):
                break  # a cap denial denies the rest; a missing board (stop_pass false) does not
    finally:
        db.close()
    return out

def generation(st: dict) -> int:
    """Generations used so far for a fingerprint (0 = never filed). Lane files written before `gen` existed derive it
    from the old counters; a collision with an earlier card is caught in file_defect anyway."""
    if st.get("gen") is not None:
        return int(st["gen"])
    if not st.get("card"):
        return 0
    return 1 + int(st.get("recurrences") or 0) + int(st.get("refiles") or 0)


def inv_key(fp: str, gen: int) -> str:
    return f"selfheal:{fp}" if gen <= 1 else f"selfheal:{fp}:r{gen}"


def fix_key(fp: str, gen: int) -> str:
    return f"selfheal:{fp}:fix" if gen <= 1 else f"selfheal:{fp}:r{gen}:fix"


def known_cards(st: dict) -> set:
    return {c for c in (st.get("cards") or []) + [st.get("card"), st.get("fix_card")] if c}


DEPLOY_GRACE_HOURS = 6.0  # "fixed" is stamped at push; hosts run old code until they update/restart (core updates
# defer up to 3 h while cards run). An occurrence inside this window after fixed_at is held, not a recurrence.
HELD_FILE_AFTER = 3  # ...unless the fingerprint is held this many times: a fix that really failed surfaces fast


def deploy_grace_s(cfg: Optional[dict] = None) -> float:
    """`deploy_grace_hours` in selfheal-config.json (0 = every occurrence after fixed is a recurrence)."""
    cfg = lane_config() if cfg is None else cfg
    try:
        return max(0.0, float(cfg.get("deploy_grace_hours", DEPLOY_GRACE_HOURS))) * 3600
    except (TypeError, ValueError):
        return DEPLOY_GRACE_HOURS * 3600


def decide(d: dict, st: dict, now: float, cfg: Optional[dict] = None) -> dict:
    """Pure dedupe / suppression / recurrence decision for one defect against its lane state.
    -> {"action": "file"|"skip"|"hold", "why", "key", "previous"}"""
    fp = d["fingerprint"]
    status = st.get("status")
    if st.get("suppressed"):
        return {"action": "skip", "why": "suppressed (Never)"}
    if (st.get("snooze_until") or 0) > now:
        return {"action": "skip", "why": f"snoozed until {_iso(st['snooze_until'])} (Not now)"}
    if st.get("filed_day") == _utc_day(now) and status != "fixed":  # a post-fix recurrence is not a duplicate
        return {"action": "skip", "why": "already filed today (<= 1 per fingerprint per UTC day)"}
    if status in ("filed", "proposed", "approved"):
        return {"action": "skip", "why": f"open: {status} {st.get('fix_card') or st.get('card') or ''}".strip()}
    if status == "not_defect" and now - (st.get("closed_at") or 0) < NOT_DEFECT_DAYS * 86400:
        return {"action": "skip", "why": f"closed as not a defect within {NOT_DEFECT_DAYS} days"}
    g = generation(st) + 1  # every filing gets a fresh generation, so its keys never name an earlier card
    if status == "fixed":
        # Judge by when it happened, not when it was processed: inside the rollout window it is most likely a
        # host still running pre-fix code. Held occurrences are deduped by time so a replay never counts twice.
        at = _detected_at(d)
        at = now if at is None else at
        grace = deploy_grace_s(cfg)
        held = [h for h in (st.get("held") or []) if h.get("at") != at]
        if grace and st.get("fixed_at") and at - float(st["fixed_at"]) < grace and len(held) + 1 < HELD_FILE_AFTER:
            return {"action": "hold", "at": at, "held": held, "until": float(st["fixed_at"]) + grace,
                    "why": f"inside the {grace / 3600:g} h rollout window after fixed ({_iso(st['fixed_at'])}); "
                           f"held {len(held) + 1}/{HELD_FILE_AFTER - 1}"}
        why = "recurrence" if not held else f"recurrence ({len(held)} held inside the rollout window)"
        return {"action": "file", "why": why, "key": inv_key(fp, g), "gen": g, "previous": st}
    if st.get("card"):  # an earlier card closed without a fix (declined/reverted/not_defect/stale): new key
        return {"action": "file", "why": "refile", "key": inv_key(fp, g), "gen": g, "previous": None}
    return {"action": "file", "why": "new", "key": inv_key(fp, g), "gen": g, "previous": None}


def card_args(d: dict, key: str, previous: Optional[dict] = None, cfg: Optional[dict] = None) -> dict:
    r = route(d, cfg)
    sym = re.sub(r"\s+", " ", d.get("symptom") or "defect").strip()[:90]
    return {"title": f"self-heal: {sym}" + (" (came back)" if previous else ""),
            "body": investigation_brief(d, previous, cfg), "idempotency_key": key, "model": MODEL, "provider": PROVIDER,
            "max_runtime": INVESTIGATION_RUNTIME, "skills": r["skills"], "board": r["board"],
            "assignee": "default", "created_by": CREATED_BY}


def kanban_argv(args: dict, hermes: str = "hermes") -> list:
    argv = [hermes, "kanban"] + (["--board", args["board"]] if args.get("board") else []) + [
        "create", args["title"], "--body", args["body"], "--assignee", args["assignee"],
        "--idempotency-key", args["idempotency_key"], "--model", args["model"],
        "--max-runtime", str(args["max_runtime"]), "--created-by", args["created_by"], "--json"]
    if args.get("provider"):
        argv += ["--provider", args["provider"]]
    for s in args.get("skills") or []:
        argv += ["--skill", s]
    return argv


def _run(argv, runner=None, stdin=None):
    import subprocess
    if runner is None and os.environ.get("COACH_SELFHEAL_NO_EXEC") == "1":  # tests: never touch the real board/hub
        raise RuntimeError(f"COACH_SELFHEAL_NO_EXEC: refused {argv[:3]}")
    r = (runner or subprocess.run)(argv, capture_output=True, text=True, input=stdin, timeout=120)
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def _task_id(out: str) -> Optional[str]:
    m = re.search(r'"id"\s*:\s*"(t_[0-9a-f]+)"', out) or re.search(r"\b(t_[0-9a-f]{6,})\b", out)
    return m.group(1) if m else None


def _store(store=None):
    if store is not None:
        return store

    class _S:
        root = coach_root()

        def log(self, rec):
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / "log.jsonl").open("a") as f:
                f.write(json.dumps({"ts": time.time(), **rec}) + "\n")
    return _S()


ADOPT_STATUSES = ("ready", "running", "blocked", "todo", "review", "done")  # finished fixes count: the work exists
ADOPT_LOOKBACK_S = 2 * 3600  # no `complaint_at` on the defect: the complaint turn is at most this long before detection


def session_owned_cards(session_id: Optional[str], since: float, home: Optional[Path] = None) -> list:
    """Kanban cards (ADOPT_STATUSES: active, review or done) the origin session created (tasks.session_id) or subscribed
    to (kanban_notify_subs.chat_id), created at/after `since`, newest first, across the default and named boards.
    -> [{id, title, status, created_at, board}]. Unreadable boards are skipped."""
    if not session_id:
        return []
    import sqlite3
    home = Path(home) if home else Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    dbs = [(None, home / "kanban.db")] + [(p.parent.name, p) for p in sorted((home / "kanban" / "boards").glob("*/kanban.db"))]
    marks = ",".join("?" * len(ADOPT_STATUSES))
    rows: list = []
    for board, path in dbs:
        if not path.exists():
            continue
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            db.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        except sqlite3.Error:
            try:
                db = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5)
                db.execute("PRAGMA query_only=ON")
            except sqlite3.Error:
                continue
        try:
            cols = {c[1] for c in db.execute("PRAGMA table_info(tasks)")}
            body = "substr(body, 1, 4000)" if "body" in cols else "NULL"
            for r in db.execute(
                    f"SELECT id, title, status, created_at, {body} FROM tasks WHERE status IN ({marks}) "
                    "AND created_at >= ? "
                    "AND (session_id = ? OR id IN (SELECT task_id FROM kanban_notify_subs WHERE chat_id = ?))",
                    (*ADOPT_STATUSES, int(since), session_id, session_id)):
                rows.append({"id": r[0], "title": r[1], "status": r[2], "created_at": r[3], "board": board,
                             "body": r[4]})
        except sqlite3.Error:
            pass
        finally:
            db.close()
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows


_CARD_ID = re.compile(r"\bt_[0-9a-f]{6,}\b")


def digest_owner_cards(d: dict, since: float, data: Optional[Path] = None) -> list:
    """Cards the origin session itself names after the complaint, read from its coach digest (it may be a session on
    another host, whose board the hub cannot open): t_<hex> ids in user turns newer than `since` (their `prev` agent
    reply and the text, e.g. a chat-closeout notice) and in `last_reply` when the session moved on after `since`.
    -> [{id, title, status, created_at, board: '<host>:default'}], newest mention first. Unreadable digest -> []."""
    sid, host = d.get("session_id"), d.get("host")
    if not sid:
        return []
    root = Path(data) if data else Path(os.environ.get("COACH_DATA") or Path.home() / "code" / "hermes-coach-data")
    paths = sorted(root.glob(f"{host}/*/{sid}.json")) if host else sorted(root.glob(f"*/*/{sid}.json"))
    for path in paths:
        try:
            dg = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        origin = dg.get("host") or path.parts[-3]
        mentions = []
        for t in dg.get("user_turns") or []:
            ts = t.get("t") or 0
            if ts <= since:
                continue
            mentions += [(ts, x) for k in ("prev", "text") for x in _CARD_ID.findall(str(t.get(k) or ""))]
        if (dg.get("last_activity_at") or 0) > since:
            mentions += [(dg["last_activity_at"], x) for x in _CARD_ID.findall(str(dg.get("last_reply") or ""))]
        found: dict = {}
        for ts, cid in sorted(mentions, key=lambda m: m[0], reverse=True):
            found.setdefault(cid, {"id": cid, "title": None, "status": None, "created_at": ts,
                                   "board": f"{origin}:default"})
        if found:
            return list(found.values())
    return []


_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
SESSION_COMMIT_SLACK_S = 1800  # a fix may land a little after the session's last recorded activity


def _fork_repo() -> Path:
    return Path(os.environ.get("COACH_FORK_REPO") or Path.home() / ".hermes" / "hermes-agent")


def _git_lines(repo: Path, *args: str) -> list:
    import subprocess
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    return p.stdout.splitlines() if p.returncode == 0 else []


def session_commit_fix(d: dict, since: float, data: Optional[Path] = None, repo: Optional[Path] = None) -> Optional[dict]:
    """A fix the origin session shipped as a bare commit (no kanban card): a commit on the fork's origin/main made
    after `since` that (a) the session's digest names by sha, or (b) landed inside the session's active window
    (since .. last_activity_at + slack) as a `fix` whose subject names the defect's component, e.g.
    `fix(desktop): ...` for component=desktop, and (when the defect carries a traceback) whose diff touches a file
    named in it. Window+component is what catches a digest whose truncated last_reply
    never names the sha. -> {sha, at, subject} or None."""
    sid, host, comp = d.get("session_id"), d.get("host"), (d.get("component") or "").lower()
    if not sid:
        return None
    root = Path(data) if data else Path(os.environ.get("COACH_DATA") or Path.home() / "code" / "hermes-coach-data")
    paths = sorted(root.glob(f"{host}/*/{sid}.json")) if host else sorted(root.glob(f"*/*/{sid}.json"))
    repo = Path(repo) if repo else _fork_repo()
    for path in paths:
        try:
            dg = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        until = (dg.get("last_activity_at") or since) + SESSION_COMMIT_SLACK_S
        commits = []  # (sha, committer ts, subject) on origin/main since the complaint
        for ln in _git_lines(repo, "log", "origin/main", f"--since={int(since)}", "--format=%H\t%ct\t%s"):
            sha, _, rest = ln.partition("\t")
            ts, _, subj = rest.partition("\t")
            if ts.isdigit() and int(ts) > since:
                commits.append((sha, int(ts), subj))
        text = " ".join([str(dg.get("last_reply") or "")] + [str(t.get(k) or "") for t in dg.get("user_turns") or []
                                                            if (t.get("t") or 0) > since for k in ("prev", "text")])
        named = set(_SHA.findall(text))
        for sha, ts, subj in commits:
            if any(sha.startswith(n) for n in named):
                return {"sha": sha, "at": ts, "subject": subj, "via": "named"}
        # A traceback defect names the files it fails in: a window match must touch one of them, or it is just
        # whatever fix landed nearby (a sessions `--source` change was once adopted as the fix for a lease error).
        tb_files = {os.path.basename(p) for p in re.findall(r'File "([^"]+)"', str(d.get("details") or ""))}
        for sha, ts, subj in sorted(commits, key=lambda c: c[1]):
            head = subj.lower()
            if ts <= until and head.startswith("fix") and comp and re.search(r"\b%s\b" % re.escape(comp), head):
                if tb_files and not tb_files & {os.path.basename(f) for f in
                                                _git_lines(repo, "show", "--name-only", "--format=", sha)}:
                    continue
                return {"sha": sha, "at": ts, "subject": subj, "via": "window"}
    return None


def _adopt_session_card(d: dict, fp: str, store, now: float, owner_fn: Optional[Callable] = None) -> Optional[dict]:
    """If the defect's origin session created/subscribed an active card after the complaint, record the newest as the
    lane's fix_card (approved) and log `selfheal_adopted_owner`. -> result dict, or None to go on and file."""
    sid = d.get("session_id")
    if not sid or (owner_fn is None and os.environ.get("COACH_SELFHEAL_NO_EXEC") == "1"):
        return None
    since = d.get("complaint_at") or ((d.get("at") or now) - ADOPT_LOOKBACK_S)
    try:
        cards = (owner_fn or session_owned_cards)(sid, since)
        if not cards and owner_fn is None:  # not on this hub's boards: the origin host's cards, named in its digest
            cards = digest_owner_cards(d, d.get("complaint_at") or d.get("at") or since)
    except Exception as exc:
        store.log({"event": "selfheal_adopt_error", "fingerprint": fp, "error": str(exc)[:200]})
        return None
    if not cards and owner_fn is None:  # no card: the session may have fixed it with a bare commit
        try:
            fix = session_commit_fix(d, d.get("complaint_at") or d.get("at") or since)
        except Exception as exc:
            store.log({"event": "selfheal_adopt_error", "fingerprint": fp, "error": str(exc)[:200]})
            fix = None
        if fix:
            record_event("fixed", fp, note=f"fixed by origin session in commit {fix['sha'][:12]}: {fix['subject']}"[:300],
                         store=store, now=now)
            store.log({"event": "selfheal_adopted_owner", "via": "session_commit", "fingerprint": fp,
                       "commit": fix["sha"], "match": fix["via"], "session_id": sid})
            return {"action": "skip", "why": f"origin session {sid} already fixed it in commit {fix['sha'][:12]}",
                    "commit": fix["sha"], "adopted": True}
    if not cards:
        return None
    if d.get("source") == "signal":
        # A machine signal names no fix of its own: the session's next card is often unrelated work (an Ask Eve test
        # was once adopted as the owner of a repeated kanban error). Adopt only a card that names the tool or error.
        terms = signal_terms(d)
        relevant = [x for x in cards if card_relevant(x, terms)]
        if not relevant:
            store.log({"event": "selfheal_adopt_skipped", "why": "no session card names the signal's tool or error",
                       "fingerprint": fp, "session_id": sid, "terms": sorted(terms)[:12],
                       "candidates": [x["id"] for x in cards]})
            return None
        cards = relevant
    c = cards[0]
    lane = LaneState(store.root)
    st = lane.get(fp)
    lane.update(fp, status="approved", fix_card=c["id"], approved_at=now, component=d.get("component"),
                symptom=d.get("symptom"), session_id=sid, board=c.get("board"), filed_day=_utc_day(now),
                cards=sorted(known_cards(st) | {c["id"]}), **_complaint_identity(d))
    store.log({"event": "selfheal_adopted_owner", "fingerprint": fp, "card": c["id"], "status": c.get("status"),
               "board": c.get("board"), "session_id": sid, "candidates": [x["id"] for x in cards]})
    return {"action": "skip", "why": f"adopted owner card {c['id']} from session {sid}", "card": c["id"],
            "adopted": True}


_EXC_LINE = re.compile(r"^\s*((?:[A-Za-z_][\w]*\.)*([A-Z]\w*(?:Error|Exception|Exit|Interrupt))):\s*(\S.*?)\s*$", re.M)
_SYMPTOM_LOC = re.compile(r"\b([A-Z]\w*(?:Error|Exception))\s+in\s+([A-Za-z_]\w*)(?:\s*\(([\w./-]+\.\w+)\))?")
SIGNATURE_MIN_MSG = 6  # a shorter exception message ("1", "None") is too generic to match card text on


def error_signature(d: dict) -> Optional[dict]:
    """The defect's error signature: {exc_class, exc_msg, func, file, symptom}. Exception from the last
    `Class: message` line of `details` (a traceback tail), else from `symptom` ("OperationalError in _read_counts
    (kanban_session_activity.py): ..."); function/file from the symptom. None when there is no exception at all."""
    sym = str(d.get("symptom") or "")
    det = str(d.get("details") or "")
    exc_class = exc_msg = func = fname = None
    hits = _EXC_LINE.findall(det)
    if hits:
        _, exc_class, exc_msg = hits[-1]
    m = _SYMPTOM_LOC.search(sym)
    if m:
        exc_class = exc_class or m.group(1)
        func, fname = m.group(2), m.group(3)
    if not exc_class:
        return None
    return {"exc_class": exc_class, "exc_msg": exc_msg, "func": func, "file": fname, "symptom": sym.strip()}


def signature_matches(sig: dict, text: str) -> bool:
    """Specific enough to adopt: the identical symptom string, or the exception (class, plus its message when the
    defect has one) AND the function or file it was raised in. Same exception elsewhere is not a match."""
    t = (text or "").lower()
    if sig.get("corruption_db"):
        if "corrupt" not in t:
            return False
        db = sig["corruption_db"].lower()
        # A bare kanban.db means only the default DB; never a named board's path.
        tokens = re.findall(r"(?:[~\w./-]+\.(?:db|sqlite3?))", t)
        tokens = [re.sub(r"^(?:/home/[^/]+|/users/[^/]+)(?=/\.hermes/)", "~", x) for x in tokens]
        return db in tokens or (db == "~/.hermes/kanban.db" and "kanban.db" in tokens)
    if len(sig.get("symptom") or "") >= 20 and sig["symptom"].lower() in t:
        return True
    msg = sig.get("exc_msg")
    if msg and len(msg) >= SIGNATURE_MIN_MSG:
        exc_ok = msg.lower() in t
    else:
        exc_ok = sig["exc_class"].lower() in t
    loc_ok = any(x and x.lower() in t for x in (sig.get("func"), sig.get("file")))
    return exc_ok and loc_ok


def signature_owned_cards(sig: dict, home: Optional[Path] = None) -> list:
    """Open (not done/archived) cards on this host's boards whose title+body match `sig` (signature_matches).
    -> [{id, title, status, created_at, board}], newest first. Opened read-only like session_owned_cards."""
    import sqlite3
    home = Path(home) if home else _kanban_home()
    dbs = _board_dbs(home)
    rows: list = []
    for board, path in dbs:
        if not path.exists():
            continue
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            db.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        except sqlite3.Error:
            try:
                db = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5)
                db.execute("PRAGMA query_only=ON")
            except sqlite3.Error:
                continue
        try:
            for r in db.execute("SELECT id, title, body, status, created_at FROM tasks "
                                "WHERE status NOT IN ('done', 'archived')"):
                if sig.get("corruption_db"):
                    # Local incident ownership only: remote hosts' equally named DBs are unrelated.
                    matches = (r[3] == "running" and r[0] != os.environ.get("HERMES_KANBAN_TASK")
                               and sig.get("host") == host_name()
                               and signature_matches(sig, r[1] or ""))
                else:
                    matches = signature_matches(sig, f"{r[1] or ''}\n{r[2] or ''}")
                if matches:
                    rows.append({"id": r[0], "title": r[1], "status": r[3], "created_at": r[4], "board": board})
        except sqlite3.Error:
            pass
        finally:
            db.close()
    # Prefer the original incident owner over later running dedupe/follow-up cards.
    rows.sort(key=lambda r: r["created_at"] or 0, reverse=not bool(sig.get("corruption_db")))
    if sig.get("corruption_db") and sig.get("host") == host_name():
        holder = recovery_holder_card(sig["corruption_db"], home, dbs)
        if holder:
            rows = [holder] + [r for r in rows if r["id"] != holder["id"]]
    return rows


def _kanban_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _board_dbs(home: Path) -> list:
    return [(None, home / "kanban.db")] + [(p.parent.name, p) for p in
                                           sorted((home / "kanban" / "boards").glob("*/kanban.db"))]


def _proc_start(pid: int) -> Optional[str]:
    """Linux process start time in clock ticks (/proc/<pid>/stat field 22): tells a reused pid apart."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def incident_holder_card(d: dict) -> Optional[dict]:
    """The live recovery-lock holder's card for this SQLite incident's DB, on this host only (the record and
    the pid are local facts). None for remote incidents, non-incidents, or under COACH_SELFHEAL_NO_EXEC."""
    inc = d.get("incident") or {}
    if not inc.get("db") or inc.get("host") != host_name() or os.environ.get("COACH_SELFHEAL_NO_EXEC") == "1":
        return None
    home = _kanban_home()
    return recovery_holder_card(inc["db"], home, _board_dbs(home))


def _adopt_recovery_holder(d: dict, fp: str, st: dict, store, now: float, runner=None) -> Optional[dict]:
    """A live `hermes kanban recover` owns its DB's incident: point the lane at its card and comment there."""
    holder = incident_holder_card(d)
    if not holder:
        return None
    lane = LaneState(store.root)
    if st.get("fix_card") != holder["id"] or st.get("status") != "approved":
        lane.update(fp, status="approved", fix_card=holder["id"], approved_at=now, component=d.get("component"),
                    symptom=d.get("symptom"), board=holder.get("board"), filed_day=_utc_day(now),
                    cards=sorted(known_cards(st) | {holder["id"]}))
        store.log({"event": "selfheal_adopted_owner", "via": "recovery_lock", "fingerprint": fp,
                   "card": holder["id"], "status": holder.get("status"), "board": holder.get("board"),
                   "previous_card": st.get("fix_card") or st.get("card")})
    commented = _corruption_comment(d, lane.get(fp), store, runner)
    if commented["action"] == "error":
        return commented
    return {"action": "skip", "why": f"adopted recovery-lock holder card {holder['id']}", "card": holder["id"],
            "adopted": True, "via": "recovery_lock"}


def recovery_holder_card(db: str, home: Path, dbs: list) -> Optional[dict]:
    """The card of the live `hermes kanban recover` (or swap_db.py) holding this DB's recovery lock, from the
    `<db>.recover.pid` record it writes ({pid, session_id, task_id}); it owns the incident while it runs. None
    when there is no record, its process is gone, it names no card, or that card is already done/archived.
    `db` is the incident's home-relative spelling (`~/.hermes/...`), resolved against this host's home."""
    import sqlite3
    rel = re.sub(r"^~/\.hermes/?", "", db)
    if rel == db:
        return None
    path = Path(home) / rel
    try:
        info = json.loads(path.with_name(path.name + ".recover.pid").read_text())
        pid, card = int(info["pid"]), str(info.get("task_id") or "")
        os.kill(pid, 0)
    except PermissionError:
        pass
    except (OSError, ValueError, KeyError, TypeError, OverflowError, AttributeError):
        return None
    if info.get("proc_start") and _proc_start(pid) not in (None, str(info["proc_start"])):
        return None  # the recorded process died and its pid was reused
    if not _CARD_ID.fullmatch(card):
        return None
    for board, kdb in dbs:  # the card's status and board, from whichever readable board holds it
        try:
            con = sqlite3.connect(f"file:{kdb}?mode=ro", uri=True, timeout=5)
            try:
                r = con.execute("SELECT id, title, status, created_at FROM tasks WHERE id = ?", (card,)).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            continue
        if r:
            if r[2] in ("done", "archived"):
                return None
            return {"id": r[0], "title": r[1], "status": r[2], "created_at": r[3], "board": board,
                    "via": "recovery_lock"}
    # Not readable anywhere: usually the card lives on the board being rebuilt. Its live holder still owns it.
    board = path.parent.name if path.parent.parent.name == "boards" else None
    return {"id": card, "title": "", "status": "running", "created_at": None, "board": board, "via": "recovery_lock"}


def _adopt_signature_card(d: dict, fp: str, store, now: float,
                          sig_owner_fn: Optional[Callable] = None) -> Optional[dict]:
    """No session owner (e.g. a background signal with no session): adopt an open card that already names the same
    error signature (exception + function/file, or the identical symptom). Logs `selfheal_adopted_owner` via=signature."""
    if sig_owner_fn is None and os.environ.get("COACH_SELFHEAL_NO_EXEC") == "1":
        return None
    sig = ({"corruption_db": d["incident"]["db"], "host": d["incident"]["host"]}
           if d.get("incident") else error_signature(d))
    if not sig:
        return None
    lane = LaneState(store.root)
    st = lane.get(fp)
    try:
        cards = [c for c in (sig_owner_fn or signature_owned_cards)(sig) if c["id"] not in known_cards(st)]
    except Exception as exc:
        store.log({"event": "selfheal_adopt_error", "fingerprint": fp, "via": "signature", "error": str(exc)[:200]})
        return None
    if not cards:
        return None
    c = cards[0]
    lane.update(fp, status="approved", fix_card=c["id"], approved_at=now, component=d.get("component"),
                symptom=d.get("symptom"), session_id=d.get("session_id"), board=c.get("board"),
                filed_day=_utc_day(now), cards=sorted(known_cards(st) | {c["id"]}), **_complaint_identity(d))
    store.log({"event": "selfheal_adopted_owner", "via": "signature", "fingerprint": fp, "card": c["id"],
               "status": c.get("status"), "board": c.get("board"), "session_id": d.get("session_id"),
               "signature": {k: v for k, v in sig.items() if k != "symptom"},
               "candidates": [x["id"] for x in cards]})
    return {"action": "skip", "why": f"adopted owner card {c['id']} by error signature", "card": c["id"],
            "adopted": True, "via": "signature"}


# Root families: different exceptions that are faces of ONE root, so one open lane owns them all. A Kanban board
# read through a stale per-process view (deleted -wal/-shm, dropped locks) shows up as "disk I/O error", as a false
# KanbanDbCorruptError from the health guard, or (after the quarantine fix) as KanbanDbUnreliableViewError.
_KANBAN_DB_FACE = re.compile(r"KanbanDbCorruptError|KanbanDbUnreliableViewError|disk I/O error|"
                             r"database disk image is malformed", re.I)
_KANBAN_DB_WHERE = re.compile(r"kanban_db_connect|kanban_session_activity|kanban\.db|kanban DB", re.I)


def root_family(d: dict) -> Optional[str]:
    """Family name for a defect whose exception is one face of a known shared root, else None."""
    text = "\n".join(str(d.get(k) or "") for k in ("symptom", "details"))
    if not _KANBAN_DB_FACE.search(text):
        return None
    if re.search(r"KanbanDb(?:Corrupt|UnreliableView)Error", text) or _KANBAN_DB_WHERE.search(text):
        return "kanban-db-view"
    return None


def _lane_family(e: dict) -> Optional[str]:
    return e.get("family") or root_family({"symptom": e.get("symptom")})


def _complaint_identity(d: dict) -> dict:
    """Stable evidence shared by hook and digest detectors; never hash generated symptom text.

    Hook `at` is detection time; backstop `at` is the user-turn time. The quote bridges
    delayed detection. Use 300 normalized characters (the queue's evidence limit).
    Missing fields remain unknown: do not backfill legacy lanes from their filing time.
    """
    quote = re.sub(r"\s+", " ", clean_user(str(d.get("quote") or ""))).strip().casefold()[:300]
    at = d.get("complaint_at", d.get("at"))
    try:
        at = float(at) if at is not None else None
    except (TypeError, ValueError):
        at = None
    return {"complaint_at": at,
            "complaint_quote_hash": hashlib.sha256(quote.encode()).hexdigest()[:20] if quote else None}


def _adopt_complaint_lane(d: dict, fp: str, store, now: float) -> Optional[dict]:
    """Different fingerprints for the same session/user turn share the original owner.

    A resolved complaint is not a new recurrence merely because a second detector saw
    it late. Declined owners and expired not-defect decisions may be investigated anew.
    """
    sid = d.get("session_id")
    identity = _complaint_identity(d)
    if not sid or not any(identity.values()):
        return None
    lane = LaneState(store.root)
    owners = []
    entries = lane.read()
    for ofp, e in entries.items():
        # An alias retains the old turn even if its canonical fingerprint later recurs.
        # Consult the canonical lifecycle rather than trusting a stale copied verdict.
        canonical = entries.get(e["complaint_owner"], {}) if e.get("complaint_owner") else e
        if ((ofp == fp and not e.get("complaint_owner")) or e.get("session_id") != sid
                or not canonical or canonical.get("status") == "declined"):
            continue
        if (canonical.get("status") == "not_defect" and
                now - (canonical.get("closed_at") or 0) >= NOT_DEFECT_DAYS * 86400):
            continue
        card = e.get("fix_card") or e.get("card")
        same_quote = (identity["complaint_quote_hash"] and
                      identity["complaint_quote_hash"] == e.get("complaint_quote_hash"))
        try:
            same_time = (identity["complaint_at"] is not None and e.get("complaint_at") is not None and
                         abs(identity["complaint_at"] - float(e["complaint_at"])) <= 120)
        except (TypeError, ValueError):
            same_time = False
        if card and (same_quote or same_time):
            owners.append((e.get("filed_at") or now, ofp, card, e))
    if not owners:
        return None
    _, ofp, card, e = sorted(owners, key=lambda o: (o[0], o[1]))[0]
    st = lane.get(fp)
    # Keep only an alias, not a copied lifecycle: copying `proposed` would launch a
    # second fix from sync(), and copying `not_defect` would suppress this fingerprint
    # globally. Keep its own earlier lifecycle for a genuinely new recurrence.
    previous = st.get("complaint_previous") if st.get("status") == "adopted" else (st or None)
    lane.update(fp, status="adopted", card=None, fix_card=card,
                complaint_previous=previous,
                fixed_at=None, closed_at=None, filed_day=None,
                component=d.get("component"), symptom=d.get("symptom"), session_id=sid,
                board=e.get("board"), complaint_owner=e.get("complaint_owner") or ofp,
                complaint_at=e.get("complaint_at"), complaint_quote_hash=e.get("complaint_quote_hash"),
                cards=sorted(known_cards(st) | {card}))
    store.log({"event": "selfheal_adopted_owner", "via": "complaint_turn", "fingerprint": fp,
               "owner_fingerprint": ofp, "card": card, "session_id": sid, "board": e.get("board")})
    return {"action": "skip", "why": f"same complaint turn as {ofp}", "card": card,
            "adopted": True, "via": "complaint_turn", "fingerprint": fp}


def _adopt_family_lane(d: dict, fp: str, store, now: float) -> Optional[dict]:
    """Another open lane (filed/proposed/approved) already owns this defect's root family: join its card instead of
    filing a second investigation of the same root. The oldest open lane wins. Logs selfheal_adopted_owner via=family."""
    fam = root_family(d)
    if not fam:
        return None
    lane = LaneState(store.root)
    owners = []
    for ofp, e in lane.read().items():
        if ofp == fp or e.get("suppressed") or e.get("status") not in ("filed", "proposed", "approved"):
            continue
        card = e.get("fix_card") or e.get("card")
        if card and _lane_family(e) == fam:
            first = min([h[0] for h in (e.get("history") or []) if h] or [e.get("filed_at") or now])
            owners.append((first, ofp, card, e.get("board")))
    if not owners:
        return None
    _, ofp, card, board = sorted(owners)[0]
    st = lane.get(fp)
    lane.update(fp, status="approved", fix_card=card, approved_at=now, component=d.get("component"),
                symptom=d.get("symptom"), session_id=d.get("session_id"), board=board, family=fam,
                family_owner=ofp, filed_day=_utc_day(now), cards=sorted(known_cards(st) | {card}), **_complaint_identity(d))
    store.log({"event": "selfheal_adopted_owner", "via": "family", "family": fam, "fingerprint": fp, "card": card,
               "owner_fingerprint": ofp, "board": board, "session_id": d.get("session_id"),
               "candidates": [o[2] for o in sorted(owners)]})
    return {"action": "skip", "why": f"adopted {ofp}'s card {card}: same root family {fam}", "card": card,
            "adopted": True, "via": "family", "fingerprint": fp}


def corruption_incident(d: dict, now: float) -> Optional[dict]:
    """One SQLite incident per source host, DB identity and UTC event-hour, not per callsite.
    Pathless kanban tracebacks refer to the source's default board, never the hub's env override.
    Other pathless SQLite errors are left alone rather than merging unrelated databases.
    """
    text = "\n".join(str(d.get(k) or "") for k in ("symptom", "details", "quote"))
    if not re.search(r"database disk image is malformed|KanbanDbCorruptError|file is not a database", text, re.I):
        return None
    paths = re.findall(r"(?:~?/|[A-Za-z]:[/\\])[^\s\"'<>():,;]*\.(?:db|sqlite3?|sqlite)", text)
    path = d.get("db_path") or (paths[0] if paths else None)
    if not path and d.get("component") == "kanban":
        path = "~/.hermes/kanban.db"
    if not path:
        return None
    # The source home may not be the collector's home. Do not expand ~ on this host.
    path = re.sub(r"^(?:/home/[^/]+|/Users/[^/]+)(?=/\.hermes/)", "~", str(path))
    return {"db": os.path.normpath(path), "host": d.get("host") or host_name(),
            "hour": int(d.get("at") or now) // 3600}


def _corruption_comment(d: dict, st: dict, store, runner=None) -> dict:
    card = (st.get("card") if st.get("status") in ("filed", "proposed") else st.get("fix_card"))
    card = card or st.get("fix_card") or st.get("card")
    if not card:
        return {"action": "skip", "why": "incident suppressed"}
    board = st.get("board")
    argv = ["hermes", "kanban"] + (["--board", board] if board else [])
    body = (f"SQLite corruption occurrence ({d['incident']['db']}, host {d['incident']['host']}): "
            f"{d.get('symptom') or ''}\n{str(d.get('details') or '')[-1500:]}")
    rc, out, err = _run(argv + ["comment", card, body], runner)
    store.log({"event": "selfheal_incident_comment", "fingerprint": d["fingerprint"], "card": card,
               "ok": rc == 0, "error": (err or out)[-200:] if rc else None})
    return {"action": "skip" if rc == 0 else "error", "card": card,
            "why": "existing SQLite incident" if rc == 0 else "incident comment failed"}


def _open_card_comment(d: dict, st: dict, store, runner=None) -> dict:
    """A signal flagged ``comment_open_card`` (one occurrence of a defect whose card is open): add it there."""
    card = (st.get("card") if st.get("status") in ("filed", "proposed") else st.get("fix_card")) or st.get("card")
    if not card:
        return {"action": "skip", "why": "no open card"}
    argv = ["hermes", "kanban"] + (["--board", st["board"]] if st.get("board") else [])
    body = (f"Occurrence on {d.get('host') or '?'}: {d.get('symptom') or ''}\n"
            f"{str(d.get('details') or '')[-1500:]}")
    rc, out, err = _run(argv + ["comment", card, body], runner)
    store.log({"event": "selfheal_occurrence_comment", "fingerprint": d["fingerprint"], "card": card,
               "ok": rc == 0, "error": (err or out)[-200:] if rc else None})
    return {"action": "skip" if rc == 0 else "error", "card": card,
            "why": "occurrence added to the open card" if rc == 0 else "occurrence comment failed"}


def file_defect(d: dict, store=None, m: Optional[str] = None, runner=None, now: Optional[float] = None,
                cfg: Optional[dict] = None, budget_fn: Optional[Callable] = None,
                owner_fn: Optional[Callable] = None, sig_owner_fn: Optional[Callable] = None) -> dict:
    store = _store(store)
    now = now or time.time()
    incident = corruption_incident(d, now)
    if incident:
        key = json.dumps(incident, sort_keys=True)
        d = {**d, "incident": incident, "host": incident["host"],
             "fingerprint": "sqlitecorrupt-" + hashlib.sha1(key.encode()).hexdigest()[:16]}
    def run():
        with _admission_lock(store, live=(m or mode()) == "live" and is_hub(cfg)):
            return _file_defect(d, store, m, runner, now, cfg, budget_fn, owner_fn, sig_owner_fn)
    if not incident or (m or mode()) != "live" or not is_hub(cfg):
        return run()
    # Lane JSON is a read/check/create/write transaction. One lock also protects different
    # incident fingerprints from losing each other's updates in the shared lane file.
    import fcntl
    Path(store.root).mkdir(parents=True, exist_ok=True)
    with (Path(store.root) / "sqlite-incidents.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return run()


def _file_defect(d: dict, store=None, m: Optional[str] = None, runner=None, now: Optional[float] = None,
                cfg: Optional[dict] = None, budget_fn: Optional[Callable] = None,
                owner_fn: Optional[Callable] = None, sig_owner_fn: Optional[Callable] = None) -> dict:
    """File one defect: {fingerprint, component, symptom, source, kind, p_defect, session_id, quote, host, at,
    details}. Returns {"action": would_file|filed|skip|forwarded|error, ...}."""
    store = _store(store)
    m = m or mode()
    now = now or time.time()
    cfg = lane_config() if cfg is None else cfg
    if not d.get("fingerprint"):
        d = {**d, "fingerprint": fingerprint(d.get("component") or "other", d.get("symptom") or "")}
    fp = d["fingerprint"]
    if m == "off":
        return {"action": "skip", "why": "COACH_SELFHEAL=off"}
    hub = is_hub(cfg)
    if m == "live" and not hub:
        rc, out, err = _run(hub_ssh_argv(cfg) + [cfg["hub_ssh"],
                             f"python3 {SELF} file --mode live"], runner, stdin=json.dumps(d))
        res = {"action": "forwarded" if rc == 0 else "error", "hub": cfg["hub_ssh"],
               "out": (out or err).strip()[-300:]}
        try:  # the hub prints its own file_defect result as the last line: callers (monitors) need its action
            res["hub_result"] = json.loads((out or "").strip().splitlines()[-1])
        except Exception:
            pass
        store.log({"event": "selfheal_forward", "fingerprint": fp, "ok": rc == 0, "detail": res["out"]})
        return res
    if m == "live" and hub:
        closed = kanban_trigger_closed(d, now=now)
        if closed:
            store.log({"event": "selfheal_skip", "mode": m, "fingerprint": fp, "why": closed})
            return {"action": "skip", "why": closed}
    lane = LaneState(store.root)
    st = lane.get(fp)
    if st.get("status") == "adopted" and m == "live" and hub:
        owner = _adopt_complaint_lane(d, fp, store, now)
        if owner:
            if d.get("incident"):
                commented = _corruption_comment(d, lane.get(fp), store, runner)
                if commented["action"] == "error":
                    return commented
            return owner
        if st.get("complaint_previous"):
            st = lane.update(fp, **{**st["complaint_previous"], "complaint_previous": None,
                                   "complaint_owner": None})
    if (d.get("incident") and m == "live" and hub and not st.get("suppressed")
            and (st.get("snooze_until") or 0) <= now):
        adopted = _adopt_recovery_holder(d, fp, st, store, now, runner)
        if adopted:
            return adopted
    dec = decide(d, st, now, cfg)
    if dec["action"] == "hold":
        held = dec["held"] + [{"at": dec["at"], "host": d.get("host"), "source": d.get("source"), "seen": now}]
        lane.update(fp, held=held)
        store.log({"event": "selfheal_recurrence_held", "mode": m, "fingerprint": fp, "at": dec["at"],
                   "held": len(held), "until": dec["until"], "host": d.get("host"), "source": d.get("source"),
                   "component": d.get("component"), "previous": st.get("fix_card"), "why": dec["why"]})
        return {"action": "skip", "why": dec["why"], "held": len(held)}
    if dec["action"] == "skip":
        if (d.get("incident") and m == "live" and hub and not st.get("suppressed")
                and (st.get("snooze_until") or 0) <= now and st.get("status") in ("filed", "approved", "proposed")):
            return _corruption_comment(d, st, store, runner)
        if (d.get("comment_open_card") and m == "live" and hub and not st.get("suppressed")
                and (st.get("snooze_until") or 0) <= now and st.get("status") in ("filed", "approved", "proposed")):
            return _open_card_comment(d, st, store, runner)
        store.log({"event": "selfheal_skip", "mode": m, "fingerprint": fp, "why": dec["why"]})
        return {"action": "skip", "why": dec["why"]}
    if m == "live" and hub:
        owner = _adopt_complaint_lane(d, fp, store, now)
        if owner:
            if d.get("incident"):
                commented = _corruption_comment(d, lane.get(fp), store, runner)
                if commented["action"] == "error":
                    return commented
            return owner
    if m == "live" and hub:  # the origin session already queued its own fix card: adopt it, file nothing
        owner = ((_adopt_family_lane(d, fp, store, now) or _adopt_signature_card(d, fp, store, now, sig_owner_fn))
                 if d.get("incident") else
                 (_adopt_session_card(d, fp, store, now, owner_fn) or _adopt_family_lane(d, fp, store, now) or
                  _adopt_signature_card(d, fp, store, now, sig_owner_fn)))
        if owner:
            if d.get("incident"):
                commented = _corruption_comment(d, lane.get(fp), store, runner)
                if commented["action"] == "error":
                    return commented
            return owner
    args = card_args(d, dec["key"], dec.get("previous"), cfg)
    if m == "live" and hub and args.get("board") and not board_present(args["board"]):
        return _defer_missing_board(d, args["board"], store, now)
    b = (budget_fn or budget)(args.get("board"), now)
    sched = None
    if not b["ok_usd"]:
        sched = (_next_utc_midnight(now), f"self-heal lane budget: {b['why'] or 'cap reached'}")
    elif not b["ok_running"]:
        sched = (_iso(now + 3600), f"self-heal lane: {b['running']} cards running (max {MAX_RUNNING})")
    if m != "live" or not hub:
        rec = {"event": "selfheal_would_file", "mode": m, "fingerprint": fp, "component": d.get("component"),
               "source": d.get("source"), "why": dec["why"], "recurrence": bool(dec.get("previous")),
               "schedule": sched, "args": args, "host": d.get("host"),
               "budget_available": b.get("available", True)}
        store.log(rec)
        return {"action": "would_file", **rec}
    unavailable = _budget_unavailable(b, fp, store)
    if unavailable:
        return unavailable
    denied = _admission_denied(b, now)
    if denied:
        return _defer_investigation(d, denied, store)
    known, gen = known_cards(st), dec["gen"]
    tid, out, err = None, "", ""
    for _ in range(5):  # an idempotency key that hands back an earlier card (any non-archived status) -> next gen
        rc, out, err = _run(kanban_argv(args), runner)
        if rc != 0 and _board_missing_output(err, out):
            return _defer_missing_board(d, args.get("board") or "?", store, now)
        tid = _task_id(out) if rc == 0 else None
        if not tid or tid not in known:
            break
        store.log({"event": "selfheal_key_collision", "fingerprint": fp, "key": args["idempotency_key"], "card": tid})
        gen += 1
        args = card_args(d, inv_key(fp, gen), dec.get("previous"), cfg)
    if not tid or tid in known:
        why = (err or out).strip()[-300:] if not tid else f"idempotency keys keep returning earlier card {tid}"
        store.log({"event": "selfheal_file_error", "fingerprint": fp, "error": why})
        return {"action": "error", "why": why}
    prev = dec.get("previous")
    kw: dict = dict(status="filed", card=tid, component=d.get("component"), symptom=d.get("symptom"),
              filed_day=_utc_day(now), filed_at=now, board=args.get("board"), session_id=d.get("session_id"),
              fix_card=None if not prev else st.get("fix_card"), gen=gen,
              cards=sorted(known | {tid}), proposed_at=None, family=root_family(d), held=[],
              complaint_owner=None, complaint_previous=None,
              **_complaint_identity(d))
    if prev:
        kw["recurrences"] = int(st.get("recurrences") or 0) + 1
    elif dec["why"] == "refile":
        kw["refiles"] = int(st.get("refiles") or 0) + 1
    lane.update(fp, **kw)
    store.log({"event": "selfheal_filed", "fingerprint": fp, "card": tid, "component": d.get("component"),
               "board": args.get("board"), "recurrence": bool(prev), "scheduled": sched[0] if sched else None,
               "source": d.get("source")})
    if prev:
        store.log({"event": "selfheal_recurrence", "fingerprint": fp, "card": tid,
                   "previous": prev.get("fix_card"), "component": d.get("component")})
    return {"action": "filed", "card": tid, "key": args["idempotency_key"], "scheduled": sched}


def file_fix(fp: str, store=None, runner=None, now: Optional[float] = None, budget_fn=None,
             trigger: str = "do_it") -> dict:
    """Admission is serialized with investigations (see _admission_lock) and reserves FIX_BUDGET."""
    store = _store(store)
    bf = budget_fn or (lambda bd, n: budget(bd, n, new_card_usd=FIX_BUDGET))
    with _admission_lock(store, live=True):
        return _file_fix(fp, store, runner, now, bf, trigger)


def _file_fix(fp: str, store=None, runner=None, now: Optional[float] = None, budget_fn=None,
              trigger: str = "do_it") -> dict:
    """Do it: the fix card `selfheal:<fp>[:r<gen>]:fix` on the hub, keyed to the current generation (idempotent
    within it; a key that returns an earlier card of this fingerprint is an error, never an approval)."""
    store = _store(store)
    now = now or time.time()
    lane = LaneState(store.root)
    st = lane.get(fp)
    if not FP_RE.match(fp or ""):
        return {"action": "error", "why": f"not a fingerprint: {fp!r}"}
    if st.get("fix_card") and st.get("status") in ("approved", "fixed"):
        return {"action": "skip", "why": f"fix card exists: {st['fix_card']}", "card": st["fix_card"]}
    r = route({"component": st.get("component")})
    key = fix_key(fp, generation(st) or 1)
    args = {"title": f"self-heal fix: {(st.get('symptom') or fp)[:90]}", "body": fix_brief(fp, st),
            "idempotency_key": key, "model": MODEL, "provider": PROVIDER, "max_runtime": FIX_RUNTIME,
            "skills": (list(BB_SKILLS) if r["bb"] else list(FIX_SKILLS)), "board": r["board"],
            "assignee": "default", "created_by": CREATED_BY}
    if args.get("board") and not board_present(args["board"]):
        why = f"kanban board {args['board']!r} does not exist"
        store.log({"event": "selfheal_file_error", "fingerprint": fp, "error": why, "board": args["board"]})
        return {"action": "error", "why": why, "board": args["board"]}
    b = (budget_fn or budget)(args.get("board"), now)
    unavailable = _budget_unavailable(b, fp, store)
    if unavailable:
        return unavailable
    denied = _admission_denied(b, now)
    if denied:
        # Keep status=proposed: sync rereads the same Do-it outcome and retries admission.
        store.log({"event": "selfheal_fix_deferred", "fingerprint": fp, **denied})
        return denied
    rc, out, err = _run(kanban_argv(args), runner)
    tid = _task_id(out) if rc == 0 else None
    if not tid and _board_missing_output(err, out):
        why = (err or out).strip()[-300:] or f"kanban board {args.get('board')!r} does not exist"
        store.log({"event": "selfheal_file_error", "fingerprint": fp, "error": why, "board": args.get("board")})
        return {"action": "error", "why": why, "board": args.get("board")}
    if not tid:
        return {"action": "error", "why": (err or out).strip()[-300:]}
    if tid in known_cards(st):
        why = f"fix key {key} returned earlier card {tid}"
        store.log({"event": "selfheal_file_error", "fingerprint": fp, "error": why})
        return {"action": "error", "why": why}
    sched = None
    lane.update(fp, status="approved", fix_card=tid, approved_at=now, cards=sorted(known_cards(st) | {tid}))
    store.log({"event": "selfheal_approved", "fingerprint": fp, "card": tid, "component": st.get("component"),
               "trigger": trigger, "scheduled": sched})
    return {"action": "filed", "card": tid, "key": key, "scheduled": sched}


_CRON_SYMPTOM = re.compile(r"^cron job '(?P<name>.+?)' failed on (?P<host>[^:\s]+):")


def cron_job_state(fp: str, st: dict, home: Optional[Path] = None) -> Optional[dict]:
    """Live failure_streak of the cron job behind a `cron-*` fingerprint, or None when it can't be judged here
    (job on another host, jobs.json unreadable, no job matches the fingerprint)."""
    m = _CRON_SYMPTOM.match(str(st.get("symptom") or ""))
    if not fp.startswith("cron-") or not m or m.group("host") != host_name():
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_coach_signals_selfheal", Path(__file__).with_name("signals.py"))
        sig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sig)
    except Exception:
        return None
    home = Path(home) if home else Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    for prof, h in sig.homes(home):
        try:
            j = json.loads((h / "cron" / "jobs.json").read_text())
        except Exception:
            continue
        for job in (j.get("jobs") if isinstance(j, dict) else j) or []:
            name = job.get("name") or job.get("id")
            if name == m.group("name") and sig.fp("cron", m.group("host"), prof, name) == fp:
                return {"job": job.get("id"), "name": name, "profile": prof,
                        "failure_streak": int(job.get("failure_streak") or 0), "threshold": sig._cron_threshold(h),
                        "last_status": job.get("last_status")}
    return None


class FixPending(Exception):
    """`event fixed` refused: the cron job behind the fingerprint is still failing."""

    def __init__(self, state: dict, entry: dict):
        super().__init__(f"{state['name']} failure_streak={state['failure_streak']} >= {state['threshold']}")
        self.state, self.entry = state, entry


class UnknownFingerprint(ValueError):
    """`event` refused: this lane has no entry for the fingerprint (wrong HERMES_HOME, typo)."""


EVENT_STATUS = {"proposed": "proposed", "fixed": "fixed", "not_defect": "not_defect", "reverted": "reverted",
                "declined": "declined", "duplicate": "approved"}


def resolve_event_card(card: str, st: dict) -> str:
    """The card an `event` reports for, when `--card` is omitted.

    Briefs print the command without `--card`: Hermes scrubs ``HERMES_KANBAN_TASK`` from a worker's tool children
    (terminal, execute_code: ``scrub_kanban_env``) and exports ``HERMES_KANBAN_WORKER_TASK`` there instead, so a
    `--card $HERMES_KANBAN_TASK` line expanded to a bare `--card` and argparse exited 2. Order: explicit `--card`,
    the worker process's own id, the tool-child marker, then the fingerprint's recorded card (the fix card once
    approved, else the investigation card)."""
    for c in (card, os.environ.get("HERMES_KANBAN_TASK"), os.environ.get("HERMES_KANBAN_WORKER_TASK")):
        c = (c or "").strip()
        if c:
            return c
    if st.get("status") == "approved" and st.get("fix_card"):
        return st["fix_card"]
    return st.get("card") or st.get("fix_card") or ""


def record_event(kind: str, fp: str, card: str = "", note: str = "", store=None, now: Optional[float] = None,
                 owner: str = "", result: str = "", require_known: bool = False) -> dict:
    """`self_heal.py event <kind> <fp>`: cards report their lifecycle; logged as selfheal_<kind>.
    `result` (a file path, `fixed` only) is copied to <coach>/selfheal-results/<fp>.md: the quiet home for
    finished work (the Inbox is for decisions only). `require_known` (the CLI) refuses a fingerprint this lane never
    filed: an event sent to the wrong lane (a scratch HERMES_HOME left exported in the card's terminal) used to
    create an orphan entry there, print success, and leave the real entry to go stale (plugin-129c2f2518)."""
    store = _store(store)
    now = now or time.time()
    if kind not in EVENT_STATUS:
        raise ValueError(f"unknown event {kind}; one of {sorted(EVENT_STATUS)}")
    lane = LaneState(store.root)
    if require_known and fp not in lane.read():
        raise UnknownFingerprint(f"unknown fingerprint {fp} in {lane.path}: not recorded. Is HERMES_HOME pointing at a "
                                 f"scratch dir? Rerun with HERMES_HOME=\"$HOME/.hermes\".")
    st = lane.get(fp)
    card = resolve_event_card(card, st)
    if kind == "duplicate" and (not _CARD_ID.fullmatch(owner) or owner == card):
        raise ValueError("duplicate requires a distinct --owner t_<hex> card")
    kw: dict = {"status": EVENT_STATUS[kind]}
    cron_check = None
    if result and kind != "fixed":
        raise ValueError("--result applies to event fixed only")
    result_path = ""
    if result:
        src = Path(result).expanduser()
        if not src.is_file():
            raise ValueError(f"--result file not found: {src}")
        dest = Path(store.root) / RESULTS_DIR / f"{fp}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text())
        result_path = str(dest)
    if kind == "fixed" and fp.startswith("cron-"):
        # A cron fix is only fixed once the job runs green: refuse while it is still at core's nudge streak
        # (cron-5aa82ca809 was marked fixed at streak 7 and climbing, and came back hours later).
        cj = cron_job_state(fp, st)
        cron_check = "unchecked" if cj is None else "ok"
        if cj is not None and cj["failure_streak"] >= cj["threshold"]:
            e = lane.update(fp, fix_pending_at=now, fix_pending_card=card or st.get("fix_card"))
            store.log({"event": "selfheal_fix_pending", "fingerprint": fp, "card": card or st.get("fix_card"),
                       "job": cj["job"], "failure_streak": cj["failure_streak"], "threshold": cj["threshold"],
                       "note": note[:300]})
            raise FixPending(cj, e)
    if kind == "duplicate":
        kw.update(fix_card=owner, approved_at=now, closed_at=None,
                  cards=sorted(known_cards(st) | {c for c in (card, owner) if c}))
        store.log({"event": "selfheal_adopted_owner", "via": "duplicate", "fingerprint": fp,
                   "card": owner, "duplicate_card": card, "note": note[:300]})
    if kind == "proposed":
        kw["proposed_at"] = now
    if kind == "fixed":
        kw.update(fixed_at=now, fix_card=card or st.get("fix_card"), held=[])
    if kind in ("not_defect", "declined"):
        kw["closed_at"] = now
    e = lane.update(fp, **kw)
    store.log({"event": f"selfheal_{kind}", "fingerprint": fp, "card": card or e.get("card"),
               "component": e.get("component"), "note": note[:300], "recurrence": bool(e.get("recurrences")),
               **({"cron_check": cron_check} if cron_check else {}),
               **({"result_file": result_path} if result_path else {})})
    return e


def inbox_outcome(fp: str, runner=None) -> Optional[dict]:
    rc, out, _ = _run(["hermes", "inbox", "outcome", f"selfheal:{fp}"], runner)
    if rc != 0:
        return None
    try:
        return json.loads(out.strip().splitlines()[-1])
    except Exception:
        return None


def card_status(card: str, board: Optional[str] = None, runner=None) -> Optional[str]:
    """Kanban status of a card, "missing" when the board has no such card, None when unknown (read failed)."""
    argv = ["hermes", "kanban"] + (["--board", board] if board else []) + ["show", card, "--json"]
    try:
        rc, out, err = _run(argv, runner)
    except Exception:
        return None
    if rc != 0:
        return "missing" if "no such task" in (err + out).lower() else None
    try:
        return json.loads(out)["task"]["status"]
    except Exception:
        return None


ENDED = ("done", "archived", "missing")


def reconcile(store=None, runner=None, now: Optional[float] = None) -> list:
    """A `filed` (investigation) or `approved` (fix) fingerprint whose card ended without reporting proposed / fixed
    (crashed, timed out, archived) would stay open forever and block every later filing: mark it `stale` so the next
    detection refiles it under a new generation. Returns [(fp, what)]."""
    store = _store(store)
    now = now or time.time()
    lane = LaneState(store.root)
    done = []
    for fp, st in lane.read().items():
        status = st.get("status")
        card = st.get("card") if status == "filed" else st.get("fix_card") if status == "approved" else None
        if not card:
            continue
        ks = card_status(card, st.get("board"), runner)
        if ks not in ENDED:
            continue
        proposal = Path(store.root) / "selfheal" / fp / "proposal.md"
        try:
            p_mtime = proposal.stat().st_mtime if status == "filed" and ks == "done" else 0.0
        except OSError:
            p_mtime = 0.0
        if p_mtime and p_mtime >= (st.get("filed_at") or 0):
            # The investigation finished and wrote this generation's proposal, but its `event proposed` never reached
            # this lane (plugin-129c2f2518: an exported scratch HERMES_HOME sent it to a temp lane that reported
            # success). The proposal is the evidence: record it as proposed so sync() files the fix card.
            lane.update(fp, status="proposed", proposed_at=p_mtime)
            store.log({"event": "selfheal_proposed", "fingerprint": fp, "card": card, "via": "reconcile",
                       "note": f"card {ks} without reporting; proposal.md found", "component": st.get("component")})
            done.append((fp, f"proposed (card {card} {ks}, proposal.md found)"))
            continue
        lane.update(fp, status="stale", closed_at=now)
        store.log({"event": "selfheal_stale", "fingerprint": fp, "card": card, "was": status, "kanban": ks,
                   "component": st.get("component")})
        done.append((fp, f"stale ({status}, card {card} {ks})"))
    return done


# The retired step in cards filed before 921830b (proposals) / 4f1c2a6 (results): both templates wrote
# `env HERMES_HOME="$HOME/.hermes" hermes inbox post --...`. Current briefs only mention "`hermes inbox post`" in a
# "do NOT run" sentence, and proposals may quote a bare `hermes inbox post --poster ...` live check: neither matches.
OLD_INBOX_STEP = re.compile(r'env HERMES_HOME="\$HOME/\.hermes" hermes inbox post\s+--')
INBOX_STEP_NOTE = ("self-heal: IGNORE the `hermes inbox post` step in this card's body. Self-heal now auto-approves "
                   "every proposal and stores results quietly: record a proposal with `self_heal.py event proposed`, "
                   "a finished fix with `self_heal.py event fixed --result result.md` (saved to "
                   "selfheal-results/<fp>.md). The Inbox refuses self-heal posts (AgentFixablePost).")


def sweep_inbox_steps(store=None, runner=None) -> list:
    """Cards keep the instructions they were filed with: comment once on each open self-heal card whose body still
    carries the retired `hermes inbox post --...` step. Returns [(fp, card)]. Lane field `inbox_step_noted` = card."""
    store = _store(store)
    lane = LaneState(store.root)
    done = []
    for fp, st in lane.read().items():
        status = st.get("status")
        card = st.get("card") if status == "filed" else st.get("fix_card") if status == "approved" else None
        if not card or st.get("inbox_step_noted") == card:
            continue
        argv = ["hermes", "kanban"] + (["--board", st["board"]] if st.get("board") else [])
        try:
            rc, out, _ = _run(argv + ["show", card, "--json"], runner)
            task = json.loads(out)["task"] if rc == 0 else None
        except Exception:
            task = None
        if not task or task.get("status") in ENDED or not OLD_INBOX_STEP.search(task.get("body") or ""):
            continue
        try:
            rc, out, err = _run(argv + ["comment", card, INBOX_STEP_NOTE], runner)
        except Exception as exc:
            rc, out, err = 1, "", str(exc)
        if rc == 0:
            lane.update(fp, inbox_step_noted=card)
            done.append((fp, card))
        store.log({"event": "selfheal_inbox_step_noted", "fingerprint": fp, "card": card, "ok": rc == 0,
                   "error": (err or out)[-200:] if rc else None})
    return done


def auto_approve(cfg: Optional[dict] = None) -> bool:
    """Every proposal is approved without a human (David's standing approval): `auto_approve` in
    selfheal-config.json, env COACH_SELFHEAL_AUTO_APPROVE=0/1 overrides; default on."""
    env = os.environ.get("COACH_SELFHEAL_AUTO_APPROVE", "").strip().lower()
    if env:
        return env not in ("0", "false", "off", "no")
    cfg = lane_config() if cfg is None else cfg
    return bool(cfg.get("auto_approve", True))


def sync(store=None, runner=None, now: Optional[float] = None, fixer: Optional[Callable] = None,
         auto: Optional[bool] = None) -> list:
    """Act once on each proposal. Auto-approve (default): every `proposed` fingerprint gets its fix card unless an
    Inbox decision of this generation said Not now / Never; an open legacy proposal card is resolved as Do it so it
    leaves the Inbox. Manual (auto_approve false): the Inbox outcome decides: do_it, not_now (snooze), never
    (suppress). Returns [(fp, what)]."""
    store = _store(store)
    now = now or time.time()
    auto = auto_approve() if auto is None else auto
    lane = LaneState(store.root)
    done = reconcile(store, runner, now)
    try:
        done += [(fp, f"inbox step noted on {card}") for fp, card in sweep_inbox_steps(store, runner)]
    except Exception as exc:
        store.log({"event": "selfheal_inbox_step_error", "error": str(exc)[:200]})
    for fp, st in lane.read().items():
        if st.get("status") != "proposed":
            continue
        o = inbox_outcome(fp, runner)
        stale = bool(o) and (o.get("at") or now) < (st.get("filed_at") or 0)  # an earlier generation's decision
        undecided = not o or o.get("state") == "open" or stale
        if auto and (undecided or o.get("outcome") == "do_it"):
            if o and o.get("state") == "open" and not stale and o.get("session_id"):
                try:  # take the legacy proposal card out of the Inbox
                    _run(["hermes", "inbox", "act", str(o["session_id"]), "do_it"], runner)
                except Exception as exc:
                    store.log({"event": "selfheal_inbox_resolve_error", "fingerprint": fp, "error": str(exc)[:200]})
            how = "do_it" if (o and not stale and o.get("outcome") == "do_it") else "auto"
            r = (fixer or file_fix)(fp, store=store, runner=runner, now=now, trigger=how)
            done.append((fp, f"{how} -> {r.get('card') or r.get('why')}"))
            continue
        if undecided:
            continue
        what = o.get("outcome")
        if what == "do_it":
            r = (fixer or file_fix)(fp, store=store, runner=runner, now=now, trigger="sync")
            done.append((fp, f"do_it -> {r.get('card') or r.get('why')}"))
        elif what in ("not_now", "never"):
            kw = {"status": "suppressed" if what == "never" else "declined", "closed_at": now}
            if what == "never":
                kw["suppressed"] = True
            else:
                kw["snooze_until"] = now + SNOOZE_DAYS * 86400
            lane.update(fp, **kw)
            store.log({"event": "selfheal_declined", "fingerprint": fp, "card": st.get("card"), "how": what,
                       "component": st.get("component")})
            done.append((fp, what))
        elif o.get("state") == "archived":
            lane.update(fp, status="declined", closed_at=now)
            store.log({"event": "selfheal_declined", "fingerprint": fp, "card": st.get("card"), "how": "archived",
                       "component": st.get("component")})
            done.append((fp, "archived"))
    return done


# ── digest backstop ──
INTERACTIVE = ("desktop", "tui", "cli", "slack", "telegram", "discord", "whatsapp", "signal")


def backstop_context(dg: dict, turns: list, t: dict, n: int = 3, chars: int = 300, reply_chars: int = 600) -> str:
    """Session title, the `n` user turns before `t` (each clipped) and the agent's reply to `t`, for the backstop judge."""
    i = next((k for k, x in enumerate(turns) if x is t), len(turns))
    earlier = [str(x.get("text") or "")[:chars].replace("\n", " ") for x in turns[max(0, i - n):i]]
    lines = [f"Session title: {dg.get('title') or '(untitled)'}"]
    lines += ([f"Earlier user messages, oldest first:"] + [f"- {e}" for e in earlier] if earlier
              else ["This is the first user message of the session."])
    # The agent's answer to this message (the next turn's `prev`, or the digest's last reply for the newest turn): the
    # judge cannot see a screenshot, and "why are all the dots red? [screenshot]" only reads as a plugin bug once the
    # reply says what the dots are. A digest written mid-turn has last_reply == this turn's prev: left out.
    reply = (turns[i + 1].get("prev") if i + 1 < len(turns) else dg.get("last_reply")) or ""
    if reply.strip() and reply != (t.get("prev") or ""):
        lines.append("The agent's reply to this message (written after it; use it only to learn what the message and "
                     "any screenshot were about, not as proof of a defect): " + reply[:reply_chars].replace("\n", " "))
    return "\n".join(lines)


_UNSET = object()

# ── interrupted model waits (digest `prev_interrupt`, core display_metadata.interrupt) ──
# A human Stop answered by a bare nudge is a slow first token, not an agent fault; a backend exit the user had to
# nudge is a lost turn (auto-continue did not resume it). Other producers (ws_orphan, lease_takeover, ...) still go
# to the judge. Rows without a reason (older cores) are judged exactly as before.
HUMAN_STOPS = ("user_stop", "new_message", "user_interrupt")
SLOW_MODEL_S = 60.0
BARE_NUDGE_RE = re.compile(
    r"^\s*(?:resume|continue|go on|keep going|carry on|status\??|still there\??|are you (?:still )?(?:there|stuck|"
    r"working)\??|what'?s (?:going on|happening|up)\??|\?+|ping|hello\??|and\??|so\??|well\??)\W*$", re.I)


def interrupt_triage(t: dict) -> Optional[dict]:
    """Verdict for a user turn that follows an interrupted model wait, or None to judge it normally:
    ``{"action": "slow_model"|"user_stop"}`` (skip: not a defect) or ``{"action": "lost_turn", "hit": {...}}``."""
    info = t.get("prev_interrupt")
    if not isinstance(info, dict):
        return None
    reason = str(info.get("reason") or "")
    if reason in HUMAN_STOPS:
        if not BARE_NUDGE_RE.match(clean_user(t.get("text") or "")[:200]):
            return None  # a real complaint after a Stop is still judged
        try:
            waited = float(info.get("waited_s") or 0)
        except (TypeError, ValueError):
            waited = 0.0
        return {"action": "slow_model" if waited >= SLOW_MODEL_S else "user_stop"}
    if reason == "backend_exit":
        v = {"p_defect": 0.95, "kind": "hermes_bug", "component": "gateway",
             "symptom": "turn lost after backend exit, not auto-continued"}
        return {"action": "lost_turn", "hit": {**v, "fingerprint": fingerprint(v["component"], v["symptom"]),
                                               "source": "signal", "stages": []}}
    return None


def backstop(data: Path, store=None, judge: Optional[Callable] = None, now: Optional[float] = None,
             lookback: float = 3 * 3600, filer: Optional[Callable] = None, m: Optional[str] = None,
             limit: int = 200, confirm=_UNSET) -> list:
    """Run the complaint detector over user turns newer than the per-session cursor in the coach digests
    (<data>/<host>/<profile>/<sid>.json). A session seen for the first time is scanned only `lookback` back. The judge
    gets the agent reply before each turn (digest v6 `prev`) and always the session context (title + the earlier user
    turns, `backstop_context`): with neither, the strict stage read \"why didnt this work\" or \"the camera stream is
    broken\" as a new issue and scored 0. Returns the filings."""
    store = _store(store)
    now = now or time.time()
    m = m or mode()
    cur_p = Path(store.root) / "selfheal_backstop.json"
    try:
        cursor = json.loads(cur_p.read_text())
    except Exception:
        cursor = {}
    judge = judge or aux_judge
    # The confirm model (auxiliary.coach_defect_confirm) joins the cascade here as on the hook path; None when unset.
    confirm = confirm_judge() if confirm is _UNSET else confirm
    out, n = [], 0
    for f in sorted(Path(data).glob("*/*/*.json")):
        if f.parts[-3] in ("reports", "findings", "research", "eval", "curriculum"):
            continue
        rel = "/".join(f.parts[-3:])
        try:
            if f.stat().st_mtime < now - 14 * 86400:
                continue
            dg = json.loads(f.read_text())
        except Exception:
            continue
        if dg.get("source") not in INTERACTIVE or dg.get("parent_session_id"):
            continue
        turns = [t for t in dg.get("user_turns") or [] if isinstance(t, dict) and t.get("t")]
        # Cursor: a bare timestamp (legacy) or [timestamp, k] meaning k turns AT that timestamp were already scanned.
        cur = cursor.get(rel, now - lookback)
        since, skip = (cur[0], int(cur[1])) if isinstance(cur, list) and len(cur) == 2 else (cur, None)
        turns.sort(key=lambda t: t["t"])
        at_since = [t for t in turns if t["t"] == since]
        skip = len(at_since) if skip is None else skip  # legacy bare timestamp: everything at it was scanned
        new = at_since[skip:] + [t for t in turns if t["t"] > since]
        if not new:
            continue
        done = []  # turns actually scanned; the cursor only advances over these, so a spent limit loses nothing
        for t in new:
            if n >= limit:
                break
            done.append(t)
            text = t.get("text") or ""
            if not prefilter(text):
                continue
            tri = interrupt_triage(t)
            if tri and tri["action"] in ("slow_model", "user_stop"):
                info = t.get("prev_interrupt") or {}
                store.log({"event": "selfheal_slow_model" if tri["action"] == "slow_model" else "selfheal_user_stop",
                           "session_id": dg.get("id") or f.stem, "at": t["t"], "waited_s": info.get("waited_s"),
                           "prompt_tokens": info.get("prompt_tokens"), "reason": info.get("reason")})
                continue
            if tri:  # lost_turn: a machine signal, no judge
                hit = tri["hit"]
            else:
                n += 1
                hit = detect_complaint(t.get("prev") or "", text, llm=judge, threshold=threshold(),
                                       context=backstop_context(dg, turns, t), confirm=confirm)
            if not hit:
                continue
            sid = dg.get("id") or f.stem
            new_fp = Fingerprints(store.root).record(hit, sid)
            d = {**hit, "session_id": sid, "quote": clean_user(text)[:500], "host": dg.get("host") or f.parts[-3],
                 "profile": dg.get("profile") or f.parts[-2], "at": t["t"], "source": "backstop",
                 "kind": hit.get("kind") or "complaint"}
            store.log({"event": "selfheal_detect", "mode": m, "session_id": sid, "source": "backstop",
                       "p": hit["p_defect"], "component": hit["component"], "symptom": hit["symptom"],
                       "fingerprint": hit["fingerprint"], "new": new_fp, "evidence": d["quote"][:300]})
            if new_fp:
                out.append((filer or file_defect)(d, store=store, m=m))
        if done:
            last = done[-1]["t"]
            scanned = sum(1 for t in done if t["t"] == last) + (skip if last == since else 0)
            cursor[rel] = [last, scanned]
    cur_p.parent.mkdir(parents=True, exist_ok=True)
    cur_p.write_text(json.dumps(cursor))
    return out


def main(argv=None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="self_heal.py", description="self-heal lane (see the Lane comment)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("file", help="file one defect JSON from stdin (hub side of an ssh forward)")
    f.add_argument("--mode", choices=("shadow", "live"))
    x = sub.add_parser("fix", help="Do it: create the fix card for a fingerprint")
    x.add_argument("fp")
    e = sub.add_parser("event", help="record a lifecycle event from a card")
    e.add_argument("kind", choices=sorted(EVENT_STATUS))
    e.add_argument("fp")
    e.add_argument("--card", default="")
    e.add_argument("--note", default="")
    e.add_argument("--owner", default="", help="existing owner card for event duplicate")
    e.add_argument("--result", default="", help="event fixed: result.md to store quietly (no Inbox post)")
    sub.add_parser("sync", help="act on Inbox outcomes (Do it / Not now / Never)")
    b = sub.add_parser("backstop", help="detector over new user turns in the coach digests")
    b.add_argument("--data", default=os.environ.get("COACH_DATA") or str(Path.home() / "code/hermes-coach-data"))
    b.add_argument("--mode", choices=("shadow", "live"))
    c = sub.add_parser("cron", help="hub pass every 30 min: drain queue, backstop, sync")
    c.add_argument("--data", default=os.environ.get("COACH_DATA") or str(Path.home() / "code/hermes-coach-data"))
    sub.add_parser("status", help="lane state + budget")
    a = ap.parse_args(argv)
    if a.cmd == "file":
        d = json.loads(sys.stdin.read())
        print(json.dumps(file_defect(d, m=a.mode)))
    elif a.cmd == "fix":
        print(json.dumps(file_fix(a.fp)))
    elif a.cmd == "event":
        try:
            e = record_event(a.kind, a.fp, a.card, a.note, owner=a.owner, result=a.result, require_known=True)
        except UnknownFingerprint as exc:
            print(json.dumps({"fingerprint": a.fp, "error": str(exc)}))
            print(f"self_heal event: {exc}", file=sys.stderr)
            sys.exit(4)
        except FixPending as fx:
            s = fx.state
            print(json.dumps({"fingerprint": a.fp, "status": fx.entry.get("status"), "fix_pending": True,
                              "job": s["name"], "failure_streak": s["failure_streak"], "threshold": s["threshold"],
                              "why": "cron job still failing: not recorded fixed; keep the card open, verify a green "
                                     "run (failure_streak below threshold), then record fixed again"}))
            sys.exit(3)
        print(json.dumps({"fingerprint": a.fp, "status": e.get("status")}))
    elif a.cmd == "sync":
        for fp, what in sync():
            print(f"{fp}: {what}")
    elif a.cmd == "backstop":
        for r in backstop(Path(a.data), m=a.mode):
            print(json.dumps(r)[:400])
    elif a.cmd == "cron":
        for line in cron(Path(a.data)):
            print(line)
    elif a.cmd == "status":
        st = LaneState(coach_root()).read()
        print(json.dumps({"mode": mode(), "hub": is_hub(), "budget": budget(),
                          "fingerprints": {k: {**{kk: v.get(kk) for kk in ("status", "card", "fix_card", "filed_day")},
                                               "held": len(v.get("held") or [])}
                                           for k, v in st.items()},
                          "held_total": sum(len(v.get("held") or []) for v in st.values()),
                          "handoff_nudges": handoff_nudge_counts(),
                          "nudges": nudge_counts(),
                          "deploy_grace_hours": deploy_grace_s() / 3600}, indent=1))
    return 0


def drain(store=None, m: Optional[str] = None, filer: Optional[Callable] = None) -> list:
    """File queued live detections that were not filed in-process (e.g. the hub was unreachable)."""
    store = _store(store)
    q = Path(store.root) / "selfheal_queue.jsonl"
    done_p = Path(store.root) / "selfheal_queue.offset"
    if not q.exists():
        return []
    lines = q.read_text().splitlines()
    try:
        off = int(done_p.read_text().strip() or 0)
    except Exception:
        off = 0
    out = []
    for line in lines[off:]:
        try:
            r = json.loads(line)
        except Exception:
            continue
        d = {k: r.get(k) for k in ("fingerprint", "component", "symptom", "source", "session_id")}
        d.update(quote=r.get("evidence") or "", at=r.get("ts"), p_defect=r.get("p"), host=host_name())
        out.append((filer or file_defect)(d, store=store, m=m))
    done_p.write_text(str(len(lines)))
    return out


def cron(data: Path, store=None, now: Optional[float] = None, backstop_every: float = 25 * 60,
         backstop_fn: Optional[Callable] = None) -> list:
    """The hub's pass (no agent, hermes cron every 5 min): Inbox outcomes (Do it -> fix card within 5 min), queue
    drain, and the digest backstop at most every `backstop_every` seconds. Prints only what it did.

    Do it is a plain resolve on the Inbox card and THIS pass files the fix card: a Kanban worker (the Inbox's task
    action would run one) is fenced from creating cards through the CLI, the cron scheduler is not."""
    lines = []
    if mode() == "off":
        return lines
    store = _store(store)
    now = now or time.time()
    if mode() == "live":
        for r in retry_deferred(store, now=now):
            lines.append(f"deferred: {json.dumps(r)[:300]}")
    for fp, what in sync(store, now=now):
        lines.append(f"inbox: {fp} {what}")
    for r in drain(store):
        if r.get("action") not in ("skip",):
            lines.append(f"queue: {json.dumps(r)[:300]}")
    stamp = Path(store.root) / "selfheal_backstop.last"
    try:
        last = float(stamp.read_text().strip() or 0)
    except Exception:
        last = 0.0
    if now - last >= backstop_every:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(now))
        try:
            for r in (backstop_fn or backstop)(data, store):
                if r.get("action") not in ("skip",):
                    lines.append(f"backstop: {r.get('action')} {r.get('fingerprint') or r.get('card') or ''}")
        except Exception as exc:
            lines.append(f"backstop failed: {exc}")
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
