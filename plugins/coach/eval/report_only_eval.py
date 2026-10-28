"""Offline eval of the reply check's `report_only` rule on a labeled set of real reply endings.

    python3 plugins/coach/eval/report_only_eval.py --data <eval_set.jsonl> [--out DIR] [--model claude-sonnet-5-5]
                                                   [--profile <reply_profile.md>] [--workers 8]

Each row needs: id, session, trigger ("human" | "event"), user, tools ({name: count}), reply, label
({outcome, agent_fixable}). Positive = label.outcome == "report_only" and agent_fixable is true.

Production path, unchanged: reply_check.prefilter() -> report_only candidate -> scope_limited(ask) guard -> the
production judge prompt (judge_system() + judge_prompt()) -> report_only p >= threshold. Rows are split by a hash of
their session (70% train / 30% held out); the threshold is picked on train (highest recall with precision >= 0.85,
ties to the higher threshold) and scored on held-out human-trigger rows; event-trigger rows are reported separately.
The scope-limit fixtures (tests/fixtures/report_only_scope_limits.json) go through the same path and must never fire;
they are also judged WITHOUT the deterministic guard, to show what the judge alone does.

Judge calls go through the hermes-coach `coach_llm_worker.py` (one bounded request each, Hermes's own credentials)
and are cached in <out>/verdicts.jsonl, so a rerun only pays for new rows.
"""
import argparse
import concurrent.futures as cf
import hashlib
import importlib.util
import json
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
COACH = HERE.parent
MODS = COACH.parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rc = _load("ro_reply_check", COACH / "reply_check.py")


def split(session):
    return "train" if int(hashlib.sha256(session.encode()).hexdigest(), 16) % 100 < 70 else "test"


def positive(row):
    lab = row.get("label") or {}
    return lab.get("outcome") == "report_only" and lab.get("agent_fixable") is True


def tool_log(tools):
    out = []
    for name, n in sorted((tools or {}).items(), key=lambda kv: -kv[1]):
        out += [f"-> {name}(...)"] * min(int(n), 5)
    return out


def metrics(rows, th):
    fired = [r for r in rows if r["fire_p"] is not None and r["fire_p"] >= th]
    tp = sum(1 for r in fired if r["pos"])
    fp = len(fired) - tp
    npos = sum(1 for r in rows if r["pos"])
    p = tp / (tp + fp) if tp + fp else float("nan")
    rcl = tp / npos if npos else float("nan")
    return {"th": th, "tp": tp, "fp": fp, "fn": npos - tp, "precision": round(p, 3), "recall": round(rcl, 3),
            "fires": len(fired), "n": len(rows), "positives": npos}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default=os.path.expanduser("~/.hermes/cache/report_only_eval"))
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--profile", default=None, help="reply_profile.md overlay (default: the live one)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--min-precision", type=float, default=0.85)
    ap.add_argument("--thinking", default="disabled", choices=["disabled", "omit"],
                    help="omit for models that reject thinking=disabled")
    ap.add_argument("--splits", default="train,test,fixture",
                    help="which rows to judge (tune prompts on train,fixture; score test once at the end)")
    ap.add_argument("--no-judge", action="store_true",
                    help="no LLM calls: score only the deterministic unverified-owner check (needs no credentials)")
    a = ap.parse_args()
    if a.profile:
        os.environ["COACH_REPLY_PROFILE"] = a.profile
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in open(a.data) if l.strip()]
    fixtures = json.loads((COACH / "tests" / "fixtures" / "report_only_scope_limits.json").read_text())
    items = []
    for r in rows:
        items.append({"id": r["id"], "user": r.get("user") or "", "reply": r.get("reply") or "",
                      "log": tool_log(r.get("tools")), "split": split(r["session"]), "trigger": r.get("trigger"),
                      "pos": positive(r), "quote": (r.get("label") or {}).get("quote", "")})
    for i, f in enumerate(fixtures):
        items.append({"id": f"scope{i}", "user": f["user"], "reply": f["reply"], "log": f.get("log", []),
                      "split": "fixture", "trigger": "fixture", "pos": False, "quote": ""})
    # unverified-owner fixtures (the 2026-10-27 hub turn and its controls): expect = the deterministic check fires
    owners = json.loads((COACH / "tests" / "fixtures" / "report_only_unverified_owner.json").read_text())
    by_id = {f["id"]: f for f in owners}
    for f in owners:
        items.append({"id": f["id"], "user": f["user"],
                      "reply": f.get("reply") or by_id.get(f.get("reply_from"), {}).get("reply", ""),
                      "log": f.get("log") or by_id.get(f.get("log_from"), {}).get("log", []),
                      "split": "owner_fixture", "trigger": "owner_fixture", "pos": bool(f["expect"]), "quote": ""})

    system = rc.judge_system()
    cache_p = out / "verdicts.jsonl"
    key = hashlib.sha256((system + a.model).encode()).hexdigest()[:12]
    cache = {}
    if cache_p.exists():
        for l in cache_p.read_text().splitlines():
            d = json.loads(l)
            if d.get("key") == key:
                cache[d["id"]] = d
    for it in items:
        it["cand"] = "report_only" in rc.prefilter(it["reply"])
        it["scoped"] = rc.scope_limited(it["user"])
        # production order: the deterministic unverified-owner check runs before the judge and fires at p 1.0
        it["owner"] = not it["scoped"] and rc.unverified_owner(it["reply"], it["log"]) is not None

    def owner_metrics(rows):
        f = [r for r in rows if r["owner"]]
        tp = sum(1 for r in f if r["pos"])
        return {"n": len(rows), "fires": len(f), "tp": tp, "fp": len(f) - tp,
                "precision": round(tp / len(f), 3) if f else None, "fp_ids": [r["id"] for r in f if not r["pos"]]}

    owner_report = {f"{s}_{t}": owner_metrics([it for it in items if it["split"] == s and it["trigger"] == t])
                    for s, t in (("train", "human"), ("test", "human"), ("train", "event"), ("test", "event"),
                                 ("fixture", "fixture"), ("owner_fixture", "owner_fixture"))}
    ofx = [it for it in items if it["split"] == "owner_fixture"]
    owner_report["owner_fixture_mismatches"] = [it["id"] for it in ofx if it["owner"] != it["pos"]]
    print("unverified_owner (deterministic):", json.dumps(owner_report, indent=2), flush=True)
    if a.no_judge:
        (out / "owner_report.json").write_text(json.dumps(owner_report, indent=2))
        return
    items = [it for it in items if it["split"] != "owner_fixture"]  # judged metrics keep the labeled set only
    # fixtures are judged even though the guard drops them, to report the judge alone
    todo = [it for it in items if it["cand"] and (not it["scoped"] or it["split"] == "fixture")
            and it["id"] not in cache and it["split"] in a.splits.split(",")]
    print(f"{len(items)} items, {sum(it['cand'] for it in items)} prefilter candidates, {len(todo)} to judge "
          f"(cache {len(cache)}, key {key})", flush=True)

    if todo:
        sys.path.insert(0, str(MODS / "skills/productivity/hermes-coach/scripts"))
        from coach_eval import hermes_interpreter, _Worker  # type: ignore
        py, root = hermes_interpreter()
        w = str(MODS / "skills/productivity/hermes-coach/scripts/coach_llm_worker.py")
        cmd = [py, "-I", "-c", "import runpy, sys; sys.path.insert(0, %r); import hermes_bootstrap; "
               "sys.argv = [%r]; runpy.run_path(%r, run_name='__main__')" % (root, w, w)]
        env = dict(os.environ)
        lock = threading.Lock()
        local = threading.local()

        def judge(it):
            if not hasattr(local, "wk"):
                local.wk = _Worker(cmd, env)
            for attempt in range(6):
                resp = local.wk.ask({"op": "call", "model": a.model, "system": system,
                                     "user": rc.judge_prompt(it["user"], it["log"], it["reply"]),
                                     "max_tokens": 2000,
                                     **({"thinking": {"type": "disabled"}} if a.thinking == "disabled" else {})})
                if resp.get("error"):
                    print(f"  {it['id']}: {resp['error'][:120]}", flush=True)
                    busy = "429" in resp["error"] or "529" in resp["error"]
                    time.sleep(min(60, 5 * 2 ** attempt) if busy else 1)
                    continue
                v = rc.parse_verdict(resp.get("text") or "")
                if v:
                    rec = {"id": it["id"], "key": key, "verdict": v}
                    with lock:
                        with open(cache_p, "a") as fh:
                            fh.write(json.dumps(rec) + "\n")
                    return rec
            return None

        with cf.ThreadPoolExecutor(a.workers) as ex:
            for i, rec in enumerate(ex.map(judge, todo)):
                if rec:
                    cache[rec["id"]] = rec
                if i % 50 == 0:
                    print(f"  judged {i + 1}/{len(todo)}", flush=True)

    for it in items:
        v = (cache.get(it["id"]) or {}).get("verdict")
        it["judge_p"] = v["report_only"] if v and it["cand"] else None
        it["evidence"] = v["evidence"] if v else ""
        it["fire_p"] = None if it["scoped"] else (1.0 if it["owner"] else it["judge_p"])

    def sel(split_, trig):
        return [it for it in items if it["split"] == split_ and it["trigger"] == trig]

    grid = [round(x / 100, 2) for x in range(50, 100, 5)]
    train_h = sel("train", "human")
    table = [metrics(train_h, th) for th in grid]
    ok = [m for m in table if m["precision"] == m["precision"] and m["precision"] >= a.min_precision]
    best = max(ok, key=lambda m: (m["recall"], m["th"])) if ok else max(table, key=lambda m: m["precision"])
    th = best["th"]
    fx = [it for it in items if it["split"] == "fixture"]
    hum = [it for it in items if it["trigger"] == "human"]
    report = {
        "model": a.model, "threshold": th, "train_human_grid": table,
        "train_human": metrics(train_h, th), "train_event": metrics(sel("train", "event"), th),
        "heldout_human": metrics(sel("test", "human"), th), "heldout_event": metrics(sel("test", "event"), th),
        "heldout_human_grid": [metrics(sel("test", "human"), t) for t in grid],
        "prefilter_recall_human": round(sum(1 for it in hum if it["pos"] and it["cand"])
                                        / max(1, sum(1 for it in hum if it["pos"])), 3),
        "prefilter_fire_rate_human": round(sum(1 for it in hum if it["cand"]) / max(1, len(hum)), 3),
        "scope_fixtures": {"n": len(fx), "fires": sum(1 for it in fx if it["fire_p"] is not None and it["fire_p"] >= th),
                           "judge_alone_fires": sum(1 for it in fx if (it["judge_p"] or 0) >= th),
                           "judge_alone_p": [it["judge_p"] for it in fx]},
    }
    errs = [{"id": it["id"], "split": it["split"], "trigger": it["trigger"], "pos": it["pos"], "p": it["fire_p"],
             "evidence": it["evidence"], "quote": it["quote"]}
            for it in items if it["trigger"] in ("human", "event") and ((it["fire_p"] or 0) >= th) != it["pos"]]
    (out / "report.json").write_text(json.dumps(report, indent=2))
    (out / "errors.jsonl").write_text("".join(json.dumps(e) + "\n" for e in errs))
    print(json.dumps({k: v for k, v in report.items() if k not in ("train_human_grid", "heldout_human_grid")},
                     indent=2))
    for name in ("train_human_grid", "heldout_human_grid"):
        print(name)
        for m in report[name]:
            print(f"  {m['th']:.2f}  P {m['precision']}  R {m['recall']}  fires {m['fires']}")


if __name__ == "__main__":
    main()
