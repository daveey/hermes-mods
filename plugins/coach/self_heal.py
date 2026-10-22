"""self_heal: notice the FIRST time the user reports that Hermes did something wrong, and when the agent admits it.

Two entry paths, one schema:
- detect_complaint(prev_reply, user_text) — pre_llm_call. A recall-oriented regex prefilter on the user's message,
  then the aux judge `coach_defect` on (agent's previous reply, user's message).
- detect_admission(final_reply) — post_llm_call. A regex on the agent's own final reply ("my mistake", "I missed",
  "a bug in X", "workaround"), then the same judge in admission mode.

Both return {p_defect, component, symptom, fingerprint} (plus `source`) when p_defect >= threshold, else None.
The fingerprint is a stable hash of component + the symptom's content words, so one defect reported several ways
in one day is filed once (`Fingerprints.seen`).

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

# Recall-oriented: anything that might be a report that something is off. The judge does the precision work.
COMPLAINT_RE = re.compile(
    r"\b(why (?:did|didn'?t|are|aren'?t|is|isn'?t|does|doesn'?t|do|don'?t|was|wasn'?t|were|weren'?t|would|can'?t|not|"
    r"the|so|has|hasn'?t|did ?n'?t)|still|again|broken|break(?:s|ing)?|doesn'?t (?:work|show|load|seem|do|let|have|"
    r"actually|respond)|not (?:working|showing|loading|there|updating|set|responding|running|doing|getting|linked|"
    r"marked|filtering|what)|isn'?t (?:working|showing|there|doing|responding|running|marked|linked)|wrong|bug(?:gy|s)?|"
    r"jank\w*|lost|lose|regress\w*|you (?:didn'?t|forgot|missed|should|said|just|keep|never|lost|broke|made|opened|"
    r"launched|have (?:access|my|it))|i (?:thought|already|told|asked|said|keep|kept|tried)|wtf|what the (?:fuck|hell)|"
    r"fuck\w*|ugh|root ?cause|five whys|5 ?whys|figure out (?:what|why|how this)|keeps?|kept|didn'?t|did not|"
    r"isn'?t|aren'?t|wasn'?t|weren'?t|error|fail\w*|crash\w*|stuck|hang(?:s|ing)?|hung|freez\w*|froze|missing|"
    r"disappear\w*|gone|ignor\w*|supposed to|should(?:n'?t)? have|what happened|how did (?:this|that|we|it)|"
    r"i (?:don'?t|do not|cant|can'?t) see|dont see|no (?:reply|response|longer)|for some reason|tired of|annoying|"
    r"are you sure|stop (?:asking|doing|showing|launching|it)|instead of|blank|empty|slow|takes? (?:so |too )?long|"
    r"took (?:so |too )?long|useless|ugly|confus\w*|halluc\w*|refus\w*|blocked|not (?:what i|the right)|"
    r"spam\w*|leak\w*|orphan\w*|duplicat\w*|twice|over and over|random(?:ly)?|garbled|static|noise|shouldn'?t|"
    r"fix\w*|investigat\w*|should|but|can'?t|cant|cannot|couldn'?t|won'?t|stop\w*|don'?t want|dont want|lazy|"
    r"badly|nope|no,|gone|instead|you have (?:them|it|access)|automatically|already)\b",
    re.I)
IMAGE_RE = re.compile(r"\[(?:image|screenshot)\]|\[Image attached at:", re.I)
IMAGE_ONLY_RE = re.compile(r"^\s*(?:fix(?: this| it)?|this|\?+|what(?:'s| is) this\??|why\??|see)?\s*"
                           r"(?:\[(?:image|screenshot)\]|\[Image attached at:[^\]]*\])", re.I)
SKIP_RE = re.compile(
    r"^\s*(?:/\S+.*|go|yes|y|yep|yeah|ok(?:ay)?|k|do it|done|thanks?|thank you|ty|sure|proceed|continue|resume|"
    r"lgtm|ship it|sounds good|perfect|great|nice|cool|👍)\W*$", re.I)
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

THRESHOLD = 0.65

JUDGE_SYS = """You watch a power user's conversations with his AI agent system ("Hermes": the coding/ops agent, its
Desktop app, plugins, cron jobs, Kanban task cards, worker agents, chat bots, gateways, model providers, and the
products those agents build and run for him, such as the "Baby Bear"/"bb" home assistant, its pods, apps and hub).
You see the agent's previous reply and the user's next message.

Score p_defect (0-1): the user's message REPORTS A DEFECT in that system — something it did wrong or janky, broke,
lost data, regressed, failed, hung, didn't do although it should or said it would, claimed falsely, or asked him to do
something it should have done or checked itself. Includes "still broken" after a fix, "why did you...", "it didn't
work", a frustrated "wtf", a screenshot sent with "fix this" after the agent's work, or a request to root-cause a
failure of the system.

Always high (0.8+):
- "still broken", "nope", "didn't work", "same thing" after the agent changed or claimed to fix something. Assume the
  broken thing is part of the system whenever the agent worked on it or it is a Hermes/Baby Bear feature, app, pod,
  device integration, panel, card or session.
- the agent asked him for something it could have done, found or decided itself ("you have them", "you should have
  access", "don't ask me", "run this for me", "I already did it", "stop asking", "do it automatically").
- a feature that used to work or was built is gone, wrong or regressed ("X is gone", "I thought we fixed it",
  "it's back", "the old version had...").
- "stuck?" / "are you stuck" / "why did you stop" about the agent or a session; sessions, cards or tasks in the wrong
  state ("why is this blocked/not running/not linked/green").
- the agent's knowledge is stale or wrong about his setup ("brain is now hub", "it's on tailscale").

Low (<0.3): a new feature request or design preference with no failure ("make the menu shorter", "add a toggle");
taste feedback on content the agent drafted (wording, style); a question about how something works; approvals,
answers, go-aheads; problems in third-party things the system didn't cause (his disk is full, a vendor site is down,
his phone's battery) unless he says the system caused or should have prevented it; status questions with no sign of
a problem ("is this session still doing anything?", "what's blocked on this session and how do I unblock?", "should
we have this many?"); bugs in his own research/demo projects that ARE the task he gave the agent; telling the agent a
problem is now fixed so it can retry; errors from outside services or admins (a Google Workspace "app is blocked",
a vendor login page) unless the system caused them.

component: one of agent (the chat agent's own behavior), desktop (Hermes Desktop app UI), plugin, cron, kanban,
bot (Slack/Discord/X bots), gateway (sessions, servers, sync), provider (model accounts, rate limits, fallback),
browser, bb (Baby Bear product), other.
symptom: <=10 words naming what is wrong, in neutral words, no names (e.g. "session sidebar order reshuffles",
"asked user to run terminal command").

Return ONLY JSON: {"p_defect": p, "component": "...", "symptom": "..."}"""

ADMIT_SYS = """You watch an AI agent system ("Hermes": agent, Desktop app, plugins, cron, Kanban cards, bots,
gateways, and products it runs such as Baby Bear). You see the agent's final reply to its user.

Score p_defect (0-1): in this reply the agent ADMITS a mistake of its own or of the system that still needs a
root-cause fix.
High (0.8+), even when the reply also says it fixed the immediate symptom (the cause of the mistake is still
there): the agent's OWN error — "that was my mistake/fault", "I was wrong", "I told you X but it wasn't true", "I
missed/skipped X", "I should have...", "sorry, I'll stop asking", "I only gave instructions instead of doing it",
"it had already regressed", a card or worker that did the wrong thing; a bug it stumbled on in Hermes or its tools
while doing something else.
Low (<0.3): the reply reports a bug that IS the task the user gave it ("Root cause: ... Fixed in commit X", "I
created card t_x to fix ...", a worker's fix report); status updates and plans; apologies with no defect ("sorry for
the wait"); an external outage; bugs in the user's own project code.

component: agent, desktop, plugin, cron, kanban, bot, gateway, provider, browser, bb, other.
symptom: <=10 words naming what is wrong, neutral words, no names.

Return ONLY JSON: {"p_defect": p, "component": "...", "symptom": "..."}"""


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


def prefilter(user_text) -> bool:
    t = clean_user(user_text)[:2000]
    if not t or SYNTHETIC_RE.match(t) or SKIP_RE.match(t[:200]):
        return False
    return bool(COMPLAINT_RE.search(t) or IMAGE_ONLY_RE.match(t) or IMAGE_RE.search(t))


def admission_prefilter(reply: str) -> bool:
    return bool(ADMIT_RE.search((reply or "")[:6000]))


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
                "symptom": str(d.get("symptom") or "").strip()[:120]}
    except Exception:
        return None


def complaint_prompt(prev_reply: str, user_text: str) -> str:
    return (f"AGENT'S PREVIOUS REPLY:\n{(prev_reply or '(none)')[-3000:]}\n\n"
            f"USER'S NEW MESSAGE:\n{clean_user(user_text)[:2000]}")


def admission_prompt(final_reply: str) -> str:
    return f"AGENT'S FINAL REPLY:\n{(final_reply or '')[-5000:]}"


def _verdict(v: Optional[dict], source: str, threshold: float) -> Optional[dict]:
    if not v or v["p_defect"] < threshold:
        return None
    return {**v, "fingerprint": fingerprint(v["component"], v["symptom"]), "source": source}


def detect_complaint(prev_reply: str, user_text, llm: Optional[Callable[[str, str], str]] = None,
                     threshold: float = THRESHOLD) -> Optional[dict]:
    """Prefilter, then judge. Returns {p_defect, component, symptom, fingerprint, source} or None. Fail-open."""
    try:
        if not prefilter(user_text):
            return None
        raw = (llm or aux_judge)(JUDGE_SYS, complaint_prompt(prev_reply, user_text))
        return _verdict(parse(raw), "complaint", threshold)
    except Exception:
        return None


def detect_admission(final_reply: str, llm: Optional[Callable[[str, str], str]] = None,
                     threshold: float = THRESHOLD) -> Optional[dict]:
    try:
        if not isinstance(final_reply, str) or not admission_prefilter(final_reply):
            return None
        raw = (llm or aux_judge)(ADMIT_SYS, admission_prompt(final_reply))
        return _verdict(parse(raw), "admission", threshold)
    except Exception:
        return None


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
