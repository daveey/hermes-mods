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

Mode: COACH_SELFHEAL=off|shadow|live (default shadow). shadow logs `selfheal_detect` to coach log.jsonl only;
live also appends the defect to <coach>/selfheal_queue.jsonl for the self-heal lane to file. Fail-open everywhere.
"""
from __future__ import annotations

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
SYNTHETIC_RE = re.compile(r"^\s*(?:\[[A-Z][A-Za-z -]{2,40}(?::|\]| —)|You've reached the maximum|[✔✓✗✖⚠] )")
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
INTERRUPT_RE = re.compile(r"Operation interrupted|turn lease lost|Turn stopped|Tool execution cancelled", re.I)
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
COMPLAINT_STRICT_SYS = """You watch a power user's chats with his AI agent system "Hermes" (the coding/ops agent, its Desktop
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
- a bare nudge ("resume", "what's going on", "are you stuck", "status?") when the previous turn did not end in a real
  answer: it was interrupted, errored, stopped mid-work, went silent after many tool calls, or promised work that never
  came. A nudge after a complete answer is low.
Do NOT count (low, <0.3): brand-new requests or feature asks; design taste or wording preferences the agent could not
have predicted; answers to the agent's legitimate questions; approvals; status questions with no sign of a problem;
bugs in his products or the outside world that he reports as a NEW issue with no sign the agent's or Hermes's own work
caused them or should have prevented them (score those p_defect low and set kind "product_bug").

kind: agent_fault (how the agent worked: claims, asks, verification, instruction-following, scope), hermes_bug
(Hermes software/infra misbehaved), product_bug (see above), none.
component: one of agent, desktop, plugin, cron, kanban, bot, gateway, provider, browser, bb (his home-assistant
product), other.
symptom: <=10 words naming what is wrong, neutral words, no names (e.g. "asked user to run terminal command").

Return ONLY JSON: {"p_defect": p, "kind": "...", "component": "...", "symptom": "..."}"""

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
    elif INTERRUPT_RE.search(r) or any(INTERRUPT_RE.search(x) for x in log[-10:]):
        v = {"p_defect": 0.9, "kind": "interrupted", "component": "gateway",
             "symptom": "turn interrupted before finishing"}
    else:
        errs = [ERR_LINE_RE.search(x).group(1)[:80] for x in _err_lines(log)]
        rep = next((e for e in set(errs) if e and errs.count(e) >= 3), None)
        if not rep:
            return None
        v = {"p_defect": 0.85, "kind": "repeat_failure", "component": "agent", "symptom": "same tool error repeated"}
    return {**v, "fingerprint": fingerprint(v["component"], v["symptom"]), "source": "signal"}


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


def parse(raw: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
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
        return Path(p).read_text()[:6000]
    except Exception:
        return ""


def _with_profile(system: str) -> str:
    prof = profile_text()
    return system + (f"\n\nUSER'S STANDING RULES AND ENVIRONMENT (breaking these is a defect):\n{prof}"
                     if prof.strip() else "")


def _log_text(tool_log, n=25, chars=2500) -> str:
    return "\n".join(str(x)[:200] for x in (tool_log or [])[-n:])[:chars] or "(no tool calls)"


def complaint_prompt(prev_reply: str, user_text, tool_log=None) -> str:
    return (f"AGENT'S PREVIOUS FINAL MESSAGE:\n{(prev_reply or '(none: first turn)')[-3000:]}\n\n"
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


def complaint_verdict(prev_reply: str, user_text, llm: Callable[[str, str], str], tool_log=None,
                      confirm: Optional[Callable[[str, str], str]] = None) -> Optional[dict]:
    """The complaint cascade, before the threshold: judge (COMPLAINT_SYS); below GATE stop there. Otherwise a second
    flash opinion (COMPLAINT_STRICT_SYS) and, when configured, the confirm model on COMPLAINT_SYS; p_defect = the mean
    of the stage scores (a non-defect kind scores 0). Component/symptom come from the strongest stage."""
    prompt = complaint_prompt(prev_reply, user_text, tool_log)
    v1 = parse(llm(_with_profile(COMPLAINT_SYS), prompt))
    if not v1:
        return None
    s1 = _score(v1)
    if s1 < GATE:
        return {**v1, "p_defect": s1, "stages": [s1]}
    v2 = parse(llm(_with_profile(COMPLAINT_STRICT_SYS), prompt))
    stages, best = [s1, _score(v2)], v1
    if confirm:
        v3 = parse(confirm(_with_profile(COMPLAINT_SYS), prompt))
        stages.append(_score(v3))
        best = v3 if v3 and _score(v3) > 0 else best
    p = round(sum(stages) / len(stages), 3)
    return {**best, "kind": best.get("kind") if _score(best) > 0 else v1.get("kind"), "p_defect": p,
            "stages": stages}


def detect_complaint(prev_reply: str, user_text, llm: Optional[Callable[[str, str], str]] = None,
                     threshold: float = THRESHOLD, tool_log=None,
                     confirm: Optional[Callable[[str, str], str]] = None) -> Optional[dict]:
    """pre_llm_call path: prefilter, then the cascade (complaint_verdict) on (previous reply + its turn's tool log,
    user message). Returns {p_defect, kind, component, symptom, fingerprint, source, stages} or None. Fail-open."""
    try:
        if not prefilter(user_text):
            return None
        return _verdict(complaint_verdict(prev_reply, user_text, llm or aux_judge, tool_log, confirm),
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
    m = os.environ.get("COACH_SELFHEAL", "shadow").strip().lower()
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


def handle(hit: Optional[dict], session_id: str, store, evidence: str = "", m: Optional[str] = None) -> Optional[dict]:
    """Log a detection (shadow) and, in live mode, enqueue it for the lane once per fingerprint per day."""
    if not hit:
        return None
    m = m or mode()
    new = Fingerprints(store.root).record(hit, session_id)
    rec = {"event": "selfheal_detect", "mode": m, "session_id": session_id, "source": hit["source"],
           "p": hit["p_defect"], "component": hit["component"], "symptom": hit["symptom"],
           "fingerprint": hit["fingerprint"], "new": new, "evidence": (evidence or "")[:300]}
    store.log(rec)
    if m == "live" and new:
        q = Path(store.root) / "selfheal_queue.jsonl"
        with open(q, "a") as fh:
            fh.write(json.dumps({**rec, "ts": time.time()}) + "\n")
    return rec
