"""Mechanical agent-hint rates. Stdlib only; missing observations never imply failure."""
import json
from pathlib import Path

RULES = ('no_checkpoint', 'poll_loop')


def load(homes):
    """Read each home/profile log once. Source path scopes IDs across hosts/profiles."""
    rows, seen = [], set()
    for home in homes:
        home = Path(home).expanduser()
        for p in [home / 'coach/log.jsonl', *sorted(home.glob('profiles/*/coach/log.jsonl'))]:
            p = p.resolve()
            if p in seen or not p.is_file():
                continue
            seen.add(p)
            with p.open(errors='replace') as f:
                for line in f:
                    if 'agent_hint' not in line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(r, dict) and r.get('rule') in RULES:
                        rows.append({**r, 'source': str(p)})
    return rows


def _key(r):
    return (r.get('source', ''), r.get('session_id', ''), r.get('rule'), r.get('hint_id'))


def _ts(r):
    try:
        return float(r.get('ts', 0))
    except (ValueError, TypeError):
        return 0


def stats(rows, lo, hi):
    fires, outcomes = {}, {}
    legacy = dict.fromkeys(RULES, 0)
    for r in rows:
        rule = r.get('rule')
        if rule not in RULES:
            continue
        if r.get('event') == 'agent_hint' and lo <= _ts(r) < hi:
            if r.get('hint_id'):
                fires.setdefault(_key(r), r)
            else:
                legacy[rule] += 1
        elif (r.get('event') == 'agent_hint_outcome' and _ts(r) < hi
              and type(r.get('heeded')) is bool and r.get('hint_id') and r.get('evidence')):
            outcomes.setdefault(_key(r), []).append(r)
    result = {}
    for rule in RULES:
        cohort = {k: r for k, r in fires.items() if r['rule'] == rule}
        measured = []
        for k, f in cohort.items():
            verdicts = {r['heeded'] for r in outcomes.get(k, []) if _ts(r) >= _ts(f)}
            if len(verdicts) == 1:
                measured.append(verdicts.pop())
        n, h = len(measured), sum(measured)
        result[rule] = dict(fires=len(cohort) + legacy[rule], measured=n, heeded=h,
                            unresolved=len(cohort) - n, legacy=legacy[rule], rate=h / n if n else None)
    return result


def section(rows, now, days):
    out = [f'## Agent hint outcomes (last {days:g} days)', '',
           '| Rule | Fires | Measured | Heeded | Heeded rate | Unresolved | Legacy unmeasured |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for rule, s in stats(rows, now - days * 86400, now).items():
        rate = f"{s['rate']:.1%}" if s['rate'] is not None else 'n/a'
        out.append(f"| {rule} | {s['fires']} | {s['measured']} | {s['heeded']} | {rate} | "
                   f"{s['unresolved']} | {s['legacy']} |")
    out += ['', 'Rate = heeded / measured, joined by source, session, rule and hint ID to hints fired in this window. '
            'Unresolved includes undelivered hints, interrupted/incomplete windows and lost observations; '
            'legacy hints have no ID. Neither is counted as ignored. Missing data is not evidence of ineffectiveness.',
            'Mechanical window: next five tool calls started after delivery (or observed normal turn end). '
            'no_checkpoint: subsequent assistant report or successful checkpoint tool. '
            'poll_loop: fewer than three polling calls and no foreground shell polling loop. '
            'This measures behavior change, not causal benefit or detector precision. '
            'Keep/reword/rate-limit/retire recommendations require seven days of measured data and coverage review.']
    return out
