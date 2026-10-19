"""Regression harness: the coach's System One screens vs the LLM judges, scored on the hindsight gold sets.

    python3 plugins/coach/eval/system_one_bakeoff.py [--coach-dir ~/.hermes/coach] [--out DIR]

Uses the production question definitions in coach/system_one.py and the same inputs the LLM judge sees. Needs
<coach-dir>/{fixloop,replyset}/{sample,gold,detect}.jsonl (built by the coach backtest tooling) and TYPESAFE_API_KEY.
Prints AUC for the current judge (detect.jsonl) and Jev, plus how many LLM calls a screen at 0.2/0.3/0.5 removes
and how many of the current judge's correct catches it would lose. Gold >= 0.7 counts as positive.
Calls are logged to <out>/calls.jsonl, never the live system-one log.
"""
import argparse
import ast
import concurrent.futures as cf
import importlib.util
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def jl(p):
    return {json.loads(l)["id"]: json.loads(l) for l in open(p) if l.strip()}


def as_list(v):
    if isinstance(v, list):
        return v
    try:
        return ast.literal_eval(v)
    except Exception:
        return [str(v)]


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def auc(pos, neg):
    if not pos or not neg:
        return float("nan")
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


def run(name, sample, state_fn, questions, client, out):
    path = out / f"{name}.jsonl"
    done = jl(path) if path.exists() else {}
    todo = [s for i, s in sample.items() if i not in done]

    def one(s):
        return {"id": s["id"], "answers": client.decide(f"bakeoff_{name}", state_fn(s), questions, timeout=15)}

    with cf.ThreadPoolExecutor(8) as ex, open(path, "a") as fh:
        for rec in ex.map(one, todo):
            fh.write(json.dumps(rec) + "\n")
    return jl(path)


def report(label, gold, det, jev, rule, jkey, prod_th):
    ids = [i for i in gold if i in det and (jev.get(i) or {}).get("answers")
           and f(gold[i].get(rule)) is not None and f(det[i].get(rule)) is not None]
    g = {i: f(gold[i][rule]) >= 0.7 for i in ids}
    d = {i: f(det[i][rule]) for i in ids}
    j = {i: jev[i]["answers"][jkey]["noul"] for i in ids}
    print(f"## {label}: n={len(ids)} gold+={sum(g.values())}")
    print(f"   AUC current={auc([d[i] for i in ids if g[i]], [d[i] for i in ids if not g[i]]):.3f} "
          f"jev={auc([j[i] for i in ids if g[i]], [j[i] for i in ids if not g[i]]):.3f}")
    base_tp = [i for i in ids if d[i] >= prod_th and g[i]]
    for t in (0.2, 0.3, 0.5):
        kept = [i for i in ids if j[i] >= t]
        lost = [i for i in base_tp if j[i] < t]
        print(f"   screen@{t}: LLM calls {len(kept)}/{len(ids)} ({100 - 100 * len(kept) // max(1, len(ids))}% fewer), "
              f"correct catches lost {len(lost)}/{len(base_tp)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coach-dir", default=str(Path.home() / ".hermes/coach"))
    ap.add_argument("--out", default=str(Path.cwd() / "system-one-bakeoff"))
    a = ap.parse_args()
    cdir, out = Path(a.coach_dir).expanduser(), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    os.environ["SYSTEM_ONE_LOG"] = str(out / "calls.jsonl")
    s1 = _load("coach_s1_bakeoff", HERE.parent / "system_one.py")
    rc = _load("coach_rc_bakeoff", HERE.parent / "reply_check.py")
    client = s1.client()
    assert client and client.api_key(), "system-one plugin and TYPESAFE_API_KEY required"
    profile = rc.profile_text()

    fx = run("fixloop", jl(cdir / "fixloop/sample.jsonl"),
             lambda s: s1.fix_state(s.get("prev_reply"), as_list(s.get("prev_log") or []), s.get("user")),
             s1.FIX_Q, client, out)
    report("fixloop p_failed_fix", jl(cdir / "fixloop/gold.jsonl"), jl(cdir / "fixloop/detect.jsonl"), fx,
           "p_failed_fix", "failed_fix", 0.8)
    rq = {r: s1.REPLY_Q[r] for r in s1.SCREENED_RULES}
    rx = run("replyset", jl(cdir / "replyset/sample.jsonl"),
             lambda s: s1.reply_state(s.get("user_ask"), as_list(s.get("tool_log") or []), s.get("reply"), profile),
             rq, client, out)
    for r in s1.SCREENED_RULES:
        report(f"reply_check {r}", jl(cdir / "replyset/gold.jsonl"), jl(cdir / "replyset/detect.jsonl"), rx, r, r, 0.5)


if __name__ == "__main__":
    main()
