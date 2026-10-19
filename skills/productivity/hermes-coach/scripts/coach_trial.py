#!/usr/bin/env python3
"""coach trial lane: the coach's weekly `trial` proposal (the top outside idea with a gap of >= 2 findings) becomes a
Kanban trial card that builds the idea behind a flag and gates it with coach_eval.py (stdlib only).

    coach_trial.py spend [--days 3] [--data DIR] [--no-log]   trial spend per UTC day (card workers + eval runs)
    coach_trial.py gate [--data DIR]                           can a trial card be filed now? (exit 0 yes, 1 no)

Called by `coach_apply.py auto` for every pending `kind: trial` proposal: at most TRIAL_CARDS_PER_DAY card a UTC day,
none while today's lane spend >= TRIAL_USD_DAY. Lane spend = the summed worker-session cost of `coach-trial:<slug>`
cards (charged to the run's UTC start day, logged as lane "trial" rows with "cards") + the coach_eval behavior runs
(their own lane "trial" rows; the replays run in a separate eval home, so they are not in the worker's cost).

Proposal shape (weekly digest, SOUL.md "Trial lane"):
    {"kind": "trial", "target": "<idea-slug>", "title": "...", "evidence": "reports/ideas.md <date> #<n>: ...",
     "change": {"idea": "<ideas.md date #n or url>", "gap": ["<finding id>", ">= 2 distinct"],
                "pattern": "unverified_done | handoff | ... | detector:<set>:<rule>",
                "candidate": "what to build behind the flag", "adoption": "skill_patch | plugin_rule | config | ..."},
     "risk": "low"}

Roots (env): COACH_DATA (~/code/hermes-coach-data), HERMES_HOME (~/.hermes), COACH_KANBAN_BOARD (hermes).
"""
import argparse, datetime as dt, json, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coach_fixer as fx  # noqa: E402

TRIAL_USD_DAY = 10.0
TRIAL_CARDS_PER_DAY = 1
TRIAL_RUNTIME_S = 7200
EVAL_BUDGET_USD = 5.0
MIN_GAP = 2
BEHAVIOR_PATTERNS = ("handoff", "confirm", "unverified_done", "repeat_fix", "unwatched")
HOME = Path.home()


def validate_trial(p):
    errs, ch = [], p.get("change") if isinstance(p.get("change"), dict) else {}
    if not fx.SLUG.match(str(p.get("target") or "")):
        errs.append("trial target must be an idea slug (lowercase-hyphenated, 3-61 chars)")
    if not str(ch.get("idea") or "").strip():
        errs.append("trial change needs idea (the ideas.md entry or URL)")
    gap = ch.get("gap")
    if not (isinstance(gap, list) and len({str(g) for g in gap}) >= MIN_GAP):
        errs.append(f"trial change needs gap: [>= {MIN_GAP} distinct finding ids] (an idea without a gap isn't trialed)")
    pat = str(ch.get("pattern") or "")
    if not (pat in BEHAVIOR_PATTERNS or (pat.startswith("detector:") and len(pat.split(":")) == 3)):
        errs.append(f"trial change needs pattern: one of {BEHAVIOR_PATTERNS} or detector:<set>:<rule>")
    if not str(ch.get("candidate") or "").strip():
        errs.append("trial change needs candidate (what to build behind the flag)")
    return errs


def lane_spend(home, board, data, day, by_day=None):
    """(card usd, eval usd) for a UTC day. Card spend is read live from the board; eval spend from spend.jsonl."""
    by_day = by_day or fx.spend_by_day(home, board, 1, lane="trial")
    cards = by_day.get(day, {}).get("usd", 0.0)
    ev = 0.0
    f = fx.spend_log_path(data)
    if f.exists():
        for line in f.read_text().splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("lane") == "trial" and r.get("date") == day and "cards" not in r:
                ev += float(r.get("usd") or 0)
    return round(cards, 4), round(ev, 4)


def trial_cards_filed(home, board, day):
    kdb = fx.kanban_db(home, board)
    if not kdb.exists():
        return []
    lo = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
    with fx._ro(kdb) as k:
        return [r[0] for r in k.execute("select id from tasks where idempotency_key like 'coach-trial:%' "
                                        "and created_at >= ? and created_at < ?", (lo, lo + 86400))]


def existing_card(home, board, slug):
    kdb = fx.kanban_db(home, board)
    if not kdb.exists():
        return None
    with fx._ro(kdb) as k:
        r = k.execute("select id, status from tasks where idempotency_key = ? order by created_at desc limit 1",
                      (f"coach-trial:{slug}",)).fetchone()
    return {"id": r[0], "status": r[1]} if r else None


def gate(home, board, data, now=None, by_day=None):
    """(ok, reason, today_usd)."""
    now = now or time.time()
    day = fx.utc_day(now)
    if by_day is None:
        by_day = fx.spend_by_day(home, board, 1, now, lane="trial")
    cards, ev = lane_spend(home, board, data, day, by_day)
    usd = round(cards + ev, 4)
    if usd >= TRIAL_USD_DAY:
        return False, f"trial lane spend today ${usd:.2f} >= ${TRIAL_USD_DAY:.0f} cap", usd
    filed = trial_cards_filed(home, board, day)
    if len(filed) >= TRIAL_CARDS_PER_DAY:
        return False, f"trial lane already filed {', '.join(filed)} today (cap {TRIAL_CARDS_PER_DAY}/day)", usd
    return True, "", usd


def card_title(p):
    return f"Trial: {p.get('title') or p['target']}"[:120]


def card_body(p):
    ch = p["change"]
    pat = ch["pattern"]
    gap = "\n".join(f"- {g}" for g in ch["gap"])
    if pat.startswith("detector:"):
        _, st, rule = pat.split(":")
        evalcmd = (f"coach_eval.py detector --set {st} --rule {rule} --candidate '<th=..., detect=<COACH_DETECT_TAG "
                   f"file>, module=<candidate .py>>' --out <workspace>/eval-report.md")
    else:
        evalcmd = (f"coach_eval.py behavior --candidate <workspace>/candidate.json --target {pat} "
                   f"--budget {EVAL_BUDGET_USD:.0f} --out <workspace>/eval-report.md")
    return (f"Trial lane card from the coach Bot ({p.get('report')}, proposal {p['id']}). Idea: {ch['idea']}\n\n"
            f"## Evidence\n{p.get('evidence')}\n\n## Gap (findings it should fix)\n{gap}\n\n"
            f"## Candidate\n{ch['candidate']}\n\n"
            "## Steps\n"
            "1. Already in Hermes? grep the Hermes checkout and docs llms.txt first; an existing feature becomes a "
            "curriculum proposal instead of a build (close the card with that).\n"
            "2. Build the change BEHIND A FLAG: nothing live changes until the eval passes. Instruction changes "
            "(AGENTS.md / SOUL.md / a skill / memory) are a coach_eval candidate file (`{\"name\", \"target\", "
            "\"edits\": [{\"file\", \"append\"} | {\"file\", \"old_string\", \"new_string\"}]}`); code goes on a branch "
            "or behind an env/config flag that defaults off; detector changes are a COACH_DETECT_TAG run + threshold.\n"
            f"3. Run the eval (scripts in the hermes-coach skill): `{evalcmd}`. Keep the eval at or under "
            f"${EVAL_BUDGET_USD:.0f}; it logs its spend to research/state/spend.jsonl (lane trial).\n"
            "4. Attach the report (kanban_attach eval-report.md + .json) and put the verdict in the summary.\n"
            "5. PASS -> register ONE coach-proposal for the real rollout (skill_patch / plugin_rule / config / "
            "curriculum ...; `coach_proposals.py add` assigns the tier: auto / canary / review), quoting the eval "
            "numbers in its evidence, push the data repo. REJECT -> no proposal; write what the eval showed into the "
            "card summary so the Bot doesn't re-propose it.\n\n"
            f"Budget: this card runs at most {TRIAL_RUNTIME_S // 60} min; trial lane cap ${TRIAL_USD_DAY:.0f}/day "
            f"(cards + evals). Finish with kanban_request_review.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="coach_trial.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("spend", "gate"):
        s = sub.add_parser(name)
        s.add_argument("--data", default=os.environ.get("COACH_DATA") or str(HOME / "code/hermes-coach-data"))
        s.add_argument("--home", default=os.environ.get("HERMES_HOME") or str(HOME / ".hermes"))
        s.add_argument("--board", default=os.environ.get("COACH_KANBAN_BOARD") or "hermes")
        if name == "spend":
            s.add_argument("--days", type=int, default=3)
            s.add_argument("--no-log", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "spend":
        by_day = fx.spend_by_day(a.home, a.board, a.days, lane="trial")
        for day, d in sorted(by_day.items()):
            cards, ev = lane_spend(a.home, a.board, a.data, day, by_day)
            print(f"{day}  trial cards ${cards:.2f} + evals ${ev:.2f}  {d['runs']} run(s)  {json.dumps(d['cards'])}")
        if not a.no_log:
            print(f"logged to {fx.log_spend(a.data, by_day, lane='trial')}")
        return 0
    ok, why, usd = gate(a.home, a.board, a.data)
    print(f"trial gate: {'open' if ok else 'closed'} (today ${usd:.2f}/${TRIAL_USD_DAY:.0f}){': ' + why if why else ''}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
