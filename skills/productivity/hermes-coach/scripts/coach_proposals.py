#!/usr/bin/env python3
"""Coach proposals: parse, dedupe and track the weekly digest's proposed changes (stdlib only).

    coach_proposals.py add      --data DIR --report reports/weekly-<date>.md   # register the report's proposals
    coach_proposals.py list     --data DIR [--status pending|applied|rejected|all] [--json]
    coach_proposals.py rejected --data DIR                                    # the never-propose-again list
    coach_proposals.py mark     --data DIR --id ID --status applied|rejected [--note TEXT] [--by WHO]
    coach_proposals.py outcomes --data DIR [--limit 6]   # last card outcomes / tried / watch (prompt block)

A proposal is a fenced block in the weekly report:

    ```coach-proposal
    {"kind": "config", "target": "agent.max_turns", "change": {"value": 150},
     "evidence": "macbook/default/<id> — <title>: ...", "risk": "low", "title": "...",
     "short": "Park cards on an outside blocker",
     "effect": "When a chat mentions a billing or quota failure, you get a tip to park the affected cards.",
     "before": "93 Kanban runs retried the same GitHub billing error ($460)."}
    ```

short / effect / before are the user-facing copy of the Inbox card (optional, but write them): `short` is the
row title (<= 45 chars, plain words, no tool names when avoidable), `effect` one sentence on what is different FOR
THE USER now (not what the coach did), `before` one sentence on the problem with one number from the evidence.

kind is one of skill_patch | config | shared_config | curriculum | plugin_rule | core | fix | trial. The `change` shape per kind:
  config / shared_config  {"value": <json value>}
  skill_patch             {"old_string": "...", "new_string": "...", "file_path": "SKILL.md"}   (target = skill name)
  curriculum/plugin_rule  {"old_string": "...", "new_string": "..."} or {"append": "..."}       (target = file path)
  core                    {"summary": "..."}  (target = area; never applied by a script, always a Kanban card)
  fix                     {"sessions": [">=2 session ids"], "hypothesis": "...", "acceptance": ["..."], "repro": "...",
                           "area": "plugin:coach | core | skill:<n>"}  (target = pattern slug; the fixer lane:
                           coach_fixer.py files one `coach-fix:<slug>` card a day while the lane is under $7/day)
  trial                   {"idea": "ideas.md <date> #n", "gap": [">=2 finding ids"], "pattern": "unverified_done | ... |
                           detector:<set>:<rule>", "candidate": "what to build behind the flag"}  (target = idea slug;
                           the trial lane: coach_trial.py files one `coach-trial:<slug>` card a day (goal mode, 2 h)
                           while the lane is under $10/day; the card gates the idea with coach_eval.py)

Rollout tier (sets the autonomy; `tier` in the block may only RAISE the default from `default_tier`):
  auto    curriculum tips, skill patches, coach.* detector thresholds  -> `coach-apply auto` applies them when the
          gate passes and the weekly digest lists each with `coach-apply revert <id>`
  canary  config / shared_config / plugin_rule  -> applied on the MacBook only, 7-day before/after check (`check`),
          promoted to shared-config on KEEP, reverted on REVERT
  review  core, hermes-agent files, model/provider keys, `paid: true`, `cost_usd_day` > REVIEW_COST_USD_DAY,
          risk high  -> a card on the `hermes` Kanban board instead of an apply

reports/proposals.jsonl is an append-only event log: one `status: pending` line per new proposal, then one
line per decision (applied | canary | carded | rejected | dropped | reverted). The current state of a proposal is its last line. A proposal whose
fingerprint (kind + target + change) was ever rejected is dropped by `add`, so it is never proposed again.
reports/suppressions.jsonl holds the Inbox card decisions (`coach-improvement` type, written by coach_apply):
Not now = no proposal for that kind + target for 14 days, Never = no proposal for it ever. `add` honours both.
A metadata-only line {"id", "date", "card_outcome": {card, board, card_status, run_id, run_status, summary, evidence,
completed_at}} records what the proposal's linked card ended with (written by `coach-apply auto` when the card is
done/archived); it never carries `status`, so a proposal's decision is only ever the explicit one.
"""
import argparse, calendar, contextlib, hashlib, json, os, re, sys, time
from pathlib import Path

KINDS = ("skill_patch", "config", "shared_config", "curriculum", "plugin_rule", "core", "fix", "trial")
TIERS = ("auto", "canary", "review")
STATUSES = ("pending", "applied", "canary", "carded", "rejected", "dropped", "reverted")
STALE_DAYS = 7
REVIEW_COST_USD_DAY = 2.0
IDENTITY = ("id", "fp", "date", "report", "kind", "target", "change", "evidence", "title", "risk", "feature")
REVIEW_KEYS = re.compile(r"^(model|provider|providers|fallback|auxiliary|delegation\.(model|provider)|"
                         r"custom_providers|credential)", re.I)
AUTO_KEYS = re.compile(r"^(coach|plugins\.coach)\.|threshold", re.I)
BLOCK = re.compile(r"```coach-proposal\s*\n(.*?)\n```", re.S)


def fingerprint(p):
    raw = json.dumps([p.get("kind"), p.get("target"), p.get("change")], sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def tier_rank(t):
    return TIERS.index(t) if t in TIERS else 0


def default_tier(p):
    """Lowest tier a proposal may run at, from its kind/target/cost/risk."""
    kind, tgt = p.get("kind"), str(p.get("target") or "")
    if (kind in ("core", "fix", "trial") or "hermes-agent" in tgt or p.get("paid")
            or float(p.get("cost_usd_day") or 0) > REVIEW_COST_USD_DAY or p.get("risk") == "high"):
        return "review"
    if kind in ("config", "shared_config"):
        if REVIEW_KEYS.search(tgt):
            return "review"
        return "auto" if AUTO_KEYS.search(tgt) else "canary"
    if kind == "plugin_rule":
        from coach_yield import is_source_rule
        return "auto" if is_source_rule(p) else "canary"
    return "auto"  # curriculum, skill_patch


def effective_tier(p):
    """The Bot may raise the tier, never lower it."""
    d, asked = default_tier(p), p.get("tier")
    return asked if asked in TIERS and tier_rank(asked) > tier_rank(d) else d


def age_days(p, now=None):
    try:
        t = calendar.timegm(time.strptime(p.get("date", "")[:10], "%Y-%m-%d"))
    except ValueError:
        return 0.0
    return ((now or time.time()) - t) / 86400


def log_path(data):
    return Path(data) / "reports" / "proposals.jsonl"


def events(data):
    f = log_path(data)
    if not f.exists():
        return []
    out = []
    for line in f.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def state(data):
    """id -> proposal dict merged with its latest decision."""
    cur = {}
    for e in events(data):
        pid = e.get("id")
        if not pid:
            continue
        if pid in cur:
            cur[pid].update({k: v for k, v in e.items() if k not in IDENTITY})
        else:
            cur[pid] = dict(e)
    for p in cur.values():  # rows registered before tiers existed get the default
        p.setdefault("tier", effective_tier(p))
    return cur


def rejected(data):
    return [p for p in state(data).values() if p.get("status") == "rejected"]


def suppress_path(data):
    return Path(data) / "reports" / "suppressions.jsonl"


def suppressions(data):
    f = suppress_path(data)
    out = []
    for line in (f.read_text().splitlines() if f.exists() else []):
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def add_suppression(data, p, mode, days=None, now=None, source="inbox"):
    """Record a Not-now (days) or Never (until null) decision for a proposal's kind + target."""
    now = now or time.time()
    rec = {"kind": p.get("kind"), "target": p.get("target"), "mode": mode, "id": p.get("id"), "source": source,
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
           "until": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + days * 86400)) if days else None}
    f = suppress_path(data)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


def suppressed(data, p, now=None):
    """The suppression row that blocks a proposal of the same kind + target (never, or not-now still running).
    A later ``clear`` row for that kind + target lifts everything before it."""
    now = now or time.time()
    rows = [r for r in suppressions(data) if r.get("kind") == p.get("kind") and r.get("target") == p.get("target")]
    for i in range(len(rows) - 1, -1, -1):
        if rows[i].get("mode") == "clear":
            rows = rows[i + 1:]
            break
    for r in rows:
        if not r.get("until"):
            return r
        try:
            if calendar.timegm(time.strptime(r["until"], "%Y-%m-%dT%H:%M:%SZ")) > now:
                return r
        except ValueError:
            continue
    return None


def append(data, rec):
    f = log_path(data)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── Card outcomes: neutral memory of what a proposal's linked Kanban card ended with ──
# `coach-apply auto` reads the card (read-only, on its own explicit board) once it is done or archived and appends
# one metadata-only row {"id", "date", "card_outcome": {...}}: no `status`/`at`/`by`, so the proposal's explicit
# decision (carded / applied / rejected / ...) is never changed or inferred from the card. A row is appended only when
# the snapshot differs from the one already recorded, so reruns are no-ops.
OUTCOME_TERMINAL = ("done", "archived")
OUTCOME_SUMMARY_MAX = 280
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def board_db(home, board, environ=None):
    """The explicit board's own kanban.db (core `board_own_db_path`): `<root>/kanban.db` for `default`, else
    `<root>/kanban/boards/<board>/kanban.db`. root = HERMES_KANBAN_HOME, else the Hermes root (a profile home's
    root is two levels up: boards are shared across profiles). Worker pins (HERMES_KANBAN_DB) are ignored on purpose:
    the board is explicit, and a missing board DB means no outcomes, never another board's."""
    e = os.environ if environ is None else environ
    board = str(board or "").strip().lower()
    if not _SLUG.match(board):
        raise ValueError(f"bad board slug {board!r}")
    root = Path(e.get("HERMES_KANBAN_HOME") or home).expanduser()
    if not e.get("HERMES_KANBAN_HOME") and root.parent.name == "profiles":
        root = root.parent.parent
    return root / "kanban.db" if board == "default" else root / "kanban" / "boards" / board / "kanban.db"


def _ro_db(path):
    import sqlite3
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        c.execute("select 1 from sqlite_master limit 1").fetchall()
        return c
    except sqlite3.OperationalError:  # WAL db without -shm: query_only on a normal open, never writes rows
        c = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=10)
        c.execute("pragma query_only = on")
        return c


def _iso(ts):
    if ts in (None, ""):
        return None
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return None
    ts = ts / 1000 if ts > 1e11 else ts  # some writers store ms
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _bound(text, limit=OUTCOME_SUMMARY_MAX):
    t = " ".join(str(text or "").split())
    t = re.sub(r"(gh[pousr]_|github_pat_|sk-[a-z]*-?)[A-Za-z0-9_-]{8,}", "<token>", t)
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"


def card_outcome(kdb, board, card):
    """Snapshot of a done/archived card on `kdb`, else None (absent from this board, or not terminal yet).
    The run is the latest completed run with a non-empty summary; without one the latest run (any outcome) is named
    and `summary` is None with `evidence: unknown`. Nothing is inferred from the summary."""
    with contextlib.closing(_ro_db(kdb)) as k:
        t = k.execute("select id, status, completed_at from tasks where id = ?", (card,)).fetchone()
        if not t or t[1] not in OUTCOME_TERMINAL:
            return None
        runs = k.execute("select id, status, outcome, summary, ended_at from task_runs where task_id = ? "
                         "order by id desc", (card,)).fetchall()
    real = [r for r in runs if r[2] == "completed" and str(r[3] or "").strip()]
    run = real[0] if real else (runs[0] if runs else None)
    return {"card": card, "board": board, "card_status": t[1],
            "run_id": run[0] if run else None,
            "run_status": (run[2] or run[1]) if run else None,
            "summary": _bound(run[3]) if real else None,
            "evidence": "run_summary" if real else "unknown",
            "completed_at": _iso(t[2]) or (_iso(run[4]) if run else None)}


def reconcile_outcomes(data, home, board, dry=False, environ=None):
    """Record the card outcome of every proposal whose linked card on `board` is done/archived. Returns
    [(id, snapshot)] for new/changed snapshots; appends them unless dry. Never touches `status`."""
    kdb = board_db(home, board, environ)
    if not kdb.exists():
        return []
    out = []
    for p in state(data).values():
        card = p.get("card")
        if not (isinstance(card, str) and re.match(r"^t_[0-9a-f]{6,}$", card)):
            continue
        snap = card_outcome(kdb, board, card)
        if not snap or snap == p.get("card_outcome"):
            continue
        out.append((p["id"], snap))
        if not dry:  # `date` is identity (ignored by state()); it keeps date-windowed readers (coach_yield) whole
            append(data, {"id": p["id"], **({"date": p["date"]} if p.get("date") else {}), "card_outcome": snap})
    return out


def render_outcomes(data, limit=6):
    """Bounded prompt block: the last card outcomes, what was tried and decided, and the watch list (a terminal
    card whose proposal still has no explicit decision). Status is always the proposal's own."""
    rows = [p for p in state(data).values()
            if isinstance(p.get("card_outcome"), dict) and p["card_outcome"].get("card") == p.get("card")]
    rows.sort(key=lambda p: (p["card_outcome"].get("completed_at") or "", p["id"]), reverse=True)

    def line(p):
        o = p["card_outcome"]
        s = f'"{o["summary"]}"' if o.get("summary") else "summary unknown"
        return (f"- {p['id']} {p.get('kind')} {p.get('target')} [{p.get('status')}]: card {o['card']} "
                f"{o.get('card_status')} {str(o.get('completed_at') or 'date unknown')[:10]}, run "
                f"{o.get('run_id') if o.get('run_id') is not None else 'unknown'} {o.get('run_status') or ''}"
                f" — {s}").replace("  ", " ")

    def capped(xs, fmt):
        return [fmt(p) for p in xs[:limit]] + ([f"- (+{len(xs) - limit} more)"] if len(xs) > limit else []) \
            or ["- none"]

    watch = [p for p in rows if p.get("status") in ("carded", "pending")]
    tried = [p for p in rows if p not in watch]
    return (["## Card outcomes (linked Kanban cards; [status] is the proposal's own decision, never inferred)", "",
             f"Last outcomes ({len(rows)}):"] + capped(rows, line)
            + ["", f"Tried and decided ({len(tried)}):"]
            + capped(tried, lambda p: f"- {p.get('kind')} {p.get('target')} -> {p.get('status')} "
                                      f"(card {p['card_outcome']['card']} {p['card_outcome'].get('card_status')})")
            + ["", f"Watch: card closed, proposal still undecided ({len(watch)}):"]
            + capped(watch, line))


def validate(p):
    errs = []
    if p.get("kind") not in KINDS:
        errs.append(f"kind must be one of {KINDS}")
    for k in ("target", "change", "evidence"):
        if not p.get(k):
            errs.append(f"missing {k}")
    ch = p.get("change") if isinstance(p.get("change"), dict) else {}
    if p.get("change") is not None and not isinstance(p.get("change"), dict):
        errs.append("change must be an object")
    if p.get("tier") is not None and p.get("tier") not in TIERS:
        errs.append(f"tier must be one of {TIERS}")
    kind = p.get("kind")
    if kind in ("config", "shared_config") and "value" not in ch:
        errs.append("config change needs {value}")
    if kind == "skill_patch" and not ("old_string" in ch and "new_string" in ch):
        errs.append("skill_patch change needs {old_string, new_string}")
    if kind in ("curriculum", "plugin_rule") and not ("append" in ch or ("old_string" in ch and "new_string" in ch)):
        errs.append(f"{kind} change needs {{append}} or {{old_string, new_string}}")
    if kind == "fix":
        from coach_fixer import validate_fix
        errs += validate_fix(p)
    if kind == "trial":
        from coach_trial import validate_trial
        errs += validate_trial(p)
    return errs


def parse_report(text):
    out, bad = [], []
    for i, m in enumerate(BLOCK.finditer(text), 1):
        try:
            out.append(json.loads(m.group(1)))
        except Exception as e:
            bad.append(f"block {i}: invalid JSON ({e})")
    return out, bad


def cmd_add(a):
    report = Path(a.report)
    if not report.is_absolute():
        report = Path(a.data) / report
    report = report.resolve()
    props, bad = parse_report(report.read_text())
    m = re.search(r"(\d{4}-\d{2}-\d{2})", report.name)
    date = m.group(1) if m else time.strftime("%Y-%m-%d")
    st = state(a.data)
    seen = {p.get("fp"): p for p in st.values()}
    n = sum(1 for p in st.values() if p["id"].startswith(date + "-"))
    msgs = list(bad)
    for p in props:
        errs = validate(p)
        if errs:
            msgs.append(f"skipped {p.get('kind')}:{p.get('target')}: " + "; ".join(errs))
            continue
        fp = fingerprint(p)
        dup = next((q for q in st.values() if p["kind"] in ("fix", "trial") and q.get("kind") == p["kind"]
                    and q.get("target") == p["target"] and q.get("status") in ("pending", "carded")), None)
        if dup:
            msgs.append(f"skipped {p['kind']}:{p['target']}: slug already {dup.get('status')} as {dup['id']}")
            continue
        sup = suppressed(a.data, p)
        if sup:
            msgs.append(f"skipped {p['kind']}:{p['target']}: {sup['mode'].replace('_', ' ')} from the Inbox"
                        + (f" until {sup['until'][:10]}" if sup.get("until") else ""))
            continue
        if fp in seen:
            msgs.append(f"skipped {p['kind']}:{p['target']}: same as {seen[fp]['id']} ({seen[fp].get('status')})")
            continue
        n += 1
        rec = {"id": f"{date}-{n}", "fp": fp, "date": date, "report": os.path.relpath(report, Path(a.data).resolve()),
               "status": "pending", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        rec.update({k: p[k] for k in ("kind", "target", "change", "evidence", "title", "short", "effect", "before",
                                      "risk", "feature", "signal",
                                      "paid", "cost_usd_day", "idea_id", "idea_ids", "trial_id", "source_url") if k in p})
        rec["tier"] = effective_tier(p)
        append(a.data, rec)
        seen[fp] = rec
        st[rec["id"]] = rec
        raised = f" (asked {p['tier']}, default is higher)" if p.get("tier") and p["tier"] != rec["tier"] else ""
        msgs.append(f"added {rec['id']} [{rec['tier']}] {p['kind']}:{p['target']}{raised}")
    print("\n".join(msgs) or "no coach-proposal blocks found")
    return 0 if props else 1


def cmd_list(a):
    rows = [p for p in state(a.data).values() if a.status == "all" or p.get("status") == a.status]
    if a.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
        return 0
    for p in rows:
        print(f"{p['id']:<14} {p.get('status'):<8} {p.get('tier', '?'):<6} {p.get('risk', '?'):<5} {p['kind']:<13} {p['target']}"
              f"  {p.get('title') or ''}")
    if not rows:
        print(f"no {a.status} proposals")
    return 0


def cmd_rejected(a):
    for p in rejected(a.data):
        print(f"- {p['kind']} {p['target']}: {json.dumps(p['change'], ensure_ascii=False)[:200]}"
              f" (rejected {p.get('at', '')[:10]}{': ' + p['note'] if p.get('note') else ''})")
    return 0


def cmd_suppressions(a):
    """List the Not now / Never decisions, or lift one (--clear KIND TARGET)."""
    if a.clear:
        kind, target = a.clear
        add_suppression(a.data, {"kind": kind, "target": target, "id": "manual"}, "clear", source="cli")
        print(f"lifted: {kind}:{target} can be proposed again")
        return 0
    for r in suppressions(a.data):
        if r.get("mode") != "clear" and suppressed(a.data, r):
            print(f"{r['mode']:<8} {r['kind']}:{r['target']}  since {r['at'][:10]}"
                  + (f" until {r['until'][:10]}" if r.get("until") else "") + f"  ({r.get('id')})")
    return 0


def cmd_outcomes(a):
    print("\n".join(render_outcomes(a.data, a.limit)))
    return 0


def cmd_mark(a):
    st = state(a.data)
    if a.id not in st:
        print(f"unknown proposal {a.id}", file=sys.stderr)
        return 2
    rec = {"id": a.id, "status": a.status, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "by": a.by, **({"note": a.note} if a.note else {}), **({"result": a.result} if a.result else {})}
    rec.update({k: v for k, v in (getattr(a, "extra", None) or {}).items() if k not in IDENTITY})
    append(a.data, rec)
    print(f"{a.id} -> {a.status}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("add", "list", "rejected", "mark", "suppressions", "outcomes"):
        s = sub.add_parser(name)
        s.add_argument("--data", required=True)
        if name == "add":
            s.add_argument("--report", required=True)
        if name == "outcomes":
            s.add_argument("--limit", type=int, default=6)
        if name == "suppressions":
            s.add_argument("--clear", nargs=2, metavar=("KIND", "TARGET"))
        if name == "list":
            s.add_argument("--status", default="pending")
            s.add_argument("--json", action="store_true")
        if name == "mark":
            s.add_argument("--id", required=True)
            s.add_argument("--status", required=True, choices=STATUSES)
            s.add_argument("--note", default="")
            s.add_argument("--result", default="")
            s.add_argument("--by", default="coach-apply")
    a = ap.parse_args(argv)
    rc = {"add": cmd_add, "list": cmd_list, "rejected": cmd_rejected, "mark": cmd_mark,
          "suppressions": cmd_suppressions, "outcomes": cmd_outcomes}[a.cmd](a)
    if a.cmd == "mark" and rc == 0 and a.status in ("applied", "reverted"):
        # A card that lands a review-tier proposal marks it here, not through coach-apply: post it to the Inbox too.
        try:
            import coach_apply  # noqa: E402 — lazy: coach_apply imports this module
        except ImportError:  # the cloud Bot ships without coach_apply: the Mac's next apply pass posts it
            return rc
        p = state(a.data).get(a.id)
        if p:
            coach_apply.notify_inbox(coach_apply.Env(data=a.data), p, a.status, a.result or a.note)
    return rc


if __name__ == "__main__":
    sys.exit(main())
