#!/usr/bin/env python3
"""Replay eval for the self-heal complaint detector (self_heal.detect_complaint) over a hand-labeled set.

Dataset: JSONL rows {id, label (1 defect report / 0 not), component, text (user message), prev_reply, split, ...}.
The real set holds the user's own words, so it lives in the private coach data repo
(hermes-coach-data/eval/selfheal-v1.jsonl), never here.

Runs the exact production path: prefilter, then the `coach_defect` judge with the production prompt, via the Hermes
auxiliary client. So run it under the Hermes interpreter:

  <hermes python> -I plugins/coach/eval/selfheal_replay.py --hermes-root ~/.hermes/hermes-agent \
      --data ~/code/hermes-coach-data/eval/selfheal-v1.jsonl --out /tmp/selfheal-eval [--model M --provider P]

Judge verdicts are cached by (prompt, model) in <out>/cache.jsonl, so re-running after a prefilter-only change is
free. Writes <out>/report.md + report.json: catch rate (recall), precision, every miss with its quote, every false
positive, per split (dev/test) and per component, and the prefilter's own recall.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_self_heal():
    spec = importlib.util.spec_from_file_location("selfheal_eval_mod", HERE.parent / "self_heal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def metrics(rows, pred_key="pred"):
    tp = sum(1 for r in rows if r["label"] == 1 and r[pred_key])
    fn = sum(1 for r in rows if r["label"] == 1 and not r[pred_key])
    fp = sum(1 for r in rows if r["label"] == 0 and r[pred_key])
    tn = sum(1 for r in rows if r["label"] == 0 and not r[pred_key])
    rec = tp / (tp + fn) if tp + fn else 0.0
    prec = tp / (tp + fp) if tp + fp else 0.0
    return {"n": len(rows), "pos": tp + fn, "neg": fp + tn, "tp": tp, "fn": fn, "fp": fp, "tn": tn,
            "catch_rate": round(rec, 3), "precision": round(prec, 3),
            "fp_rate": round(fp / (fp + tn), 3) if fp + tn else 0.0}


def quote(t, n=220):
    return " ".join(str(t or "").split())[:n]


def main(argv=None, llm=None, confirm_llm=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hermes-root", default="")
    ap.add_argument("--model", default=os.environ.get("COACH_DEFECT_MODEL", ""))
    ap.add_argument("--provider", default=os.environ.get("COACH_DEFECT_PROVIDER", ""))
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-prev", action="store_true", help="hide the previous reply from the judge (ablation)")
    ap.add_argument("--mode", choices=("complaint", "admission"), default="complaint",
                    help="admission: rows' text is the agent's final reply, judged by detect_admission's path")
    ap.add_argument("--samples", type=int, default=1,
                    help="independent judge samples per row (the judge is not deterministic at temp 0); a row is "
                         "predicted a defect when ANY sample reaches the threshold, as one live turn filing would")
    ap.add_argument("--confirm", action="store_true",
                    help="admission: the production cascade (audit_verdict: judge, then the coach_defect_confirm "
                         "model on an admission >= GATE), not the first-stage judge alone")
    ap.add_argument("--confirm-model", default=os.environ.get("COACH_DEFECT_CONFIRM_MODEL", ""),
                    help="pin the confirm stage (class routing picks claude-sonnet-5-5 or gpt-6.1-sol per call, and "
                         "the two score the same row 0.0 vs 0.96): replay the model a live detection logged")
    ap.add_argument("--confirm-provider", default=os.environ.get("COACH_DEFECT_CONFIRM_PROVIDER", ""))
    a = ap.parse_args(argv)
    if a.hermes_root:
        sys.path.insert(0, a.hermes_root)
        import hermes_bootstrap  # noqa: F401  (credentials, provider routing)
    if a.model:
        os.environ["COACH_DEFECT_MODEL"] = a.model
    if a.provider:
        os.environ["COACH_DEFECT_PROVIDER"] = a.provider
    if a.confirm_model:
        os.environ["COACH_DEFECT_CONFIRM_MODEL"] = a.confirm_model
    if a.confirm_provider:
        os.environ["COACH_DEFECT_CONFIRM_PROVIDER"] = a.confirm_provider
    sh = load_self_heal()
    th = a.threshold if a.threshold is not None else (sh.AUDIT_THRESHOLD if a.mode == "admission" else sh.THRESHOLD)
    rows = [json.loads(x) for x in open(a.data) if x.strip()]
    if a.limit:
        rows = rows[: a.limit]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cache_path = out / "cache.jsonl"
    cache = {}
    if cache_path.exists():
        for x in cache_path.read_text().splitlines():
            try:
                d = json.loads(x)
                cache[d["k"]] = d["raw"]
            except Exception:
                pass
    lock = threading.Lock()
    model_tag = f"{a.provider}/{a.model}" if a.model else "aux:coach_defect"
    calls = {"n": 0, "err": 0}
    models: dict = {}  # stage -> {resolved model: calls}: what the replay actually ran on

    def judge(system, prompt, tag="", fn=None):
        """`tag` keeps samples and cascade stages apart in the cache (an empty tag keeps the old keys)."""
        stage_tag = (f"@{a.confirm_provider}/{a.confirm_model}" if "#confirm" in tag and a.confirm_model else "")
        k = hashlib.sha1(f"{model_tag}{tag}{stage_tag}\n{system}\n{prompt}".encode()).hexdigest()
        if k in cache:
            return cache[k]
        try:
            if hasattr(sh, "_MODEL_TL"):
                sh._MODEL_TL.model = ""
            raw = (fn or llm or sh.aux_judge)(system, prompt)
        except Exception as exc:
            with lock:
                calls["err"] += 1
            raise exc
        resolved = getattr(getattr(sh, "_MODEL_TL", None), "model", "") or "(unreported)"
        with lock:
            calls["n"] += 1
            stage = models.setdefault("confirm" if "#confirm" in tag else "judge", {})
            stage[resolved] = stage.get(resolved, 0) + 1
            cache[k] = raw
            with open(cache_path, "a") as fh:
                fh.write(json.dumps({"k": k, "raw": raw}) + "\n")
        return raw

    confirm = (confirm_llm or sh.confirm_judge()) if a.confirm and a.mode == "admission" else None
    if a.confirm and a.mode == "admission" and not confirm:
        raise SystemExit("--confirm: no coach_defect_confirm model configured")

    def sample(r, i):
        tag = f"#s{i}" if i else ""
        if a.mode == "admission":
            if confirm:
                return sh.audit_verdict(r["text"], lambda s, p: judge(s, p, tag), r.get("tool_log"),
                                        r.get("ask") or "", lambda s, p: judge(s, p, tag + "#confirm", confirm))
            return sh.parse(judge(sh._with_profile(sh.AUDIT_SYS),
                                  sh.admission_prompt(r["text"], r.get("tool_log"), r.get("ask") or ""), tag))
        prev = "" if a.no_prev else r.get("prev_reply") or ""
        return sh.parse(judge(sh._with_profile(sh.COMPLAINT_SYS), sh.complaint_prompt(prev, r["text"],
                                                                                      r.get("tool_log")), tag))

    def hits(v):
        if a.mode == "admission":  # only these kinds ever file from the turn-end audit (detect_admission)
            return bool(v and v["p_defect"] >= th and v.get("kind") in sh.AUDIT_KINDS)
        return bool(v and v["p_defect"] >= th and v.get("kind") not in ("product_bug", "external", "none"))

    def one(r):
        r["prefilter"] = (sh.admission_prefilter(r["text"], r.get("tool_log")) if a.mode == "admission"
                          else sh.prefilter(r["text"]))
        vs = []
        if r["prefilter"]:
            for i in range(max(1, a.samples)):
                v = None
                for _ in range(2):  # one retry on a transient provider error
                    try:
                        v = sample(r, i)
                        break
                    except Exception as exc:
                        r["error"] = str(exc)[:200]
                vs.append(v)
        got = [v for v in vs if v]
        v = max(got, key=lambda x: (hits(x), x["p_defect"])) if got else None
        r["p"] = v["p_defect"] if v else None
        r["ps"] = [x["p_defect"] if x else None for x in vs]
        r["sample_hits"] = sum(1 for x in vs if hits(x))
        r["pred_component"] = v["component"] if v else ""
        r["symptom"] = v["symptom"] if v else ""
        r["pred"] = hits(v)
        return r

    with ThreadPoolExecutor(a.workers) as ex:
        rows = list(ex.map(one, rows))

    for r in rows:
        r["pre_pred"] = r["prefilter"]
    rep = {"threshold": th, "model": model_tag, "judge_calls": calls, "no_prev": a.no_prev,
           "samples": max(1, a.samples), "confirm": bool(confirm), "models": models,
           "per_row": {r["id"]: {"label": r["label"], "sample_hits": r["sample_hits"], "ps": r["ps"]} for r in rows},
           "all": metrics(rows), "prefilter_only": metrics(rows, "pre_pred"),
           "splits": {s: metrics([r for r in rows if r.get("split") == s]) for s in ("dev", "test")},
           "with_prev": metrics([r for r in rows if r.get("prev_reply")]),
           "without_prev": metrics([r for r in rows if not r.get("prev_reply")]),
           "random_sample": metrics([r for r in rows if r.get("src") == "random"]),
           "components": {}, "errors": sum(1 for r in rows if r.get("error") and r.get("p") is None)}
    for r in rows:
        r.setdefault("component", "other")
    comps = sorted({r["component"] for r in rows if r["label"] == 1})
    for c in comps:
        sub = [r for r in rows if r["label"] == 1 and r["component"] == c]
        rep["components"][c] = {"pos": len(sub), "caught": sum(1 for r in sub if r["pred"])}
    misses = [r for r in rows if r["label"] == 1 and not r["pred"]]
    fps = [r for r in rows if r["label"] == 0 and r["pred"]]
    rep["misses"] = [{"id": r["id"], "component": r["component"], "prefilter": r["prefilter"], "p": r["p"],
                      "quote": quote(r["text"]), "prev": quote(r.get("prev_reply"), 160)} for r in misses]
    rep["false_positives"] = [{"id": r["id"], "p": r["p"], "pred_component": r["pred_component"],
                               "symptom": r["symptom"], "quote": quote(r["text"])} for r in fps]
    (out / "report.json").write_text(json.dumps(rep, indent=1))
    with open(out / "rows.jsonl", "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    def line(name, m):
        return (f"| {name} | {m['n']} | {m['pos']} | {m['tp']} | **{m['catch_rate']:.0%}** | {m['fp']} | "
                f"**{m['precision']:.2f}** | {m['fp_rate']:.1%} |")
    md = [f"# Self-heal {a.mode} detector replay ({model_tag}, threshold {th})", "",
          "| set | n | defects | caught | catch rate | FPs | precision | FP rate on non-defects |",
          "|---|---|---|---|---|---|---|---|",
          line("all", rep["all"]), line("dev split", rep["splits"]["dev"]), line("test split", rep["splits"]["test"]),
          line("random turns only", rep["random_sample"]), line("with prev reply", rep["with_prev"]),
          line("without prev reply", rep["without_prev"]), line("prefilter alone", rep["prefilter_only"]), "",
          f"Judge calls this run: {calls['n']} (errors {calls['err']}); unjudged after retry: {rep['errors']}.", "",
          "Resolved models (stage: model x calls; compare with the live detection's judge_model/confirm_model): "
          + ("; ".join(f"{s}: " + ", ".join(f"{m} x{n}" for m, n in sorted(d.items())) for s, d in models.items())
             or "none called (all cached)"), "",
          "## Per component", "", "| component | defects | caught |", "|---|---|---|"]
    md += [f"| {c} | {v['pos']} | {v['caught']} |" for c, v in rep["components"].items()]
    md += ["", f"## Misses ({len(misses)})", ""]
    md += [f"- `{m['id']}` [{m['component']}] {'prefilter miss' if not m['prefilter'] else 'p=' + str(m['p'])}: "
           f"\"{m['quote']}\"" for m in rep["misses"]]
    md += ["", f"## False positives ({len(fps)})", ""]
    md += [f"- `{f['id']}` p={f['p']} [{f['pred_component']}: {f['symptom']}]: \"{f['quote']}\""
           for f in rep["false_positives"]]
    md += ["", f"## Per row ({rep['samples']} sample(s){', confirm cascade' if confirm else ''})", "",
           "| id | label | samples at/above threshold | p per sample |", "|---|---|---|---|"]
    md += [f"| `{k}` | {v['label']} | {v['sample_hits']}/{len(v['ps'])} | {v['ps']} |" for k, v in rep["per_row"].items()]
    (out / "report.md").write_text("\n".join(md) + "\n")
    print(json.dumps({k: rep[k] for k in ("all", "prefilter_only", "splits", "random_sample", "errors", "per_row")},
                     indent=1))


if __name__ == "__main__":
    main()
