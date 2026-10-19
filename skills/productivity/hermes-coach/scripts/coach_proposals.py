#!/usr/bin/env python3
"""Coach proposals: parse, dedupe and track the weekly digest's proposed changes (stdlib only).

    coach_proposals.py add      --data DIR --report reports/weekly-<date>.md   # register the report's proposals
    coach_proposals.py list     --data DIR [--status pending|applied|rejected|all] [--json]
    coach_proposals.py rejected --data DIR                                    # the never-propose-again list
    coach_proposals.py mark     --data DIR --id ID --status applied|rejected [--note TEXT] [--by WHO]

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
"""
import argparse, calendar, hashlib, json, os, re, sys, time
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


def append(data, rec):
    f = log_path(data)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


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
    for name in ("add", "list", "rejected", "mark"):
        s = sub.add_parser(name)
        s.add_argument("--data", required=True)
        if name == "add":
            s.add_argument("--report", required=True)
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
    rc = {"add": cmd_add, "list": cmd_list, "rejected": cmd_rejected, "mark": cmd_mark}[a.cmd](a)
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
