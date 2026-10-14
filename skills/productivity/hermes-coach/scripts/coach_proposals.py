#!/usr/bin/env python3
"""Coach proposals: parse, dedupe and track the weekly digest's proposed changes (stdlib only).

    coach_proposals.py add      --data DIR --report reports/weekly-<date>.md   # register the report's proposals
    coach_proposals.py list     --data DIR [--status pending|applied|rejected|all] [--json]
    coach_proposals.py rejected --data DIR                                    # the never-propose-again list
    coach_proposals.py mark     --data DIR --id ID --status applied|rejected [--note TEXT] [--by WHO]

A proposal is a fenced block in the weekly report:

    ```coach-proposal
    {"kind": "config", "target": "agent.max_turns", "change": {"value": 150},
     "evidence": "macbook/default/<id> — <title>: ...", "risk": "low", "title": "..."}
    ```

kind is one of skill_patch | config | shared_config | curriculum | plugin_rule. The `change` shape per kind:
  config / shared_config  {"value": <json value>}
  skill_patch             {"old_string": "...", "new_string": "...", "file_path": "SKILL.md"}   (target = skill name)
  curriculum/plugin_rule  {"old_string": "...", "new_string": "..."} or {"append": "..."}       (target = file path)

reports/proposals.jsonl is an append-only event log: one `status: pending` line per new proposal, then one
line per decision (`applied` / `rejected`). The current state of a proposal is its last line. A proposal whose
fingerprint (kind + target + change) was ever rejected is dropped by `add`, so it is never proposed again.
"""
import argparse, hashlib, json, os, re, sys, time
from pathlib import Path

KINDS = ("skill_patch", "config", "shared_config", "curriculum", "plugin_rule")
BLOCK = re.compile(r"```coach-proposal\s*\n(.*?)\n```", re.S)


def fingerprint(p):
    raw = json.dumps([p.get("kind"), p.get("target"), p.get("change")], sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


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
            cur[pid].update({k: v for k, v in e.items() if k in ("status", "at", "note", "by", "result")})
        else:
            cur[pid] = dict(e)
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
    kind = p.get("kind")
    if kind in ("config", "shared_config") and "value" not in ch:
        errs.append("config change needs {value}")
    if kind == "skill_patch" and not ("old_string" in ch and "new_string" in ch):
        errs.append("skill_patch change needs {old_string, new_string}")
    if kind in ("curriculum", "plugin_rule") and not ("append" in ch or ("old_string" in ch and "new_string" in ch)):
        errs.append(f"{kind} change needs {{append}} or {{old_string, new_string}}")
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
        if fp in seen:
            msgs.append(f"skipped {p['kind']}:{p['target']}: same as {seen[fp]['id']} ({seen[fp].get('status')})")
            continue
        n += 1
        rec = {"id": f"{date}-{n}", "fp": fp, "date": date, "report": os.path.relpath(report, Path(a.data).resolve()),
               "status": "pending", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        rec.update({k: p[k] for k in ("kind", "target", "change", "evidence", "title", "risk", "feature") if k in p})
        append(a.data, rec)
        seen[fp] = rec
        msgs.append(f"added {rec['id']} {p['kind']}:{p['target']}")
    print("\n".join(msgs) or "no coach-proposal blocks found")
    return 0 if props else 1


def cmd_list(a):
    rows = [p for p in state(a.data).values() if a.status == "all" or p.get("status") == a.status]
    if a.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
        return 0
    for p in rows:
        print(f"{p['id']:<14} {p.get('status'):<8} {p.get('risk', '?'):<5} {p['kind']:<13} {p['target']}"
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
    append(a.data, {"id": a.id, "status": a.status, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "by": a.by, **({"note": a.note} if a.note else {}), **({"result": a.result} if a.result else {})})
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
            s.add_argument("--status", required=True, choices=("applied", "rejected", "pending"))
            s.add_argument("--note", default="")
            s.add_argument("--result", default="")
            s.add_argument("--by", default="coach-apply")
    a = ap.parse_args(argv)
    return {"add": cmd_add, "list": cmd_list, "rejected": cmd_rejected, "mark": cmd_mark}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
