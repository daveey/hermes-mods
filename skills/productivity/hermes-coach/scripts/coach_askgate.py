#!/usr/bin/env python3
"""Ask-gate metric (stdlib only): two weekly KPIs from the cross-host coach digests, both target 0.
  ask_me          David's corrections of the form "why are you asking me / I already did it / do it yourself".
  had_to_ask_fix  David asking for a Hermes defect to be fixed or root-caused ("make a card to fix", "root cause",
                  "figure out why", "fixed this once / broken again", "why did you not…", a screenshot + "fix"):
                  the work the self-heal lane should have started unasked.

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
ASK_CLASS, FIX_CLASS = "ask_me", "had_to_ask_fix"
CLASS_LABEL = {ASK_CLASS: "Ask-me corrections (\"why are you asking me / I already did it / do it yourself\")",
               FIX_CLASS: "Had to ask for a fix (\"make a card to fix / root cause / figure out why / broken again\")"}
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
FIX_NEGATIVE = re.compile(r"^\s*(?:work kanban task|Investigate this [\w-]+ cron output|Review (?:these|this week)|"
                          r"Self-heal|Context carried over|\{)|\*\*\w|\bno need to (?:root|fix)\b|[\"'“]still broken[\"'”]",
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
        st = d.get("started_at")
        if st and (first_ts is None or st < first_ts):
            first_ts = st
        for i, u in enumerate(d.get("user_turns") or []):
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
        out[w] = {ASK_CLASS: len(a), FIX_CLASS: len(f), "fix_sessions": len({h["session"] for h in f})}
    return out


def render(hits, weeks, scanned, title):
    counts = week_counts(hits, weeks)
    out = [f"# {title}", "",
           f"Generated {fmt_ts(time.time())}. Detector: skills/productivity/hermes-coach/scripts/coach_askgate.py "
           f"(regex over cross-host coach digests). Two classes, target 0 each: `{ASK_CLASS}` once the ask-gate ships, "
           f"`{FIX_CLASS}` once the self-heal lane is live. A turn counts in one class only (ask-me wins); fix requests "
           "are counted in interactive chats only.", "",
           "| Week (Mon, PT) | Ask-me | Had to ask for a fix (sessions) | User turns scanned |", "|---|---|---|---|"]
    now = datetime.now(PT)
    for w in weeks:
        tag = " (partial)" if w + timedelta(days=7) > now else ""
        n, c = scanned.get(w, 0), counts[w]
        fix_cell = f"{c[FIX_CLASS]} ({c['fix_sessions']})" if n else "n/a"
        out.append(f"| {w:%Y-%m-%d}{tag} | {c[ASK_CLASS] if n else 'n/a'} | {fix_cell} | {n} |")
    fx = by_class(hits, FIX_CLASS)
    out.append(f"| **Total** | **{len(by_class(hits, ASK_CLASS))}** | **{len(fx)}** ({len({h['session'] for h in fx})}) | "
               f"{sum(scanned.values())} |")
    out.append("")
    for cls in (FIX_CLASS, ASK_CLASS):
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
    return m.get("host") if m.get("host") and m.get("host") != me else None


def post_body(hits, week, extra=None):
    ask, fix = by_class(hits, ASK_CLASS), by_class(hits, FIX_CLASS)
    fs = len({h["session"] for h in fix})
    lines = [f"**{len(fix)}** time{'s' if len(fix) != 1 else ''} David had to ask for a Hermes fix (\"make a card to fix / "
             f"root cause / figure out why / broken again\") in {fs} session{'s' if fs != 1 else ''}, and "
             f"**{len(ask)}** correction{'s' if len(ask) != 1 else ''} where he had to say \"why are you asking me / I "
             f"already did it / do it yourself\", in the week of {week:%Y-%m-%d}. Target: 0 for both.", ""]
    for cls, hs in ((FIX_CLASS, fix), (ASK_CLASS, ask)):
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
        ask, fix = by_class(hits, ASK_CLASS), by_class(hits, FIX_CLASS)
        cmd = ["hermes", "inbox", "post",
               f"Ask-gate: {len(fix)} fix request{'s' if len(fix) != 1 else ''}, {len(ask)} ask-me, "
               f"week of {weeks[0]:%b %-d}",
               "--body", post_body(hits, weeks[0], extra), "--key", f"ask-gate-weekly:{weeks[0]:%Y-%m-%d}",
               "--poster", "coach", "--prompt",
               "Review this week's ask-gate KPI. Fix requests (had_to_ask_fix): for each, check whether the self-heal "
               "lane filed it (a `selfheal:` card or Inbox proposal); if not, find why the detector missed it and fix "
               "the detector. Ask-me corrections: find the root cause and fix the ask-gate or the skill that should "
               "have prevented it.",
               "--prompt-label", "Review this week's ask-gate KPI"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        sys.stderr.write(r.stdout + r.stderr)
        if r.returncode:
            sys.exit(r.returncode)
        p = marker_path(data, weeks[0])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"host": me, "posted_at": int(time.time()), ASK_CLASS: len(ask), FIX_CLASS: len(fix),
                                 "fix_sessions": len({h["session"] for h in fix})}) + "\n")


if __name__ == "__main__":
    main()
