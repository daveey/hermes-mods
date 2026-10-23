#!/usr/bin/env python3
"""Ask-gate metric (stdlib only): two weekly KPIs from the cross-host coach digests, both target 0.
  ask_me          David's corrections of the form "why are you asking me / I already did it / do it yourself".
  had_to_ask_fix  David asking for a Hermes defect to be fixed or root-caused ("make a card to fix", "root cause",
                  "figure out why", "fixed this once / broken again", "why did you not…", a screenshot + "fix"):
                  the work the self-heal lane should have started unasked. Baby Bear product fix requests count
                  separately as `had_to_ask_fix_bb` and go to bb-tuner's handoff file (--bb-handoff), not the lane.

    coach_askgate.py --data ~/code/hermes-coach-data --weeks 6 --baseline OUT.md     # baseline, 6 weeks
    coach_askgate.py --data ... --last-week --post --selfheal                         # Monday: previous Mon-Sun, to the Inbox
    coach_askgate.py --data ... --weeks 6 --json                                      # machine-readable

The Monday post is one card (key ask-gate-weekly:<monday>) with both classes and, with --selfheal, the self-heal lane
section from coach_weekly.py. It writes reports/askgate/<monday>.json in the data repo; another host that finds that
marker (posted by someone else) skips its post, so moving the cron between hosts never doubles the card.

A hit is a real user turn (digests already drop Hermes-written user-role messages; Slack-engagement batches, probe
prompts and Kanban notices are skipped here too) matching a correction pattern: the agent asked David for something
it should have checked or done itself. Regex precision is imperfect by design (see references/labeling.md): hits are
listed with session + quote so a human can eyeball them, and a miss on the sampled turns (digests keep the first 10 and
last 50 user turns per session) is possible. Weeks run Monday 00:00 PT to Monday 00:00 PT. Hits seen on several hosts
(forked or mirrored sessions) count once.
Target after the ask-gate ships: 0.
"""
import argparse, glob, json, os, re, subprocess, sys, time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")

# Turns that are not David typing a correction.
SKIP = re.compile(
    r"^\s*(\[(Slack engagement|IMPORTANT:|OUT-OF-BAND)|\{\s*\"(?:person|kind)\"|Without any other tools:|[✔✖✘⏸⏱🔄]\ufe0f?\s+\[)", re.I)
OOB = re.compile(r"\[OUT-OF-BAND USER MESSAGE[^\]]*\]\s*(.*?)\s*\[/OUT-OF-BAND USER MESSAGE\]", re.S)

_V = r"(?:do|did|doing|check|checked|merge|nudge|investigate|investigating|figure|fix|run|handle|try|test|verify|find|look|ssh|read|open|search|review|update|click|sign|log)"
PATTERNS = [
    ("why-asking-me", r"\bwhy\b.{0,40}\b(?:ask|asking|asked)\w*\s+me\b"),
    ("stop-asking", r"\b(?:stop|quit)\s+asking\b|(?<!\bi )\bkeep\s+asking\b|\bdon.?t\s+(?:ask|keep asking)\s+me\b|\bto stop asking\b"),
    ("do-it-yourself", r"\b" + _V + r"\b(?:\s+\S+){0,5}?\s+yourself\b"),
    ("why-not-yourself", r"\bwhy\s+(?:did|didn.?t|don.?t|can.?t|couldn.?t|would|wouldn.?t)\w*\s+(?:you|the agent|it)\b.{0,40}\byourself\b"),
    ("why-didnt-check", r"\bwhy\s+(?:did|didn.?t)\w*\s+you\s+(?:just\s+)?(?:figure|check|look|find|verify|list|search)"),
    ("instead-of-doing", r"\binstead of (?:doing|checking|running|trying)\b.{0,30}\b(?:yourself|it)\b"),
    ("already-done", r"\bi(?:.?ve|\s+have)?\s+(?:already|just)\s+(?:did|done|told|said|answered|completed|handled)\b"
                     r"|\bi\s+keep\s+(?:completing|doing|telling|answering|saying|handling)\b"
                     r"|\bhas been (?:revoked|canceled|cancelled|done|handled)\s+for\b"),
    ("you-just-said", r"\byou\s+(?:just|already)\s+(?:said|told|know)\b|\byou should (?:already )?know\b"),
    ("waiting-on-me", r"\bare you waiting (?:for|on) me\b|\bi (?:dont|don.?t) see any blocked\b"),
]
RX = [(n, re.compile(p, re.I)) for n, p in PATTERNS]
# Not corrections even though a pattern fires (the user is granting access, giving a style rule, or describing a deliverable).
NEGATIVE = re.compile(r"\brights? to\b|\b(?:intro|introduce|rename|describe|identify)\s+yourself\b|\bsetting (?:this|it) up yourself\b", re.I)


# Class `had_to_ask_fix`: David asking for a Hermes defect to be fixed or root-caused ("make a card to fix", "root
# cause", "figure out why", "fixed this once / broken again", "why did you not…", a screenshot + "fix"). Counted only
# in interactive chats (a cron/Kanban prompt that says "root cause" is a Hermes-written instruction, not David), and
# only when the turn is not already an ask-me correction (that class wins, so no turn counts twice). Like the ask-me
# class, a regex: it cannot tell a Hermes defect from a bug in the project David is building, so the weekly post lists
# every hit with its quote. The self-heal lane exists to drive this to 0.
ASK_CLASS, FIX_CLASS, BB_CLASS = "ask_me", "had_to_ask_fix", "had_to_ask_fix_bb"
CLASS_LABEL = {ASK_CLASS: "Ask-me corrections (\"why are you asking me / I already did it / do it yourself\")",
               FIX_CLASS: "Had to ask for a Hermes/agent fix (\"make a card to fix / root cause / figure out why / "
                          "broken again\")",
               BB_CLASS: "Had to ask for a Baby Bear fix (handed to bb-tuner: why didn't BB's own monitoring catch it?)"}

# Baby Bear product fix requests count separately (`had_to_ask_fix_bb`): the self-heal complaint cascade scopes product
# bugs reported as new issues out by design, and catching them before David notices is BB's own monitoring (bb-tuner:
# logs, digest flags, burn-down), so each one goes to bb-tuner as a "why didn't we catch this first" finding instead of
# to the self-heal lane. Decided 2026-10-22 (ask-gate review, week of 2026-10-12). The turn's own words decide; the
# session title only breaks a tie when the turn names neither BB nor Hermes.
BB_WORDS = re.compile(r"\b(?:bb(?!-tuner)|baby ?bear|pods?|echo[- ]?(?:dot|ear)|echoear|nanit|cameras?|camera stream|"
                      r"home assistant|triptych|mishear\w*|(?:my|the|apple) watch|watch app|room listening|wake word|"
                      r"volume|main room|soundscapes?)\b", re.I)
BB_HA = re.compile(r"\bHA\b")
HERMES_WORDS = re.compile(r"\b(?:hermes|desktop|sidebar|sessions?|kanban|cards?|plugins?|pluggin|gateway|skills?|cron|"
                          r"subagents?|bottom-bar|status bar|left pane|panel)\b", re.I)


def _bb_hermes_counts(text):
    return len(BB_WORDS.findall(text)) + len(BB_HA.findall(text)), len(HERMES_WORDS.findall(text))


def session_text(d):
    """A digest's user turns, the agent replies before them and its last reply, joined: the last-resort tie-break."""
    turns = [u for u in d.get("user_turns") or [] if isinstance(u, dict)]
    return " ".join([f"{u.get('text') or ''} {u.get('prev') or ''}" for u in turns] + [d.get("last_reply") or ""])


# "[Image attached at: ~/Library/Application Support/Hermes/composer-images/…]": the path names Hermes on every
# Desktop screenshot, which made every screenshot fix request a Hermes one.
ATTACH_PATH = re.compile(r"\[Image attached at:[^\]\n]*\]?", re.I)


def is_bb(text, title="", profile="", session=""):
    """True when a fix request is about the Baby Bear product rather than Hermes or the agent. The turn's own words
    decide, then the title; when neither names BB or Hermes, the rest of the session does (BB words outnumber Hermes
    words). Week of 2026-10-12: "why didnt this work [screenshot]" and "can we figure out why this happen" were BB
    music/watch sessions whose later turns and replies said so, and counted as Hermes misses."""
    if (profile or "").startswith("bb-") and profile != "bb-tuner":
        return True
    text = ATTACH_PATH.sub("[screenshot]", text or "")
    if BB_WORDS.search(text) or BB_HA.search(text):
        return True
    if HERMES_WORDS.search(text):
        return False
    if BB_WORDS.search(title or "") or BB_HA.search(title or ""):
        return True
    if HERMES_WORDS.search(title or ""):
        return False
    bb, hermes = _bb_hermes_counts(session or "")
    return bb > hermes
_FIXV = r"(?:fix|investigat\w*|root.?caus\w*|figure out|debug|look into|find out)"
FIX_PATTERNS = [
    ("card-to-fix", r"\b(?:make|create|file|open|start|spin up|add)\s+(?:a\s+|an\s+|another\s+)?(?:new\s+)?(?:kanban\s+)?"
                    r"(?:card|task|ticket|issue|bug)s?\b.{0,60}?\b" + _FIXV),
    ("root-cause", r"\broot.?caus(?:e|ed|es|ing)\b|\b5 whys\b|\bfive.?whys\b"),
    ("figure-out-why", r"\b(?:figure out|find out|investigate|look into|dig into|understand)\b.{0,30}?"
                       r"\b(?:why|what went wrong|what(?:.s| is) (?:wrong|broken|going on))\b"),
    ("analyze-and-fix", r"\b(?:investigate|analy[sz]e|analysis|debug|diagnose)\b.{0,40}?\band (?:then )?fix\b"),
    ("broken-again", r"\bfixed (?:this|it|that) (?:once|before|already)\b|\b(?:broken|broke|happening|failing|back) again\b"
                     r"|\bthought (?:we|you|i) (?:had |mostly |already |just )*fixed\b|\bstill (?:broken|happening|failing|"
                     r"not (?:working|fixed))\b|\bregress(?:ed|ion)\b"),
    ("why-did-you-not", r"\bwhy (?:did you not|didn.?t you|did(?:n.?t)? (?:it|that|the \w+) not|did you (?:skip|ignore|lose|"
                        r"forget|stop|break|delete|drop))\b|\bwhy (?:is|are|was|were|isn.?t|aren.?t|wasn.?t|does|doesn.?t)"
                        r"\b(?:\s+\S+){0,4}?\s+(?:not (?:working|linked|showing|loading|running)|broken|missing|red|failing|"
                        r"empty|gone|stuck)\b"),
    ("is-broken", r"\b(?:is|are|seems|looks|got|was|it.?s|that.?s) (?:still |now |totally |completely |all )?broken\b"
                  r"|\bkeeps? (?:crashing|failing|breaking|launching|stealing|popping|losing|reshuffling|reordering)\b"),
]
FIX_RX = [(n, re.compile(p, re.I)) for n, p in FIX_PATTERNS]
SCREENSHOT = re.compile(r"\[(?:screenshot|Image attached)[^\]]*\]", re.I)
SCREENSHOT_FIX = re.compile(r"\b(?:fix|broken|wtf|why)\b", re.I)
# Instructions Hermes wrote (Inbox Do-it prompts, hand-off briefs, card bodies pasted back: David does not type
# Markdown bold), and a quoted button label.
# Also not a fix request (week of 2026-10-12 review): a status question about past fixes ("do we expect that we fixed
# the root causes"), a conditional plan ("make babybear do it, and if it fails figure out why"), and a spec for a
# mechanism that files cards itself ("a process that ... should create a kanban task to fix", "modify bb-tuner to
# measure X and then file issues to investigate").
FIX_NEGATIVE = re.compile(r"^\s*(?:work kanban task|Investigate this [\w-]+ cron output|Review (?:these|this week)|"
                          r"Self-heal|Context carried over|\{)|\*\*\w|\bno need to (?:root|fix)\b|[\"'“]still broken[\"'”]"
                          r"|\bfixed the root.?causes?\b"
                          r"|\bif (?:it|that|this|they) (?:fails?|breaks?|doesn.?t work|don.?t work)\b"
                          r"|\b(?:it|that|which|then)\s+(?:should\s+|would\s+|will\s+|can\s+)?(?:automatically\s+)?"
                          r"(?:file|create|open|make)s?\s+(?:a\s+|an\s+)?(?:new\s+)?(?:kanban\s+)?(?:card|task|ticket|issue)s?\b"
                          # approving numbered items from the agent's own list ("make cards to fix 3, 4, 5"): the
                          # agent proposed them, so it is a go, not a defect report (week of 2026-10-12)
                          r"|\b(?:make|create|open|file)\s+(?:kanban\s+)?(?:cards?|tasks?|tickets?|issues?)\s+(?:to fix|for)\s+"
                          r"(?:items?\s+|#)?\d+(?:\s*(?:,|and|&|-)\s*#?\d+)+\b",
                          re.I)
INTERACTIVE_SOURCES = {"desktop", "tui", "cli", "slack", "telegram", "discord", "whatsapp", "signal"}


def classify_fix(text):
    """The had_to_ask_fix sub-pattern of a cleaned user turn, or None. Ask-me corrections take precedence."""
    if SKIP.match(text) or FIX_NEGATIVE.search(text) or classify(text):
        return None
    for name, rx in FIX_RX:
        if rx.search(text):
            return name
    if SCREENSHOT.search(text) and SCREENSHOT_FIX.search(SCREENSHOT.sub("", text)):
        return "screenshot-fix"
    return None


def clean(text):
    m = OOB.search(text)
    if m:
        text = m.group(1)
    return text.strip()


def classify(text):
    if SKIP.match(text) or NEGATIVE.search(text):
        return None
    for name, rx in RX:
        if rx.search(text):
            return name
    return None


def week_start(ts):
    d = datetime.fromtimestamp(ts, PT)
    mon = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return mon


def collect(data, since_ts, until_ts):
    hits, seen, first_ts = [], set(), None
    scanned = {}  # week start ts -> user turns scanned (0 = no digest coverage, the count is unknown, not zero)
    for f in glob.glob(str(Path(data).expanduser() / "*" / "*" / "*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if not isinstance(d, dict) or not d.get("id"):  # reports/askgate markers, eval files: not digests
            continue
        st = d.get("started_at")
        if st and (first_ts is None or st < first_ts):
            first_ts = st
        turns = d.get("user_turns") or []
        for i, u in enumerate(turns):
            t, text = u.get("t", 0), clean(u.get("text") or "")
            if not (since_ts <= t < until_ts) or not text:
                continue
            ws = week_start(t)
            scanned[ws] = scanned.get(ws, 0) + 1
            # A session that opens with a screenshot and "stop asking me" is about an OS/app popup, not an agent ask
            # (nothing preceded it). Text-only openers still count: they often answer an ask carried over from another
            # session (e.g. a CLI chat replying to a bb-tuner question).
            opener_popup = i == 0 and re.search(r"\[(?:screenshot|Image attached)", text, re.I)
            kind, cls = (None if opener_popup else classify(text)), ASK_CLASS
            if not kind and d.get("source") in INTERACTIVE_SOURCES:
                # a fix request often opens a session ("fix [screenshot]"), so the opener rule does not apply here
                kind, cls = classify_fix(text), FIX_CLASS
                if kind and is_bb(text, d.get("title") or "", d.get("profile") or Path(f).parent.name,
                                  session_text(d)):
                    cls = BB_CLASS
            if not kind:
                continue
            key = (d.get("id"), int(t), text[:80])
            if key in seen:
                continue
            seen.add(key)
            hits.append({"t": t, "session": d.get("id"), "host": d.get("host"), "source": d.get("source"),
                         "class": cls, "pattern": kind, "quote": text})
    hits.sort(key=lambda h: h["t"])
    return hits, scanned


def fmt_ts(t):
    return datetime.fromtimestamp(t, PT).strftime("%Y-%m-%d %H:%M %Z")


def quote(h, n=240):
    q = re.sub(r"\s+", " ", re.sub(r"\[Image attached[^\]]*\]", "", h["quote"])).strip()
    return q if len(q) <= n else q[:n] + "…"


def by_class(hits, cls):
    return [h for h in hits if h.get("class", ASK_CLASS) == cls]


def week_counts(hits, weeks):
    out = {}
    for w in weeks:
        a = [h for h in by_class(hits, ASK_CLASS) if week_start(h["t"]) == w]
        f = [h for h in by_class(hits, FIX_CLASS) if week_start(h["t"]) == w]
        b = [h for h in by_class(hits, BB_CLASS) if week_start(h["t"]) == w]
        out[w] = {ASK_CLASS: len(a), FIX_CLASS: len(f), "fix_sessions": len({h["session"] for h in f}), BB_CLASS: len(b)}
    return out


def render(hits, weeks, scanned, title):
    counts = week_counts(hits, weeks)
    out = [f"# {title}", "",
           f"Generated {fmt_ts(time.time())}. Detector: skills/productivity/hermes-coach/scripts/coach_askgate.py "
           f"(regex over cross-host coach digests). Two classes, target 0 each: `{ASK_CLASS}` once the ask-gate ships, "
           f"`{FIX_CLASS}` once the self-heal lane is live. A turn counts in one class only (ask-me wins); fix requests "
           "are counted in interactive chats only.", "",
           "| Week (Mon, PT) | Ask-me | Had to ask for a Hermes/agent fix (sessions) | Baby Bear fix requests | "
           "User turns scanned |", "|---|---|---|---|---|"]
    now = datetime.now(PT)
    for w in weeks:
        tag = " (partial)" if w + timedelta(days=7) > now else ""
        n, c = scanned.get(w, 0), counts[w]
        fix_cell = f"{c[FIX_CLASS]} ({c['fix_sessions']})" if n else "n/a"
        out.append(f"| {w:%Y-%m-%d}{tag} | {c[ASK_CLASS] if n else 'n/a'} | {fix_cell} | {c[BB_CLASS] if n else 'n/a'} | "
                   f"{n} |")
    fx = by_class(hits, FIX_CLASS)
    out.append(f"| **Total** | **{len(by_class(hits, ASK_CLASS))}** | **{len(fx)}** ({len({h['session'] for h in fx})}) | "
               f"**{len(by_class(hits, BB_CLASS))}** | {sum(scanned.values())} |")
    out.append("")
    for cls in (FIX_CLASS, BB_CLASS, ASK_CLASS):
        out += [f"## {CLASS_LABEL[cls]}", ""]
        for w in weeks:
            hs = [h for h in by_class(hits, cls) if week_start(h["t"]) == w]
            out.append(f"### Week of {w:%Y-%m-%d}: {len(hs) if scanned.get(w) else 'n/a (no digest coverage)'}")
            out.append("")
            for h in hs:
                out.append(f"- {fmt_ts(h['t'])} · {h['host']} · session {h['session']} · {h['pattern']}: \"{quote(h)}\"")
            if not hs:
                out.append("- none" if scanned.get(w) else "- no data: no host kept sessions from this week")
            out.append("")
    out.append("Digests keep the first 10 and last 50 user turns per session, so a hit in the middle of a very long session "
               "can be missed; 'n/a' weeks predate every host's retained sessions.")
    return "\n".join(out)


def marker_path(data, week):
    return Path(data) / "reports" / "askgate" / f"{week:%Y-%m-%d}.json"


def posted_elsewhere(data, week, me):
    """The host that already posted this week's card, if it is not this one (one card per week across hosts)."""
    p = marker_path(data, week)
    try:
        m = json.loads(p.read_text())
    except Exception:
        return None
    who = m.get("posted_by") or m.get("host")  # "host" = markers written before the rename
    return who if who and who != me else None


def post_body(hits, week, extra=None):
    ask, fix, bb = by_class(hits, ASK_CLASS), by_class(hits, FIX_CLASS), by_class(hits, BB_CLASS)
    fs = len({h["session"] for h in fix})
    lines = [f"**{len(fix)}** time{'s' if len(fix) != 1 else ''} David had to ask for a Hermes/agent fix (\"make a card to "
             f"fix / root cause / figure out why / broken again\") in {fs} session{'s' if fs != 1 else ''}, "
             f"**{len(bb)}** Baby Bear fix request{'s' if len(bb) != 1 else ''} (handed to bb-tuner), and "
             f"**{len(ask)}** correction{'s' if len(ask) != 1 else ''} where he had to say \"why are you asking me / I "
             f"already did it / do it yourself\", in the week of {week:%Y-%m-%d}. Target: 0 for all three.", ""]
    for cls, hs in ((FIX_CLASS, fix), (BB_CLASS, bb), (ASK_CLASS, ask)):
        lines += [f"### {CLASS_LABEL[cls]}: {len(hs)}", ""]
        for h in hs[:15]:
            lines.append(f"- {fmt_ts(h['t'])} · session `{h['session']}` · {h['pattern']}: \"{quote(h, 160)}\"")
        if len(hs) > 15:
            lines.append(f"- … and {len(hs) - 15} more")
        if not hs:
            lines.append("- none")
        lines.append("")
    if extra:
        lines += extra
    return "\n".join(lines)


def selfheal_lines(data, homes):
    """The weekly self-heal lane section (coach_weekly), or a one-line note if it cannot be built here."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import coach_weekly
        return coach_weekly.section_selfheal(Path(data), time.time(), 7, homes=homes)
    except Exception as e:  # the KPI post never fails on the lane section
        return [f"Self-heal lane section unavailable: {type(e).__name__}: {e}"]


def _coach_mod(name):
    import importlib.util
    p = Path(__file__).resolve().parents[4] / "plugins" / "coach" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_askgate_{name}", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def lane_sessions(home):
    """Session ids the self-heal lane already has a filing for (any status)."""
    try:
        st = json.loads((Path(home) / "coach" / "selfheal_lane.json").read_text())
    except Exception:
        return set()
    return {v.get("session_id") for v in st.values() if isinstance(v, dict) and v.get("session_id")}


def hit_defect(h):
    """One ask-gate hit -> a monitor finding for the self-heal lane. A fix request the lane never filed is a detector
    miss (the review used to ask for exactly that); an ask-me correction is the agent's needless handoff."""
    q = quote(h, 160)
    if h["class"] == FIX_CLASS:
        title = f"self-heal detector missed a fix request: \"{q[:90]}\""
        comp = "plugin"
        body = (f"Ask-gate weekly: David had to ask for a Hermes fix ({h['pattern']}) on {fmt_ts(h['t'])}, "
                f"host {h.get('host')}, session {h['session']}, and the self-heal lane has no filing for that session. "
                f"Quote: \"{q}\"")
        prompt = ("Check whether the self-heal detector (plugins/coach/self_heal.py complaint judge or the digest "
                  "backstop) saw this turn; find why it missed it and fix the detector. Also file the underlying defect.")
    else:
        title = f"agent asked David to do it himself: \"{q[:90]}\""
        comp = "agent"
        body = (f"Ask-gate weekly: ask-me correction ({h['pattern']}) on {fmt_ts(h['t'])}, host {h.get('host')}, "
                f"session {h['session']}. Quote: \"{q}\"")
        prompt = "Find the root cause and fix the ask-gate or the skill that should have prevented it."
    return {"monitor": "ask-gate", "key": f"ask-gate:{h['session']}:{h['class']}", "title": title, "body": body,
            "prompt": prompt, "component": comp, "session_id": h["session"], "quote": q}


def route_hits(hits, home, filer=None, signals=None):
    """File every hit the lane does not already own. -> (owned hits, not-owned hits). In shadow mode nothing is
    owned (file_defect only logs), so the card keeps its review prompt until the lane goes live."""
    signals = signals or _coach_mod("signals")
    known = lane_sessions(home)
    owned, rest = [], []
    for h in hits:
        if h["session"] in known:
            owned.append(h)
            continue
        m = hit_defect(h)
        d = signals.monitor_defect(m, host=h.get("host"))
        d.update(session_id=m["session_id"], quote=m["quote"])
        try:
            res = (filer or _coach_mod("self_heal").file_defect)(d)
        except Exception as e:
            res = {"action": "error", "why": str(e)[:200]}
        (owned if signals.lane_owns(res) else rest).append(h)
    return owned, rest


BB_HANDOFF = "~/.hermes/profiles/bb-tuner/work/askgate-bb-misses.jsonl"


def hand_to_bb_tuner(hits, path):
    """Append each Baby Bear fix request bb-tuner has not been handed yet to its handoff file (bb-tuner's daily pre-run
    prints the rows it has not closed; SOUL.md 'Ask-gate misses'). -> (rows written, rows already there). The file's
    parent must exist (= bb-tuner runs on this host); otherwise nothing is written and (None, 0) comes back."""
    p = Path(path).expanduser()
    if not p.parent.is_dir():
        return None, 0
    have = set()
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                have.add(json.loads(line)["id"])
            except Exception:
                pass
    new = []
    for h in hits:
        rid = f"askgate-bb:{h['session']}:{int(h['t'])}"
        if rid in have:
            continue
        have.add(rid)
        new.append({"id": rid, "t": int(h["t"]), "when": fmt_ts(h["t"]), "week": f"{week_start(h['t']):%Y-%m-%d}",
                    "host": h.get("host"), "session": h["session"], "pattern": h["pattern"], "quote": quote(h, 400)})
    if new:
        with open(p, "a") as fh:
            for r in new:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(new), len(hits) - len(new)


REVIEW_PROMPT = ("Review this week's ask-gate KPI. Fix requests (had_to_ask_fix): for each, check whether the self-heal "
                 "lane filed it (a `selfheal:` card or Inbox proposal); if not, find why the detector missed it and fix "
                 "the detector. Ask-me corrections: find the root cause and fix the ask-gate or the skill that should "
                 "have prevented it.")


def deliver(hits, week, extra, rest, n_fix, n_bb, n_ask, run=subprocess.run):
    """The weekly report is status, not a decision for David: it never goes to the Inbox. Hits nothing else took
    (``rest``) are agent work, so they become ONE Kanban review card per week (idempotent); with no such hits the
    report only lands in the marker/baseline files. Returns the command run, or None."""
    if not rest:
        return None
    cmd = ["hermes", "kanban", "create",
           f"Ask-gate review: {len(rest)} unrouted hit{'s' if len(rest) != 1 else ''}, week of {week:%b %-d} "
           f"({n_fix} fix, {n_bb} Baby Bear, {n_ask} ask-me)",
           "--assignee", "default", "--idempotency-key", f"ask-gate-weekly:{week:%Y-%m-%d}",
           "--created-by", "coach", "--body", REVIEW_PROMPT + "\n\n" + post_body(hits, week, extra)]
    r = run(cmd, capture_output=True, text=True)
    sys.stderr.write(r.stdout + r.stderr)
    if r.returncode:
        sys.exit(r.returncode)
    return cmd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="~/code/hermes-coach-data")
    ap.add_argument("--weeks", type=int, default=6, help="number of weeks ending now (current week is partial)")
    ap.add_argument("--last-week", action="store_true", help="only the previous full Mon-Sun week")
    ap.add_argument("--baseline", help="write the markdown report here")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--post", action="store_true", help="post to the Hermes Inbox (once per week across hosts)")
    ap.add_argument("--selfheal", action="store_true", help="append the self-heal lane section to the Inbox post")
    ap.add_argument("--home", action="append", default=[], help="Hermes home(s) for --selfheal (default ~/.hermes)")
    ap.add_argument("--pull", action="store_true", help="git pull the data repo first")
    ap.add_argument("--bb-handoff", default=BB_HANDOFF,
                    help="bb-tuner's handoff file for Baby Bear fix requests (written with --post when its dir exists)")
    a = ap.parse_args()
    data = Path(a.data).expanduser()
    if a.pull:
        subprocess.run(["git", "-C", str(data), "pull", "-q"], check=False)

    cur = week_start(time.time())
    if a.last_week:
        weeks = [cur - timedelta(days=7)]
        until = cur.timestamp()
    else:
        weeks = [cur - timedelta(days=7 * i) for i in range(a.weeks - 1, -1, -1)]
        until = time.time() + 1
    hits, scanned = collect(data, weeks[0].timestamp(), until)

    if a.last_week:
        title = f"Ask-gate and had-to-ask-for-a-fix, week of {weeks[0]:%Y-%m-%d}"
    else:
        title = f"Ask-gate and had-to-ask-for-a-fix baseline: {a.weeks} weeks to {datetime.now(PT):%Y-%m-%d}"
    md = render(hits, weeks, scanned, title)
    if a.json:
        print(json.dumps({"weeks": {f"{w:%Y-%m-%d}": c for w, c in week_counts(hits, weeks).items()},
                          "hits": hits}, indent=1))
    else:
        print(md)
    if a.baseline:
        p = Path(a.baseline).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md + "\n")
    if a.post:
        me = os.uname().nodename.split(".")[0]
        other = posted_elsewhere(data, weeks[0], me)
        if other:
            sys.stderr.write(f"week of {weeks[0]:%Y-%m-%d} already posted by {other}; not posting a second card\n")
            return
        extra = selfheal_lines(data, [Path(h).expanduser() for h in a.home] or None) if a.selfheal else None
        ask, fix, bb = by_class(hits, ASK_CLASS), by_class(hits, FIX_CLASS), by_class(hits, BB_CLASS)
        lane_hits = [h for h in hits if h["class"] != BB_CLASS]  # Baby Bear fix requests go to bb-tuner, not the lane
        rest = lane_hits
        if bb:
            n_new, n_had = hand_to_bb_tuner(bb, a.bb_handoff)
            note = (f"Handed {n_new} Baby Bear fix request{'s' if n_new != 1 else ''} to bb-tuner ({n_had} already "
                    f"there): it checks why BB's own monitoring didn't catch each one first." if n_new is not None else
                    f"bb-tuner handoff {a.bb_handoff} not on this host: the {len(bb)} Baby Bear fix requests need a review.")
            if n_new is None:
                rest = rest + bb
            extra = (extra or []) + ["", note]
        if a.selfheal:  # every other hit goes to the self-heal lane; the card only asks to review what it didn't take
            home = Path(a.home[0] if a.home else os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()
            try:
                owned, rest_lane = route_hits(lane_hits, home)
            except Exception as e:
                owned, rest_lane = [], lane_hits
                sys.stderr.write(f"self-heal routing failed: {e}\n")
            rest = rest_lane + [h for h in rest if h["class"] == BB_CLASS]
            extra = (extra or []) + ["", f"Self-heal lane took {len(owned)} of {len(lane_hits)} Hermes/agent hits (filed or "
                                         "already open)." + (f" {len(rest_lane)} still need a review (Do it)."
                                                             if rest_lane else "")]
        deliver(hits, weeks[0], extra, rest, len(fix), len(bb), len(ask))
        p = marker_path(data, weeks[0])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"posted_by": me, "posted_at": int(time.time()), ASK_CLASS: len(ask), FIX_CLASS: len(fix), BB_CLASS: len(bb),
                                 "fix_sessions": len({h["session"] for h in fix})}) + "\n")


if __name__ == "__main__":
    main()
