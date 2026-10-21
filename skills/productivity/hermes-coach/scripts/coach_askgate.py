#!/usr/bin/env python3
"""Ask-gate metric (stdlib only): David's corrections of the form "why are you asking me / I already did it /
do it yourself", counted per week from the cross-host coach digests.

    coach_askgate.py --data ~/code/hermes-coach-data --weeks 6 --baseline OUT.md     # baseline, 6 weeks
    coach_askgate.py --data ... --last-week --post                                    # Monday: previous Mon-Sun, to the Inbox
    coach_askgate.py --data ... --weeks 6 --json                                      # machine-readable

A hit is a real user turn (digests already drop Hermes-written user-role messages; Slack-engagement batches, probe
prompts and Kanban notices are skipped here too) matching a correction pattern: the agent asked David for something
it should have checked or done itself. Regex precision is imperfect by design (see references/labeling.md): hits are
listed with session + quote so a human can eyeball them, and a miss on the sampled turns (digests keep the first 10 and
last 50 user turns per session) is possible. Weeks run Monday 00:00 PT to Monday 00:00 PT. Hits seen on several hosts
(forked or mirrored sessions) count once.
Target after the ask-gate ships: 0.
"""
import argparse, glob, json, re, subprocess, sys, time
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
            kind = None if opener_popup else classify(text)
            if not kind:
                continue
            key = (d.get("id"), int(t), text[:80])
            if key in seen:
                continue
            seen.add(key)
            hits.append({"t": t, "session": d.get("id"), "host": d.get("host"), "source": d.get("source"),
                         "pattern": kind, "quote": text})
    hits.sort(key=lambda h: h["t"])
    return hits, scanned


def fmt_ts(t):
    return datetime.fromtimestamp(t, PT).strftime("%Y-%m-%d %H:%M %Z")


def quote(h, n=240):
    q = re.sub(r"\s+", " ", re.sub(r"\[Image attached[^\]]*\]", "", h["quote"])).strip()
    return q if len(q) <= n else q[:n] + "…"


def render(hits, weeks, scanned, title):
    by = {w: [] for w in weeks}
    for h in hits:
        by[week_start(h["t"])].append(h)
    out = [f"# {title}", "",
           f"Generated {fmt_ts(time.time())}. Detector: skills/productivity/hermes-coach/scripts/coach_askgate.py "
           f"(regex over cross-host coach digests; target after ask-gate ships: 0).", "",
           "| Week (Mon, PT) | Hits | User turns scanned |", "|---|---|---|"]
    now = datetime.now(PT)
    for w in weeks:
        tag = " (partial)" if w + timedelta(days=7) > now else ""
        n = scanned.get(w, 0)
        out.append(f"| {w:%Y-%m-%d}{tag} | {len(by[w]) if n else 'n/a'} | {n} |")
    out.append(f"| **Total** | **{len(hits)}** | {sum(scanned.values())} |")
    out.append("")
    for w in weeks:
        out.append(f"## Week of {w:%Y-%m-%d}: {len(by[w]) if scanned.get(w) else 'n/a (no digest coverage)'}")
        out.append("")
        for h in by[w]:
            out.append(f"- {fmt_ts(h['t'])} · {h['host']} · session {h['session']} · {h['pattern']}: \"{quote(h)}\"")
        if not by[w]:
            out.append("- none" if scanned.get(w) else "- no data: no host kept sessions from this week")
        out.append("")
    out.append("Digests keep the first 10 and last 50 user turns per session, so a hit in the middle of a very long session "
               "can be missed; 'n/a' weeks predate every host's retained sessions.")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="~/code/hermes-coach-data")
    ap.add_argument("--weeks", type=int, default=6, help="number of weeks ending now (current week is partial)")
    ap.add_argument("--last-week", action="store_true", help="only the previous full Mon-Sun week")
    ap.add_argument("--baseline", help="write the markdown report here")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--post", action="store_true", help="post to the Hermes Inbox")
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
        title = f"Ask-gate corrections, week of {weeks[0]:%Y-%m-%d}"
    else:
        title = f"Ask-gate baseline: {a.weeks} weeks to {datetime.now(PT):%Y-%m-%d}"
    md = render(hits, weeks, scanned, title)
    if a.json:
        print(json.dumps({"weeks": {f"{w:%Y-%m-%d}": sum(1 for h in hits if week_start(h['t']) == w) for w in weeks},
                          "hits": hits}, indent=1))
    else:
        print(md)
    if a.baseline:
        p = Path(a.baseline).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md + "\n")
    if a.post:
        n = len(hits)
        lines = [f"**{n}** correction{'s' if n != 1 else ''} where David had to say \"why are you asking me / I already did it / do it yourself\" "
                 f"in the week of {weeks[0]:%Y-%m-%d}. Target: 0.", ""]
        for h in hits[:20]:
            lines.append(f"- {fmt_ts(h['t'])} · session `{h['session']}`: \"{quote(h, 160)}\"")
        if n > 20:
            lines.append(f"- … and {n - 20} more")
        cmd = ["hermes", "inbox", "post", f"Ask-gate: {n} ask-me corrections, week of {weeks[0]:%b %-d}",
               "--body", "\n".join(lines), "--key", f"ask-gate-weekly:{weeks[0]:%Y-%m-%d}",
               "--poster", "coach", "--prompt",
               "Review these ask-gate corrections (agent asked David for something it should have checked or done itself). "
               "For each, find the root cause and fix the ask-gate or the skill that should have prevented it.",
               "--prompt-label", "Review this week's ask-gate corrections"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        sys.stderr.write(r.stdout + r.stderr)
        if r.returncode:
            sys.exit(r.returncode)


if __name__ == "__main__":
    main()
