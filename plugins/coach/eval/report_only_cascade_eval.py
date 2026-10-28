"""report_only_cascade_eval.py: cascade eval of report_only on a gold-labeled set (gold.jsonl from adjudicate2.py).

Stage 1 (screen): production JUDGE_SYS on the screen model (default claude-sonnet-5-5), prod tool log.
Stage 2 (confirm): CONFIRM_SYS on claude-opus-5-5 with real tool args, only rows whose screen p >= --floor.
Production gating before both: prefilter candidate, not scope_limited. Splits by session hash 70/30; thresholds are chosen
on train only. Verdicts are cached by sha(model+system+prompt) in <out>/cache.jsonl.
"""
import argparse, concurrent.futures as cf, hashlib, importlib.util, json, math, os, sys, threading, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; COACH = HERE.parent; MODS = COACH.parents[1]
spec = importlib.util.spec_from_file_location("rc_eval", COACH / "reply_check.py")
rc = importlib.util.module_from_spec(spec); spec.loader.exec_module(rc)


def split(session):
    return "train" if int(hashlib.sha256(session.encode()).hexdigest(), 16) % 100 < 70 else "test"


def wilson(k, n, z=1.96):
    if not n: return (float("nan"), float("nan"))
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round((c - h) / d, 3), round((c + h) / d, 3))


def metrics(rows, fire):
    tp = sum(1 for r in rows if fire(r) and r["pos"]); fp = sum(1 for r in rows if fire(r) and not r["pos"])
    npos = sum(1 for r in rows if r["pos"])
    return {"tp": tp, "fp": fp, "fn": npos - tp, "fires": tp + fp, "positives": npos, "n": len(rows),
            "precision": round(tp / (tp + fp), 3) if tp + fp else None, "p_ci": wilson(tp, tp + fp),
            "recall": round(tp / npos, 3) if npos else None, "r_ci": wilson(tp, npos)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--screen-model", default="claude-sonnet-5-5"); ap.add_argument("--confirm-model", default="claude-opus-5-5")
    ap.add_argument("--floor", type=float, default=0.5); ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--splits", default="train,test"); ap.add_argument("--no-confirm", action="store_true")
    ap.add_argument("--profile", default=None)
    a = ap.parse_args()
    if a.profile: os.environ["COACH_REPLY_PROFILE"] = a.profile
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in open(a.data) if l.strip()]
    for r in rows:
        r["tool_log"] = r.get("tool_log") or [f"-> {k}(...)" for k, n in (r.get("tools") or {}).items() for _ in range(min(int(n), 5))]
        r["tool_rich"] = r.get("tool_rich") or r["tool_log"]
        r["split"] = split(r["session"]); r["scoped"] = rc.scope_limited(r["user"]); r["cand"] = "report_only" in rc.prefilter(r["reply"])
    fixtures = json.loads((COACH / "tests/fixtures/report_only_scope_limits.json").read_text())
    for i, f in enumerate(fixtures):
        rows.append({"id": f"scope{i}", "session": f"fx{i}", "split": "fixture", "trigger": "fixture", "user": f["user"], "reply": f["reply"],
                     "tool_log": f.get("log", []), "tool_rich": f.get("log", []), "pos": False, "scoped": rc.scope_limited(f["user"]),
                     "cand": "report_only" in rc.prefilter(f["reply"])})
    cache_p = out / "cache.jsonl"; cache = {}
    if cache_p.exists():
        for l in cache_p.read_text().splitlines():
            d = json.loads(l); cache[d["k"]] = d["v"]
    lock = threading.Lock()
    sys.path.insert(0, str(MODS / "skills/productivity/hermes-coach/scripts"))
    from coach_eval import hermes_interpreter, _Worker  # type: ignore
    py, root = hermes_interpreter()
    w = str(MODS / "skills/productivity/hermes-coach/scripts/coach_llm_worker.py")
    cmd = [py, "-I", "-c", "import runpy, sys; sys.path.insert(0, %r); import hermes_bootstrap; sys.argv = [%r]; runpy.run_path(%r, run_name='__main__')" % (root, w, w)]
    env = dict(os.environ); local = threading.local()

    def call(model, system, user, parse, think_off):
        k = hashlib.sha256((model + system + user).encode()).hexdigest()[:24]
        if k in cache: return cache[k]
        if not hasattr(local, "wk"): local.wk = _Worker(cmd, env)
        for att in range(6):
            resp = local.wk.ask({"op": "call", "model": model, "system": system, "user": user, "max_tokens": 2000,
                                 **({"thinking": {"type": "disabled"}} if think_off else {})})
            if resp.get("error"):
                time.sleep(min(60, 5 * 2 ** att) if ("429" in resp["error"] or "529" in resp["error"]) else 1); continue
            v = parse(resp.get("text") or "")
            if v:
                with lock:
                    cache[k] = v
                    with open(cache_p, "a") as fh: fh.write(json.dumps({"k": k, "v": v}) + "\n")
                return v
        return None

    todo = [r for r in rows if r["cand"] and not r["scoped"] and r["split"] in a.splits.split(",") + ["fixture"]]
    sysj = rc.judge_system()
    print(len(rows), "rows,", len(todo), "stage-1", flush=True)
    def s1(r):
        v = call(a.screen_model, sysj, rc.judge_prompt(r["user"], r["tool_log"], r["reply"]), lambda t: (rc.parse_verdict(t) or None), "sonnet" in a.screen_model)
        r["p_screen"] = v["report_only"] if v else None; r["ev"] = v["evidence"] if v else ""
    with cf.ThreadPoolExecutor(a.workers) as ex: list(ex.map(s1, todo))
    sysc = rc.confirm_system()
    if not a.no_confirm:
        t2 = [r for r in todo if (r.get("p_screen") or 0) >= a.floor]
        print(len(t2), "stage-2", flush=True)
        def s2(r):
            v = call(a.confirm_model, sysc, rc.confirm_prompt(r["user"], r["tool_rich"], r["reply"]),
                     lambda t: ({"p": rc.parse_confirm(t)[0], "ev": rc.parse_confirm(t)[1]} if rc.parse_confirm(t) else None), "sonnet" in a.confirm_model)
            r["p_opus"] = v["p"] if v else None; r["ev2"] = v["ev"] if v else ""
        with cf.ThreadPoolExecutor(a.workers) as ex: list(ex.map(s2, t2))
    json.dump([{k: r.get(k) for k in ("id", "session", "split", "trigger", "pos", "cand", "scoped", "p_screen", "p_opus", "ev", "ev2", "gold_src", "user", "reply")} for r in rows],
              open(out / "rows.json", "w"))
    print("saved", out / "rows.json")


if __name__ == "__main__":
    main()
