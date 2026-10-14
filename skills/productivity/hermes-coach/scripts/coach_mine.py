#!/usr/bin/env python3
"""Aggregate session digests from every host into KPIs and a labeling queue (stdlib only).

    coach_mine.py --data DIR [--days 7] [--top 10] [--write-kpis]

DIR is the digest repo (<host>/<profile>/<id>.json). Prints a Markdown context block for the coach
agent: KPIs per host and overall (this window vs the previous one), then the top-N unlabeled
sessions by waste score with their user turns. Labeled = id present in DIR/findings/*.jsonl.
--write-kpis also appends the window's KPIs to DIR/reports/kpis.jsonl (one line per run day).
"""
import argparse, collections, glob, json, os, statistics, time
from pathlib import Path


def load(data: Path):
    out = []
    for f in glob.glob(str(data / "*" / "*" / "*.json")):
        if "/reports/" in f or "/findings/" in f:
            continue
        try:
            out.append(json.load(open(f)))
        except Exception:
            pass
    return out


def labeled_ids(data: Path):
    ids = set()
    for f in glob.glob(str(data / "findings" / "*.jsonl")):
        for line in open(f):
            try:
                ids.add(json.loads(line)["session_id"])
            except Exception:
                pass
    return ids


def total_cost(d):
    return (d.get("cost_usd") or 0) + ((d.get("children") or {}).get("cost_usd") or 0)


META_WORDS = ("hermes", "desktop app", "session panel", "composer", "shortcut", "hotkey", "plugin", "gateway",
              "cmd-", "shift-cmd", "skill", "kanban", "cron", "prompt coach", "topic-shift", "spin-out")


def is_meta(d):
    """Session is about Hermes itself: title says so, or Hermes is in >=30% of the user's turns."""
    t = (d.get("title") or "").lower()
    if any(w in t for w in META_WORDS):
        return True
    n = max(1, len(d.get("user_turns") or []))
    return (d.get("signals") or {}).get("hermes_meta", 0) / n >= 0.3


def kpis(ds):
    """Interactive sessions only (cron/kanban/oneshot are machine-driven)."""
    inter = [d for d in ds if d.get("source") not in ("cron", "kanban", "oneshot", "api_server")
             and d.get("user_turns_total", 0) > 0]
    n = len(inter)
    if not n:
        return {"sessions": 0}
    sizes = sorted(d["msgs"] for d in inter)
    sig = lambda d, k: (d.get("signals") or {}).get(k, 0)
    p90 = sizes[min(n - 1, int(round(0.9 * (n - 1))))]
    cost = sum(total_cost(d) for d in inter)
    meta_cost = sum(total_cost(d) for d in inter if is_meta(d))
    return {
        "sessions": n,
        "cost_usd": round(cost, 1),
        "msgs_p50": statistics.median(sizes), "msgs_p90": p90,
        "over_300_msgs": sum(1 for s in sizes if s > 300),
        "repeat_fix_rate": round(sum(1 for d in inter if sig(d, "repeat_fix") >= 3) / n, 3),
        "stuck_rate": round(sum(1 for d in inter if sig(d, "stuck") >= 2) / n, 3),
        "hermes_meta_cost_share": round(meta_cost / cost, 3) if cost else 0,
        "screenshot_sessions": sum(1 for d in inter if sig(d, "screenshot")),
        "tool_error_rate": round(sum(sum((d.get("tool_errors") or {}).values()) for d in inter)
                                 / max(1, sum(d.get("tool_calls") or 0 for d in inter)), 3),
        "subagents_per_session": round(sum((d.get("children") or {}).get("n", 0) for d in inter) / n, 2),
    }


def waste(d):
    s = d.get("signals") or {}
    errs = sum((d.get("tool_errors") or {}).values())
    return round(total_cost(d) * (1 + 0.15 * s.get("repeat_fix", 0) + 0.1 * s.get("stuck", 0))
                 + 0.02 * d.get("msgs", 0) + 0.05 * errs + 0.3 * s.get("frustration", 0), 2)


def fmt_kpis(cur, prev):
    keys = [k for k in cur if k != "sessions"] if cur.get("sessions") else []
    rows = [f"| sessions | {cur.get('sessions', 0)} | {prev.get('sessions', 0)} |"]
    for k in keys:
        rows.append(f"| {k} | {cur.get(k)} | {prev.get(k, '–')} |")
    return "| KPI | this window | previous |\n|---|---|---|\n" + "\n".join(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--turn-chars", type=int, default=300)
    ap.add_argument("--write-kpis", action="store_true")
    a = ap.parse_args()
    data = Path(a.data)
    ds = load(data)
    now = time.time()
    lo, plo = now - a.days * 86400, now - 2 * a.days * 86400
    act = lambda d: d.get("last_activity_at") or d.get("started_at") or 0
    cur = [d for d in ds if act(d) >= lo]
    prev = [d for d in ds if plo <= act(d) < lo]

    by_host = collections.defaultdict(list)
    for d in cur:
        by_host[d["host"]].append(d)
    overall = kpis(cur)
    out = [f"# Coach data — last {a.days:g} days ({time.strftime('%Y-%m-%d')})",
           f"Digests loaded: {len(ds)} across hosts {sorted({d['host'] for d in ds})}.", "",
           "## KPIs, all hosts (interactive sessions)", fmt_kpis(overall, kpis(prev)), ""]
    for h in sorted(by_host):
        k = kpis(by_host[h])
        out.append(f"- **{h}**: " + ", ".join(f"{x}={k[x]}" for x in
                   ("sessions", "cost_usd", "msgs_p90", "repeat_fix_rate", "hermes_meta_cost_share") if x in k))
    if a.write_kpis:
        (data / "reports").mkdir(exist_ok=True)
        with open(data / "reports" / "kpis.jsonl", "a") as f:
            f.write(json.dumps({"date": time.strftime("%Y-%m-%d"), "days": a.days, "overall": overall,
                                "by_host": {h: kpis(v) for h, v in by_host.items()}}) + "\n")

    done = labeled_ids(data)
    queue = sorted((d for d in cur if d["id"] not in done and d.get("user_turns")
                    and d.get("source") not in ("cron",)), key=waste, reverse=True)[: a.top]
    out += ["", f"## Labeling queue: top {len(queue)} unlabeled sessions by waste score", ""]
    for d in queue:
        st = time.strftime("%Y-%m-%d %H:%M", time.localtime(d["started_at"]))
        out.append(f"### {d['host']}/{d['profile']}/{d['id']} — {d.get('title') or '(untitled)'}")
        out.append(f"hermes_meta={is_meta(d)} source={d['source']} started={st} msgs={d['msgs']} user_turns={d['user_turns_total']} "
                   f"cost=${total_cost(d):.2f} (children {d['children']['n']}) waste={waste(d)} "
                   f"repo={d.get('repo')} signals={d.get('signals')} tool_errors={d.get('tool_errors')}")
        out.append("top tools: " + ", ".join(f"{k}={v}" for k, v in list((d.get("tools") or {}).items())[:10]))
        out.append("user turns:")
        for u in d["user_turns"]:
            t = u["text"].replace("\n", " ")
            out.append(f"  - {t[:a.turn_chars]}{'…' if len(t) > a.turn_chars else ''}")
        out.append(f"last reply: {(d.get('last_reply') or '').replace(chr(10), ' ')[:400]}")
        out.append("")
    print("\n".join(out))


if __name__ == "__main__":
    main()
