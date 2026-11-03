"""Scoring: per-skill recall, false loads, collision pairs, from results rows.

A results row (one router call):
    {variant, ask_id, skill (card owner), kind: should|near_miss, ask, instead, run, loaded: [names], ...}
"""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations


def score(rows: list[dict], skills: list[str] | None = None) -> dict:
    """Metrics for one variant's rows.

    recall[s]       = share of runs of s's should-load asks whose first reply loaded s.
    near_miss_ok[s] = share of runs of s's near-miss asks that did NOT load s.
    false_loads[s]  = loads of s on a near-miss of s, or on another skill's ask where s was neither the owner nor
                      the named ``instead`` winner.
    false_load_rate[s] = false_loads / all loads of s (0 when s never loaded).
    collisions      = pairs (a, b) whose loads alternate across runs of the same ask: some run loaded a without b
                      and another run loaded b without a.
    steals          = (owner, thief) where the thief loaded and the owner did not on >= 2/3 of a should ask's runs.
    """
    names = set(skills or []) | {r["skill"] for r in rows}
    should = defaultdict(lambda: [0, 0])
    near = defaultdict(lambda: [0, 0])
    loads = Counter()
    false = Counter()
    by_ask: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("error"):
            continue
        loaded = r.get("loaded") or []
        owner = r["skill"]
        by_ask[r["ask_id"]].append(r)
        if r["kind"] == "should":
            should[owner][1] += 1
            should[owner][0] += owner in loaded
        else:
            near[owner][1] += 1
            near[owner][0] += owner not in loaded
        for s in loaded:
            loads[s] += 1
            expected = (s == owner and r["kind"] == "should") or (s == r.get("instead"))
            if not expected:
                false[s] += 1
    recall = {s: should[s][0] / should[s][1] for s in names if should[s][1]}
    near_ok = {s: near[s][0] / near[s][1] for s in names if near[s][1]}
    flr = {s: false[s] / loads[s] for s in loads}
    pair_asks: dict[tuple[str, str], set[str]] = defaultdict(set)
    steals: dict[tuple[str, str], set[str]] = defaultdict(set)
    for ask_id, rs in by_ask.items():
        sets = [set(r.get("loaded") or []) for r in rs]
        seen = set().union(*sets) if sets else set()
        for a, b in combinations(sorted(seen), 2):
            if any(a in x and b not in x for x in sets) and any(b in x and a not in x for x in sets):
                pair_asks[(a, b)].add(ask_id)
        r0 = rs[0]
        if r0["kind"] == "should" and len(rs) >= 2:
            owner = r0["skill"]
            missed = [x for x in sets if owner not in x]
            if len(missed) * 3 >= 2 * len(sets):
                for thief, n in Counter(t for x in missed for t in x).items():
                    if n * 3 >= 2 * len(sets):
                        steals[(owner, thief)].add(ask_id)
    n_should = sum(v[1] for v in should.values())
    return {
        "recall": recall, "near_miss_ok": near_ok, "false_loads": dict(false), "loads": dict(loads),
        "false_load_rate": flr,
        "collisions": {f"{a}|{b}": sorted(v) for (a, b), v in sorted(pair_asks.items())},
        "steals": {f"{o}>{t}": sorted(v) for (o, t), v in sorted(steals.items())},
        "mean_recall": (sum(recall.values()) / len(recall)) if recall else 0.0,
        "micro_recall": (sum(v[0] for v in should.values()) / n_should) if n_should else 0.0,
        "mean_near_miss_ok": (sum(near_ok.values()) / len(near_ok)) if near_ok else 0.0,
        "errors": sum(1 for r in rows if r.get("error")),
        "rows": len(rows),
    }


def collision_skills(metrics: dict) -> set[str]:
    out = set()
    for key in metrics["collisions"]:
        out |= set(key.split("|"))
    return out


def weakest(metrics: dict, n: int, among: set[str] | None = None) -> list[str]:
    """Lowest recall first, then most false loads, then most collisions."""
    coll = Counter()
    for key, asks in metrics["collisions"].items():
        for s in key.split("|"):
            coll[s] += len(asks)
    cands = [s for s in metrics["recall"] if among is None or s in among]
    return sorted(cands, key=lambda s: (metrics["recall"][s], -metrics["false_loads"].get(s, 0), -coll[s], s))[:n]


def render_report(title: str, metrics: dict, owned: set[str], notes: list[str] | None = None,
                  top: int = 40) -> str:
    r = metrics["recall"]
    lines = [f"# {title}", ""]
    lines += notes or []
    lines += ["", "## Summary", "",
              f"- calls: {metrics['rows']} (errors {metrics['errors']})",
              f"- skills scored: {len(r)} (owned {len([s for s in r if s in owned])})",
              f"- mean recall (per skill): {metrics['mean_recall']:.3f}; micro recall (per call): {metrics['micro_recall']:.3f}",
              f"- mean near-miss pass rate: {metrics['mean_near_miss_ok']:.3f}",
              f"- skills with recall < 0.9: {sum(1 for v in r.values() if v < 0.9)}; < 0.8: {sum(1 for v in r.values() if v < 0.8)}; = 0: {sum(1 for v in r.values() if v == 0)}",
              f"- collision pairs: {len(metrics['collisions'])}; stable steals: {len(metrics['steals'])}", ""]
    lines += ["## Per-skill (lowest recall first)", "", "| skill | owned | recall | near-miss ok | false loads | false-load rate |",
              "|---|---|---|---|---|---|"]
    for s in sorted(r, key=lambda s: (r[s], s)):
        lines.append(f"| {s} | {'y' if s in owned else 'bundled'} | {r[s]:.2f} | {metrics['near_miss_ok'].get(s, float('nan')):.2f} | "
                     f"{metrics['false_loads'].get(s, 0)} | {metrics['false_load_rate'].get(s, 0):.2f} |")
    lines += ["", "## Top false loaders", "", "| skill | false loads | loads | rate |", "|---|---|---|---|"]
    for s, n in sorted(metrics["false_loads"].items(), key=lambda kv: -kv[1])[:top]:
        lines.append(f"| {s} | {n} | {metrics['loads'][s]} | {metrics['false_load_rate'][s]:.2f} |")
    lines += ["", "## Collision pairs (loads alternate across runs of one ask)", "", "| pair | asks |", "|---|---|"]
    for k, v in sorted(metrics["collisions"].items(), key=lambda kv: -len(kv[1]))[:top * 2]:
        lines.append(f"| {k.replace('|', ' / ')} | {len(v)}: {', '.join(v[:4])} |")
    lines += ["", "## Stable steals (another skill loads instead on >= 2/3 runs)", "", "| owner > thief | asks |", "|---|---|"]
    for k, v in sorted(metrics["steals"].items()):
        lines.append(f"| {k} | {', '.join(v)} |")
    return "\n".join(lines) + "\n"
