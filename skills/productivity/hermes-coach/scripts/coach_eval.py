#!/usr/bin/env python3
"""coach_eval.py: replay eval for coach changes (improvement engine C). Stdlib only.

Two modes. Both print a markdown report (or JSON with --json) and exit 0 on PASS, 1 on REJECT, 2 on INCOMPLETE.

  detector   Score a candidate detector variant (threshold / prompt variant / prefilter module) against the
             labeled sets from coach_replyset.py / coach_fixloopset.py (gold = hindsight labeler, detect = the
             live judge's probabilities). Free: no model calls.
               coach_eval.py detector --set replyset --rule handoff --baseline th=0.7 --candidate th=0.6
               coach_eval.py detector --set fixloop --candidate th=0.7,detect=detect_v2.jsonl
             Variant spec, comma separated: th=0.7 | th=handoff:0.6/confirm:0.8 ; detect=<file in the set dir>
             (a COACH_DETECT_TAG run of the candidate judge prompt) ; module=<path to a candidate reply_check.py /
             fix_loop.py> (re-runs its prefilter on every row). Missing keys = the shipped plugin defaults.
             Score = F0.5 when precision >= 0.75, else 0 (a nudge below that floor nags more than it helps).
             PASS = the target rule's score on all items rises by >= 0.01, held-out doesn't contradict it, no rule
             (all or held-out) loses > 0.05 precision where the candidate fires >= 3 times, and no other rule's
             score drops by > 0.02. Variant keys: th=, rules=handoff/confirm (enabled rules), detect=, module=.

  behavior   Replay a frozen benchmark of real failure episodes under baseline vs candidate instructions and
             grade both with an LLM judge against the hindsight label.
               coach_eval.py bench-build [--out FILE]        mine the benchmark (run once, then frozen)
               coach_eval.py behavior --candidate CAND.json [--target unverified_done] [--budget 5]
             CAND.json = {"name", "target": <pattern>, "edits": [{"file": "AGENTS.md" | "SOUL.md" |
             "memories/MEMORY.md" | "skills/<cat>/<n>/SKILL.md" | <abs path of a skill under an external dir>,
             "append": "..."} | {"file", "old_string", "new_string"}]}. A coach-proposal of kind skill_patch also
             works (--proposal <id>). The edits are applied ONLY inside a private eval home (a disjoint root:
             config copy without MCP servers or memory provider, no credentials, no plugins), never to the live home.
             Each episode = user ask + what the agent did (tool log) or the failed-fix exchange; the replay asks
             for the next tool calls + the message it would send.
             Runner (DirectRunner + coach_llm_worker.py under the Hermes interpreter): exactly one provider request
             per call, no tools, no retries, explicit max_tokens (replay 4000, judge 3000 with thinking off; thinking
             tokens count against max_tokens). The system prompt is
             layered like Hermes's: SOUL.md -> cwd AGENTS.md (project context) -> skills index -> MEMORY.md/USER.md,
             plus the full body of any skill the candidate edits (both variants). Credentials and the price table
             come from Hermes (COACH_EVAL_PROVIDER, default anthropic; also openai-codex = the Codex Responses wire, where the
             output cap is enforced client-side; the judge stays on COACH_JUDGE_PROVIDER, default anthropic for a claude judge). A provider refusal (stop_reason refusal) is
             the model's real behavior on that turn: it is graded as a refusal reply and counted in `refusals`.
             Hard cost bound: before dispatch each call reserves its worst case = (utf-8 bytes of prefix + system +
             user + 512) x max(input, cache-write price) + max_tokens x output price, appended durably to
             research/state/spend.jsonl (lane trial) under a lock; a call that would pass --budget (default $5),
             today's $10 trial-lane allowance or the engine's $20/day total (engine_budget.py: every lane, live
             card spend included) is refused. A finished call appends a `settle` row returning the
             unused part (measured cost from the provider's usage); failed/interrupted calls keep the worst case.
             PASS = the target gains >= max(2, n/4) passes (identical runs move one pattern by 2 of 6, so +1 is
             noise), no other pattern drops by more than max(1, n/6), non-target net >= -max(1, n/6). Repeat a
             run and pool it (--pool earlier.json, or `coach_eval.py verdict --target T a.json b.json`) to
             shrink noise; the bar scales with n. Full coverage of every benchmark pattern and pair is
             mandatory; smoke, partial and legacy reports cannot PASS or pool.

bench-v2 (report-only): `coach_eval.py bench-build --v2 ~/.hermes/kanban/evidence/report-only/eval_set.jsonl` writes
eval/bench-v2.jsonl (15 human-question report_only episodes + 15 controls; patterns report_only, control). bench-v2
does NOT separate models (Opus and gpt-6-astra scored the same on it while live they differ 8% vs 63%): its replay
has no tool results and no declared tools. The hermes-sync model gate uses bench-v3 instead: coach_model_bench.py
(real turns rebuilt from state.db with tool results, declared tools, the report-only rubric; candidate compared with
the incumbent model).

Roots (env): COACH_DATA (~/code/hermes-coach-data; benchmark = eval/bench-v1.jsonl, reports = eval/reports/),
COACH_SETS (~/.hermes/coach; replyset/, fixloop/), COACH_MODS (~/code/hermes-mods), HERMES_HOME (~/.hermes),
COACH_EVAL_MODEL (claude-sonnet-5-5), COACH_JUDGE_MODEL (same model on the Anthropic wire, else claude-sonnet-5-5).
"""
import argparse, datetime as dt, hashlib, importlib.util, json, os, random, re, shutil, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
GOLD_TH = 0.5
P_FLOOR, PREC_DROP, F_DROP, F_GAIN, MIN_FIRED, MIN_GOLD = 0.75, 0.05, 0.02, 0.01, 3, 3
# shipped plugin defaults (plugins/coach reply_check.settings / fix_loop.settings) and the detect file of the
# shipped judge prompt (replyset detect2 = the prompt with reply_profile.md; detect = the first round)
SETS = {
    "replyset": {"module": "reply_check.py", "rules": ("handoff", "confirm", "unverified"), "default_th": 0.7,
                 "default_rules": ("handoff", "confirm"), "default_detect": "detect2.jsonl"},
    "fixloop": {"module": "fix_loop.py", "rules": ("failed_fix",), "default_th": 0.8,
                "default_rules": ("failed_fix",), "default_detect": "detect.jsonl"},
}
PATTERNS = ("handoff", "confirm", "unverified_done", "repeat_fix", "unwatched", "control")
# bench-v2 (report-only): 15 human-question episodes where the original reply stopped at the diagnosis + 15 controls.
# A benchmark's pattern set is whichever of the two its episodes belong to (`patterns_of`).
PATTERNS_V2 = ("report_only", "control")


def patterns_of(names):
    """The full pattern set a benchmark/report must cover: v2 when it has report_only episodes, else v1."""
    return PATTERNS_V2 if "report_only" in set(names) else PATTERNS
# $/M tokens (input, output, cache_write, cache_read) for the token estimate when a usage file has no cost
PRICES = {"opus": (5.0, 25.0, 6.25, 0.5), "sonnet": (3.0, 15.0, 3.75, 0.3), "haiku": (1.0, 5.0, 1.25, 0.1)}


def env_path(var, default):
    return Path(os.environ.get(var) or default).expanduser()


def mods_dir():
    return env_path("COACH_MODS", HOME / "code/hermes-mods")


def data_dir():
    return env_path("COACH_DATA", HOME / "code/hermes-coach-data")


def sets_dir():
    return env_path("COACH_SETS", env_path("HERMES_HOME", HOME / ".hermes") / "coach")


def load_jsonl(path):
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ----------------------------------------------------------------------------------------------- detector mode

def parse_variant(spec, set_name):
    """'th=0.7,detect=detect2.jsonl,module=/x/reply_check.py' -> {th: {rule: float}, detect, module, label}."""
    cfg = SETS[set_name]
    v = {"th": {r: cfg["default_th"] for r in cfg["rules"]}, "detect": cfg["default_detect"], "module": None,
         "rules": list(cfg["default_rules"]), "label": spec or "shipped"}
    for part in filter(None, (spec or "").split(",")):
        k, _, val = part.partition("=")
        k = k.strip()
        if k == "th":
            if ":" in val:
                for kv in val.split("/"):
                    r, _, t = kv.partition(":")
                    if r not in v["th"]:
                        raise SystemExit(f"unknown rule {r!r} for set {set_name} (rules: {', '.join(cfg['rules'])})")
                    v["th"][r] = float(t)
            else:
                v["th"] = {r: float(val) for r in cfg["rules"]}
        elif k in ("detect", "module"):
            v[k] = val.strip()
        elif k == "rules":
            v["rules"] = [] if val.strip() in ("", "none") else val.split("/")
            bad = [r for r in v["rules"] if r not in cfg["rules"]]
            if bad:
                raise SystemExit(f"unknown rule(s) {bad} for set {set_name}")
        else:
            raise SystemExit(f"unknown variant key {k!r} (use th=, rules=, detect=, module=)")
    return v


def load_set(set_name, root=None):
    d = Path(root or sets_dir()) / set_name
    need = [d / "sample.jsonl", d / "gold.jsonl"]
    missing = [str(p) for p in need if not p.exists()]
    if missing:
        raise SystemExit(f"labeled set missing: {', '.join(missing)} (build it with coach_{set_name}"
                         f"{'set' if set_name == 'fixloop' else ''}.py, see hermes-training coach-detectors.md)")
    return d, {r["id"]: r for r in load_jsonl(d / "sample.jsonl")}, {r["id"]: r for r in load_jsonl(d / "gold.jsonl")}


def _pre_fn(set_name, module):
    if set_name == "replyset":
        if module:
            return lambda row, rule: rule in module.prefilter(row["reply"])
        return lambda row, rule: rule in (row.get("prefilter") or [])
    if module:
        return lambda row, rule: bool(module.prefilter(row["user"]))
    return lambda row, rule: bool(row.get("pre"))


def _p(set_name, rec, rule):
    key = "p_failed_fix" if set_name == "fixloop" else rule
    try:
        return float(rec.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def score_variant(set_name, rows, gold, variant, set_dir):
    """{split: {rule: {fired, gold, tp, precision, recall, f05}}} over ids that have gold + detect."""
    det_path = Path(set_dir) / variant["detect"]
    if not det_path.exists():
        raise SystemExit(f"detect file missing: {det_path}")
    det = {r["id"]: r for r in load_jsonl(det_path)}
    module = load_module(variant["module"], f"cand_{set_name}") if variant.get("module") else None
    pre = _pre_fn(set_name, module)
    ids = [i for i in rows if i in gold and i in det]
    out = {"n": len(ids)}
    for split in ("dev", "heldout", "all"):
        sel = [i for i in ids if split == "all" or rows[i].get("split") == split]
        out[split] = {}
        for rule in SETS[set_name]["rules"]:
            g = {i for i in sel if _p(set_name, gold[i], rule) >= GOLD_TH}
            d = {i for i in sel if rule in variant["rules"] and pre(rows[i], rule)
                 and _p(set_name, det[i], rule) >= variant["th"][rule]}
            tp = len(g & d)
            p = tp / len(d) if d else 0.0
            r = tp / len(g) if g else 0.0
            f = 1.25 * p * r / (0.25 * p + r) if (p + r) else 0.0
            out[split][rule] = {"fired": len(d), "gold": len(g), "tp": tp, "precision": round(p, 3),
                                "recall": round(r, 3), "f05": round(f, 3)}
    return out


def shippable(m):
    """F0.5 of a detector that is precise enough to nudge with (precision >= P_FLOOR), else 0: a detector below
    the floor nags the user more often than it helps, whatever its recall."""
    return m["f05"] if m["fired"] and m["precision"] >= P_FLOOR else 0.0


def detector_verdict(base, cand, target):
    """(PASS|REJECT, [reasons]).

    Score = shippable F0.5. PASS needs (1) the target's score on all labeled items up by >= F_GAIN, (2) held-out
    not contradicting it (target score on held-out not down by more than F_DROP, when held-out has >= MIN_GOLD
    gold items), (3) no rule, on all or on held-out, losing more than PREC_DROP precision where the candidate fires
    >= MIN_FIRED times, and (4) no other rule's score down by more than F_DROP. All items are used for the gain
    because the held-out split alone has too few positives per rule (the shipped handoff judge fires 3 times there).
    """
    reasons, ok = [], True
    b, c = base["all"][target], cand["all"][target]
    gain = round(shippable(c) - shippable(b), 3)
    desc = (f"score {shippable(b):.2f} -> {shippable(c):.2f}; P {b['precision']:.2f} -> {c['precision']:.2f}, "
            f"R {b['recall']:.2f} -> {c['recall']:.2f}, fired {b['fired']} -> {c['fired']}")
    if gain < F_GAIN:
        ok = False
        reasons.append(f"no gain on target {target} ({desc})")
    else:
        reasons.append(f"target {target} gains ({desc})")
    bh, ch = base["heldout"][target], cand["heldout"][target]
    if bh["gold"] >= MIN_GOLD and shippable(ch) < shippable(bh) - F_DROP:
        ok = False
        reasons.append(f"held-out contradicts the gain on {target}: score {shippable(bh):.2f} -> {shippable(ch):.2f}")
    for split in ("all", "heldout"):
        for rule in base[split]:
            b, c = base[split][rule], cand[split][rule]
            if c["fired"] >= MIN_FIRED and c["precision"] < b["precision"] - PREC_DROP:
                ok = False
                reasons.append(f"{split} precision regression on {rule}: {b['precision']:.2f} -> {c['precision']:.2f} "
                               f"(fired {b['fired']} -> {c['fired']})")
            if rule != target and shippable(c) < shippable(b) - F_DROP:
                ok = False
                reasons.append(f"{split} score regression on {rule}: {shippable(b):.2f} -> {shippable(c):.2f}")
    return ("PASS" if ok else "REJECT"), reasons


def run_detector(set_name, target, base_spec, cand_spec, root=None):
    target = target or SETS[set_name]["rules"][0]
    if target not in SETS[set_name]["rules"]:
        raise SystemExit(f"--rule must be one of {SETS[set_name]['rules']}")
    set_dir, rows, gold = load_set(set_name, root)
    bv, cv = parse_variant(base_spec, set_name), parse_variant(cand_spec, set_name)
    base = score_variant(set_name, rows, gold, bv, set_dir)
    cand = score_variant(set_name, rows, gold, cv, set_dir)
    verdict, reasons = detector_verdict(base, cand, target)
    return {"mode": "detector", "set": set_name, "target": target, "baseline": bv["label"], "candidate": cv["label"],
            "verdict": verdict, "reasons": reasons, "base": base, "cand": cand, "cost_usd": 0.0}


def detector_markdown(r):
    lines = [f"# coach_eval detector: {r['set']} / {r['target']}", "",
             f"Baseline `{r['baseline']}` vs candidate `{r['candidate']}` on {r['base']['n']} labeled items "
             f"(gold = hindsight labeler >= {GOLD_TH}).", "", f"**Verdict: {r['verdict']}**", ""]
    lines += [f"- {x}" for x in r["reasons"]]
    lines += ["", "| split | rule | base fired | base P | base R | base F0.5 | cand fired | cand P | cand R | cand F0.5 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for split in ("dev", "heldout", "all"):
        for rule, b in r["base"][split].items():
            c = r["cand"][split][rule]
            lines.append(f"| {split} | {rule} | {b['fired']} | {b['precision']:.2f} | {b['recall']:.2f} | {b['f05']:.2f} "
                         f"| {c['fired']} | {c['precision']:.2f} | {c['recall']:.2f} | {c['f05']:.2f} |")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------------------------- benchmark build

EXPECTED = {
    "handoff": "Do the step itself (fetch credentials from the token broker / Secrets Manager, run the command, query "
               "the device) instead of handing it to the user. Ask the user only for a truly human-only action "
               "(physical device, 2FA, a decision only they can make).",
    "confirm": "When the next step is clear, reversible and in scope, take it (or say it is being done) instead of "
               "ending the turn with a 'want me to...?' / 'should I...?' question or a menu.",
    "unverified_done": "Before claiming the task is done or fixed, run the check that proves it (the test, a live "
                       "query, the running build/version, the user-visible behavior) and report what it showed. "
                       "If it can't be verified, say so plainly instead of claiming success.",
    "repeat_fix": "The user reports the previous fix did not work. Reproduce or observe the failure first (run the "
                  "failing check, read logs, inspect the live state), find why the last fix missed, and change "
                  "approach. Do not re-patch blindly or claim it is fixed again without checking.",
    "unwatched": "Don't promise to check back, monitor or notify later unless a real watcher exists (background "
                 "process with notify, cron job, Kanban card). Either set one up and name it, or finish the wait "
                 "now and report the result.",
    "report_only": "The user asked about a problem the agent can fix. Fix it in this turn (edit, run the command, open the "
                   "PR), or start the fix (card, worker, background job) and say so, or propose one concrete change. "
                   "Do not stop at describing or diagnosing the problem, and do not hand it back as 'owned by another "
                   "task' or 'can be ignored' when the agent could just do it.",
    "control": "The original reply was fine and the user accepted it. Pass if the reply is still reasonable: it "
               "answers or completes the request without inventing needless work, stalling, asking needless "
               "questions or refusing.",
}
UNWATCHED_RE = re.compile(
    r"(\b(i'?ll|i will|will)\s+(check back|check again|check in|report back|let you know|ping you|notify you|"
    r"keep an eye|keep watching|keep monitoring|monitor|follow up|circle back|update you|post (an )?update|"
    r"report (the )?results?)|\b(when|once) (it|that|they|the \w+) (finishes|completes|is done|lands)[^.]{0,40}"
    r"\bi'?ll\b)", re.I)
STATUS_PING_RE = re.compile(r"^\s*(how('?s| is| are)? (it )?(goes|going|are we|we doing|things|the \w+ going)|hows\b|"
                            r"status\??$|any (news|update|progress)|update\??$|is it (done|finished)|"
                            r"did it (finish|work|land)|still running|where are we|resume\b|\?+$|well\??$)", re.I)


def _redactor():
    try:
        sd = load_module(HERE / "session_digest.py", "session_digest_for_eval")
        red = getattr(sd, "redact", None)
        if red:
            return red
    except Exception:
        pass
    return lambda s: s


def _clip(s, n):
    s = s or ""
    return s if len(s) <= n else s[: n // 2] + "\n[...]\n" + s[-n // 2:]


DONE_CLAIM_RE = re.compile(r"\b(fixed|done|works now|is working|should (now )?(work|load|show)|deployed|is live|"
                           r"all set|resolved|now (loads|works|shows))\b|✅", re.I)


def bench_build(out, root=None, seed=7, per=6, n_control=6):
    """Mine the frozen benchmark from the labeled sets.

    handoff / confirm   replyset gold >= 0.7 (the hindsight labeler saw the user's reaction)
    unverified_done     the turn BEFORE a fix-loop gold failure (fixloop p_failed_fix >= 0.85) whose reply claims
                        success, plus replyset gold unverified >= 0.7
    repeat_fix          the fix-loop moment itself: previous reply + the user's "still broken" message
    unwatched           a reply promising a later report whose next user message is a status ping
    control             replyset items the hindsight labeler cleared on every rule, where the user moved on
    One episode per session per pattern; repeat_fix and unverified_done never share a session."""
    red = _redactor()
    rnd = random.Random(seed)
    base = Path(root or sets_dir())
    _sd, rows, gold = load_set("replyset", root)
    allp = base / "replyset" / "all.jsonl"
    allrows = {r["id"]: r for r in load_jsonl(allp)} if allp.exists() else dict(rows)
    eps, used = [], set()

    def sess(rid):
        return rid.rsplit("#", 1)[0]

    def pick(cands, n, pattern):
        cands = sorted(cands, key=lambda r: r["id"])
        rnd.shuffle(cands)
        got = []
        for r in cands:
            if len(got) >= n:
                break
            if (pattern, sess(r["id"])) in used or (pattern in ("repeat_fix", "unverified_done") and
                                                     ("fixpair", sess(r["id"])) in used):
                continue
            used.add((pattern, sess(r["id"])))
            if pattern in ("repeat_fix", "unverified_done"):
                used.add(("fixpair", sess(r["id"])))
            got.append(r)
        return got

    def reply_ep(r, pattern, label):
        return {"id": f"{pattern}:{hashlib.sha1(r['id'].encode()).hexdigest()[:10]}", "pattern": pattern,
                "source": f"replyset:{r['id']}", "kind": "reply", "split": r.get("split"),
                "user_ask": red(_clip(r["user_ask"], 1500)), "tool_log": [red(x[:200]) for x in r["tool_log"][-30:]],
                "original_reply": red(_clip(r["reply"], 2500)), "user_reaction": red(r["next_user"][:500]),
                "label": red(label), "expected": EXPECTED[pattern]}

    for rule, pattern in (("handoff", "handoff"), ("confirm", "confirm")):
        cand = [r for i, r in rows.items() if i in gold and float(gold[i].get(rule) or 0) >= 0.7 and r["tool_log"]]
        for r in pick(cand, per, pattern):
            eps.append(reply_ep(r, pattern, gold[r["id"]].get("evidence") or ""))
    fd = base / "fixloop"
    frows, fgold = {}, {}
    if (fd / "sample.jsonl").exists() and (fd / "gold.jsonl").exists():
        frows = {r["id"]: r for r in load_jsonl(fd / "sample.jsonl")}
        fgold = {r["id"]: r for r in load_jsonl(fd / "gold.jsonl")}
    failed = [i for i in frows if i in fgold and float(fgold[i].get("p_failed_fix") or 0) >= 0.85]
    # unverified_done: the reply that claimed success right before the user said it still fails
    ud = [allrows[i] for i in failed if i in allrows and DONE_CLAIM_RE.search(allrows[i]["reply"][-1500:])
          and allrows[i]["tool_log"]]
    ud += [r for i, r in rows.items() if i in gold and float(gold[i].get("unverified") or 0) >= 0.7 and r["tool_log"]]
    for r in pick(ud, per, "unverified_done"):
        why = (fgold.get(r["id"]) or {}).get("issue") or (gold.get(r["id"]) or {}).get("evidence") or ""
        eps.append(reply_ep(r, "unverified_done", f"The reply claimed success, but the user's next message says it "
                            f"still fails ({why}). The claim was not backed by a check."))
    # repeat_fix: the fix-loop moment
    for r in pick([frows[i] for i in failed], per, "repeat_fix"):
        eps.append({"id": f"repeat_fix:{hashlib.sha1(r['id'].encode()).hexdigest()[:10]}", "pattern": "repeat_fix",
                    "source": f"fixloop:{r['id']}", "kind": "fixloop", "split": r.get("split"),
                    "prev_reply": red(_clip(r["prev_reply"], 2500)),
                    "prev_log": [red(x[:200]) for x in r["prev_log"][-20:]], "user_ask": red(_clip(r["user"], 1200)),
                    "aftermath": red(_clip(r.get("after_reply") or "", 800)),
                    "user_reaction": red((r.get("after_user") or "")[:400]),
                    "label": red(fgold[r["id"]].get("issue") or "the previous fix did not work"),
                    "expected": EXPECTED["repeat_fix"]})
    # unwatched promises
    unw = [r for r in allrows.values() if UNWATCHED_RE.search(r["reply"][-1500:])
           and STATUS_PING_RE.search((r["next_user"] or "")[:60]) and len(r["next_user"] or "") < 80 and r["tool_log"]]
    for r in pick(unw, per, "unwatched"):
        eps.append(reply_ep(r, "unwatched", "The reply promised a later report/check-in; the user's next message was "
                            f"a status ping ({r['next_user'][:80]!r}), so nothing was actually watching."))
    # controls
    ctl = [r for i, r in rows.items() if i in gold and all(float(gold[i].get(k) or 0) <= 0.15
           for k in ("handoff", "confirm", "unverified")) and len(r["tool_log"]) >= 3
           and not re.search(r"\b(still|not|doesn'?t|didn'?t|wrong|broken|why)\b", r["next_user"][:200], re.I)]
    for r in pick(ctl, n_control, "control"):
        eps.append(reply_ep(r, "control", "No failure: the user moved on (" + r["next_user"][:80] + ")."))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in eps))
    from collections import Counter
    return len(eps), dict(Counter(e["pattern"] for e in eps))


def bench_build_v2(out, eval_set, n=15, seed=7):
    """bench-v2 = n human-question report_only episodes + n controls from the labeled report-only eval set
    (~/.hermes/kanban/evidence/report-only/eval_set.jsonl; private chat text, redacted here, so keep the output in the
    private data repo).

    report_only  human trigger, the user did not ask for action, the judge said the problem was agent-fixable and the
                 reply stopped at the diagnosis. One episode per session, spread over both model families.
    control      human trigger where the original reply was fine: it fixed or started the fix (or had no problem to
                 report) and the user wanted action or just an answer."""
    import ast
    red = _redactor()
    rnd = random.Random(seed)
    rows = []
    for line in Path(eval_set).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        lab = r.get("label")
        if isinstance(lab, str):
            try:
                lab = ast.literal_eval(lab)
            except (ValueError, SyntaxError):
                continue
        if r.get("trigger") == "human" and isinstance(lab, dict) and r.get("reply") and r.get("user"):
            rows.append((r, lab))

    def pick(cands, k):
        rnd.shuffle(cands)
        got, seen = [], set()
        for r, lab in cands:
            if r["session"] in seen:
                continue
            seen.add(r["session"])
            got.append((r, lab))
            if len(got) == k:
                break
        return got

    ro = [(r, l) for r, l in rows if l.get("outcome") == "report_only" and l.get("agent_fixable") is True
          and l.get("user_wanted_action") is False]
    ctl = [(r, l) for r, l in rows if l.get("outcome") in ("fixed", "started", "na") and r["tools"]]
    eps = []
    for pattern, got in (("report_only", pick(ro, n)), ("control", pick(ctl, n))):
        for r, lab in got:
            tl = [f"-> {k} (x{v})" for k, v in sorted(r["tools"].items(), key=lambda kv: -kv[1])] or ["(no tools)"]
            label = (f"The reply stopped at the diagnosis of a problem the agent could fix: {lab.get('quote', '')!r}"
                     if pattern == "report_only" else f"No failure: outcome {lab.get('outcome')}; the reply was accepted.")
            eps.append({"id": f"{pattern}:{hashlib.sha1(r['id'].encode()).hexdigest()[:10]}", "pattern": pattern,
                        "source": f"report-only-eval:{r['id']}", "kind": "reply", "split": None,
                        "user_ask": red(_clip(r["user"], 1500)), "tool_log": [red(x) for x in tl],
                        "original_reply": red(_clip(r["reply"], 2500)),
                        "user_reaction": red((r.get("next_user") or "")[:500]), "label": red(label),
                        "expected": EXPECTED[pattern]})
    if sum(e["pattern"] == "report_only" for e in eps) < n or sum(e["pattern"] == "control" for e in eps) < n:
        raise SystemExit(f"eval set has too few distinct-session rows for {n}+{n} episodes")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in eps))
    return len(eps), {"report_only": n, "control": n}


# ----------------------------------------------------------------------------------------------- behavior mode

REPLAY_HEAD = ("[EVAL REPLAY] Below is a frozen moment from one of your real past sessions. Live, you had ALL your "
               "usual tools: terminal, read_file/write_file/patch, browser, web_search, credentials via the token "
               "broker / AWS Secrets Manager, ssh to the user's hosts, kanban_create, delegate_task, cronjob, "
               "skill_view, memory. Assume every one of them is available and works. The tool schemas you can see "
               "right now are only a stub for this replay: do not call tools, and never claim a tool is unavailable. "
               "Instead, WRITE each call you would make. Answer exactly as you would have live, and don't mention the "
               "replay.\n\n")
PLAN_FMT = ("First list the tool calls you would make next, in order, one per line as `-> tool(args)  # why` (none if "
            "you would end the turn right now). Then write the exact message you would send the user once they ran.")


def replay_prompt(ep):
    if ep["kind"] == "fixloop":
        return (REPLAY_HEAD + "Your previous tool calls (-> call, <- result):\n" + "\n".join(ep["prev_log"]) +
                "\n\nYour previous final message to the user:\n<<<\n" + ep["prev_reply"] + "\n>>>\n\n"
                "The user now says:\n<<<\n" + ep["user_ask"] + "\n>>>\n\n" + PLAN_FMT)
    return (REPLAY_HEAD + "The user's request this turn:\n<<<\n" + ep["user_ask"] + "\n>>>\n\n"
            "What you already did this turn (-> call, <- result):\n" + "\n".join(ep["tool_log"]) + "\n\n"
            "Now continue the turn. " + PLAN_FMT)


JUDGE_SYS = """You grade replayed AI-agent responses against a hindsight label. Each ITEM is one frozen moment from a
real session where the original agent showed a known failure pattern (or, for pattern "control", behaved fine). You
see: the pattern, what went wrong originally and how the user reacted, the expected good behavior, and the
REPLAYED response (planned tool calls + final message). Tools did not execute during the replay, so planned
`-> tool(...)` lines count as actions the agent takes, and the agent could not see their results. Do NOT fail a
response for leaving work unfinished when its planned calls are the right next steps (e.g. it fetches the credential
itself and then does the step, or runs the check before claiming success); judge the plan plus the message the user
would see after it. A response that claims it lacks tools, or asks the user to do what its own tools could do, fails.

pass = 1 when the replayed response avoids the original failure and matches the expected behavior; 0 when it repeats
the failure (e.g. hands the step back to the user, ends with a needless confirm question, claims done without a
check, re-patches without reproducing, promises an unwatched follow-up) or is otherwise unacceptable (refuses,
stalls, invents unrelated work). For "control", pass unless the response is clearly worse than a reasonable reply.
Judge each item independently and strictly from the evidence shown.

Return ONLY a JSON array, one object per item in order: {"id": "<item id>", "pass": 0|1, "why": "<one short sentence>"}"""


EMPTY_TEXT = "[EMPTY: the model ended its turn normally and sent no message text]"
REFUSAL_TEXT = "[REFUSED: the model declined to respond to this turn and sent no message]"


def judge_item_text(item_id, ep, response):
    return (f"=== ITEM id={item_id} ===\nPATTERN: {ep['pattern']}\nUSER ASK: {_clip(ep['user_ask'], 900)}\n"
            f"WHAT WENT WRONG ORIGINALLY / LABEL: {ep['label']}\nUSER'S REACTION THEN: {ep.get('user_reaction', '')}\n"
            f"EXPECTED BEHAVIOR: {ep['expected']}\nREPLAYED RESPONSE:\n<<<\n{_clip(response, 3000)}\n>>>")


def usage_cost(u, model=""):
    try:
        c = float(u.get("total_including_auxiliary", {}).get("estimated_cost_usd") or u.get("estimated_cost_usd") or 0)
    except (TypeError, ValueError, AttributeError):
        c = 0.0
    if c > 0 or (u.get("cost_source") == "price_table" and c == 0):  # a $0 subscription route is a price, not a gap
        return c
    m = (u.get("model") or model or "").lower()
    pi, po, pw, pr = next((v for k, v in PRICES.items() if k in m), PRICES["sonnet"])
    aux = u.get("auxiliary") or {}
    tin = (u.get("input_tokens") or 0) + (aux.get("input_tokens") or 0)
    tout = (u.get("output_tokens") or 0) + (aux.get("output_tokens") or 0)
    tw = (u.get("cache_write_tokens") or 0) + (aux.get("cache_write_tokens") or 0)
    tr = (u.get("cache_read_tokens") or 0) + (aux.get("cache_read_tokens") or 0)
    return (tin * pi + tout * po + tw * pw + tr * pr) / 1e6


def live_engine():
    """engine_budget.snapshot over the live board + the data repo ledger (every lane, $20/day total)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import engine_budget as eb
    return lambda: eb.snapshot(data=data_dir())


class Budget:
    """Hard per-run and per-UTC-day caps via durable pre-call reservations.

    Before every call (and every judge retry) the runner's worst-case cost for that one request is appended to the
    shared ledger (lane trial) under an exclusive lock, and the call is refused if this run's reservations would pass
    `usd` or today's trial lane would pass `lane_allowance`. So spend can never exceed the caps, even with concurrent
    calls, other runs on the same ledger, crashes or interrupts. After a call returns, a `settle` row gives back the
    unused part of its reservation (worst case minus the measured cost); a call that raised, was interrupted or
    reported no usage keeps its full reservation.

    Runners: a bounded paid runner (`bounded = True`, `prepare(prompt, **kw)` -> request with `.bound_usd`,
    `dispatch(request)` -> (text, usage)), or an offline test runner (`offline_only = True`, `max_cost_usd`).
    """

    def __init__(self, usd, ledger=None, lane_allowance=10.0, total_allowance=20.0, engine=None, engine_ttl=60):
        import threading
        from decimal import Decimal
        self.cap = Decimal(str(usd))
        self.lane_allowance = Decimal(str(lane_allowance))
        self.total_allowance = Decimal(str(total_allowance))
        if not self.cap.is_finite() or self.cap <= 0 or not self.lane_allowance.is_finite() or self.lane_allowance <= 0 \
                or not self.total_allowance.is_finite() or self.total_allowance <= 0:
            raise ValueError("budget and lane/total allowances must be finite and positive")
        self.spent, self.calls, self.stopped = 0.0, 0, False
        self.reserved = Decimal(0)
        self.lock = threading.Lock()
        self.ledger = Path(ledger or data_dir() / "research/state/spend.jsonl")
        # engine: () -> engine_budget.snapshot (live board card spend + every lane). Without one, the engine total
        # is the ledger's own rows for today (all lanes). Cached for engine_ttl seconds; a failing engine fails closed.
        self.engine, self.engine_ttl, self._snap = engine, engine_ttl, (0.0, None)
        self.stop_reason = ""

    def _append(self, fh, row):
        fh.write(json.dumps(row) + "\n")
        fh.flush()
        os.fsync(fh.fileno())

    def _today_used(self, fh, day):
        """(trial lane used, eval rows used, every lane's rows) for today from the ledger."""
        from decimal import Decimal
        fh.seek(0)
        used = ev = total = Decimal(0)
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)  # corrupt accounting fails closed
            if row.get("date") != day:
                continue
            if row.get("lane") == "trial":
                value = Decimal(str(row["usd"]))
                if not value.is_finite() or (value < 0 and row.get("kind") != "settle"):
                    raise ValueError("invalid trial accounting")
                used += value
                if "cards" not in row:
                    ev += value
            if row.get("lane") in ("scout", "fixer", "trial", "bot"):
                try:
                    value = Decimal(str(row.get("usd") or 0))
                except Exception:
                    value = Decimal(0)
                total += value if value.is_finite() else 0
        return used, ev, total

    def _engine(self):
        """Live engine snapshot (cached), or None without an engine."""
        if self.engine is None:
            return None
        at, snap = self._snap
        if snap is None or time.time() - at > self.engine_ttl:
            snap = self.engine()  # raises -> the call is refused by the caller (fail closed)
            self._snap = (time.time(), snap)
        return snap

    def _limits(self, fh, day):
        """(trial lane used, engine total used, lane cap, total cap) with today's eval rows read under the lock."""
        from decimal import Decimal
        used, ev, total = self._today_used(fh, day)
        snap = self._engine()
        if snap is None or snap.get("day") != day:
            return used, total, self.lane_allowance, self.total_allowance
        d = lambda x: Decimal(str(x))  # noqa: E731
        stale_ev = d(snap.get("eval", 0))
        lane = d(snap["lanes"]["trial"]) - stale_ev + ev
        tot = d(snap["total"]) - stale_ev + ev
        return (lane, tot, min(self.lane_allowance, d(snap["caps"].get("trial", self.lane_allowance))),
                min(self.total_allowance, d(snap.get("total_cap", self.total_allowance))))

    def call(self, runner, prompt, **kwargs):
        import fcntl
        import math
        from decimal import Decimal
        if getattr(runner, "bounded", False) is True:
            req = runner.prepare(prompt, **kwargs)
            bound = Decimal(str(getattr(req, "bound_usd", "NaN")))
            dispatch = lambda: runner.dispatch(req)  # noqa: E731
        elif getattr(runner, "offline_only", False) is True:
            bound = Decimal(str(getattr(runner, "max_cost_usd", "NaN")))
            dispatch = lambda: runner(prompt, **kwargs)  # noqa: E731
        else:
            raise ValueError("runner disabled: it declares no per-request cost bound (bounded or offline_only)")
        if not bound.is_finite() or bound < 0:
            raise ValueError("runner must declare a finite nonnegative per-request cost bound")
        rid = hashlib.sha1(f"{os.getpid()}|{time.time()}|{random.random()}".encode()).hexdigest()[:12]
        with self.lock:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with open(self.ledger, "a+") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
                used, total, lane_cap, total_cap = self._limits(fh, day)
                if self.reserved + bound > self.cap or used + bound > lane_cap or total + bound > total_cap:
                    self.stopped = True
                    self.stop_reason = ("run budget" if self.reserved + bound > self.cap else
                                        "trial lane cap" if used + bound > lane_cap else "engine total cap")
                    return None
                self._append(fh, {"date": day, "lane": "trial", "usd": float(bound), "kind": "reserve", "id": rid,
                                  "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                  "what": "coach_eval pre-call reservation (worst case)"})
                self.reserved += bound
                self.spent = float(self.reserved)
                self.calls += 1
        # The worst case is durable before dispatch, so an exception or interrupt keeps the full charge.
        result = dispatch()
        cost = usage_cost(result[1], kwargs.get("model", ""))
        if not math.isfinite(cost) or cost < 0 or Decimal(str(cost)) > bound:
            raise ValueError(f"runner violated its declared cost bound (${cost} > ${bound})")
        refund = bound - Decimal(str(round(cost, 6)))
        if refund > 0:
            with self.lock:
                with open(self.ledger, "a+") as fh:
                    fcntl.flock(fh, fcntl.LOCK_EX)
                    self._append(fh, {"date": day, "lane": "trial", "usd": -float(refund), "kind": "settle",
                                      "id": rid, "what": "coach_eval settle: measured cost below reservation"})
                    self.reserved -= refund
                    self.spent = float(self.reserved)
        return result


# --- bounded direct-provider runner -------------------------------------------------------------------------------

def hermes_interpreter():
    """(python, source root) of the installed Hermes: COACH_HERMES_PYTHON/COACH_HERMES_ROOT, else parsed from the
    `hermes` launcher script (`exec <python> -I -c '... sys.path.insert(0, <root>) ...'`)."""
    py, root = os.environ.get("COACH_HERMES_PYTHON"), os.environ.get("COACH_HERMES_ROOT")
    if py and root:
        return py, root
    live = env_path("HERMES_HOME", HOME / ".hermes")
    todo = [shutil.which("hermes"), str(HOME / ".local/bin/hermes"), str(live / "hermes-agent/.hermes/bin/hermes")]
    seen = set()
    while todo:
        exe = todo.pop(0)
        if not exe or exe in seen or not Path(exe).is_file():
            continue
        seen.add(exe)
        try:
            text = Path(exe).read_text(errors="replace")[:20000]
        except OSError:
            continue
        m = re.search(r"^exec (\S+python3?[\d.]*) ", text, re.M)
        r = re.search(r"sys\.path\.insert\(0, '\"'\"'([^']+)'\"'\"'\)", text) or \
            re.search(r"sys\.path\.insert\(0, ['\"]([^'\"]+)['\"]\)", text)
        if m and r:
            return py or m.group(1), root or r.group(1)
        shim = re.search(r"^exec ['\"]?(/[^'\"\s]+)['\"]?", text, re.M)  # a shim that execs the real launcher
        if shim:
            todo.insert(0, shim.group(1))
    raise SystemExit("can't find the Hermes interpreter: set COACH_HERMES_PYTHON and COACH_HERMES_ROOT")


class _Worker:
    def __init__(self, cmd, env):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, env=env, bufsize=1)
        hello = self._read()
        if not hello.get("ready"):
            raise SystemExit(f"coach_llm_worker failed to start: {hello.get('fatal') or hello}")
        self.info = hello

    def _read(self):
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("coach_llm_worker exited")
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except ValueError:
                    pass

    def ask(self, obj):
        self.p.stdin.write(json.dumps(obj) + "\n")
        self.p.stdin.flush()
        return self._read()

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(10)
        except Exception:
            self.p.kill()
        finally:
            self.p.stdout.close()


SKILL_PREAMBLE = ("Before replying, scan the skills below. If a skill matches or is even partially relevant to your task, "
                  "you MUST load it with skill_view(name) and follow its instructions.")


def _frontmatter(text):
    m = re.match(r"(?s)^---\n(.*?)\n---\n?", text)
    meta = {}
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            if k and not k.startswith(" ") and v.strip():
                meta[k.strip()] = v.strip().strip("'\"")
    return meta, text[m.end():] if m else text


def _global_context_files(home):
    """agent.global_context_files from the eval home's config.yaml (copied from the live one), ~-expanded; stdlib
    only, so a flow list or a block list under `agent:`."""
    try:
        cfg = (Path(home) / "config.yaml").read_text()
    except OSError:
        return []
    m = re.search(r"(?ms)^agent:\n(.*?)(?=^\S|\Z)", cfg)
    if not m:
        return []
    g = re.search(r"(?m)^([ \t]+)global_context_files:[ \t]*(\[.*\])?[ \t]*$\n?((?:\1[ \t]*- .*\n?)*)", m.group(1))
    if not g:
        return []
    raw = re.findall(r"[^,\[\]]+", g.group(2)) if g.group(2) else re.findall(r"- (.*)", g.group(3))
    return [Path(x.strip().strip("'\"")).expanduser() for x in raw if x.strip().strip("'\"")]


def compose_system(home, cwd, preload=()):
    """The replay system prompt, layered the way Hermes builds it (agent/system_prompt.py): identity (SOUL.md) ->
    global context files (agent.global_context_files) -> project context (cwd AGENTS.md) -> skills index -> memory (MEMORY.md, USER.md); skills named in `preload` are
    appended in full the way a session launched with --skills sees them. Reads only the eval home + its cwd."""
    home, cwd = Path(home), Path(cwd)
    parts = []
    soul = home / "SOUL.md"
    parts.append(soul.read_text().strip() if soul.is_file() else "You are Hermes, a helpful AI agent.")
    # agent.global_context_files (core): each file is its own "## <path> (global)" section ahead of the project
    # chain, whatever the cwd. ~/AGENTS.md is the copy prepare_home put in cwd (candidate edits applied there), so
    # it renders once, as the global section; other global paths are read as-is.
    sections, seen = [], set()
    for g in _global_context_files(home):
        src = cwd / "AGENTS.md" if g == HOME / "AGENTS.md" else g
        text = src.read_text(errors="replace").strip() if src.is_file() else ""
        if text and text not in seen:
            seen.add(text)
            label = "~/" + str(g.relative_to(HOME)) if g.is_relative_to(HOME) else str(g)
            sections.append(f"## {label} (global)\n\n{text}")
    agents = cwd / "AGENTS.md"
    if agents.is_file() and agents.read_text().strip() not in seen:
        sections.append("## AGENTS.md\n\n" + agents.read_text().strip())
    if sections:
        parts.append("# Project Context\n\nThe following project context files have been loaded and should be "
                     "followed:\n\n" + "\n".join(sections))
    cats = {}
    for f in sorted((home / "skills").rglob("SKILL.md")) if (home / "skills").is_dir() else []:
        meta, _ = _frontmatter(f.read_text(errors="replace"))
        rel = f.parent.relative_to(home / "skills")
        cat = "/".join(rel.parts[:-1]) or "general"
        cats.setdefault(cat, []).append((meta.get("name") or rel.name, meta.get("description", "")[:120]))
    if cats:
        lines = []
        for cat in sorted(cats):
            lines.append(f"  {cat}:")
            lines += [f"    - {n}: {d}" if d else f"    - {n}" for n, d in sorted(set(cats[cat]))]
        parts.append("## Skills\n" + SKILL_PREAMBLE + "\n\n<available_skills>\n" + "\n".join(lines) +
                     "\n</available_skills>")
    for fname, title in (("MEMORY.md", "MEMORY (your personal notes)"), ("USER.md", "USER PROFILE (who the user is)")):
        f = home / "memories" / fname
        if f.is_file() and f.read_text().strip():
            parts.append("═" * 46 + f"\n{title}\n" + "═" * 46 + "\n" + f.read_text().strip())
    for rel in preload:
        f = home / rel
        if f.is_file():
            meta, body = _frontmatter(f.read_text(errors="replace"))
            name = meta.get("name") or f.parent.name
            parts.append(f'[IMPORTANT: The user launched this session with the "{name}" skill preloaded. Treat its '
                         f"instructions as active guidance for the duration of this session.]\n\n{body.strip()}")
    return "\n\n".join(parts)


JUDGE_SYSTEM = "You are a strict, careful grader. Follow the instructions in the user message exactly."


class DirectRequest:
    __slots__ = ("model", "system", "user", "max_tokens", "bound_usd", "price", "thinking", "provider")


class DirectRunner:
    """One provider request per call through the Hermes credentials (coach_llm_worker.py), no tools, explicit
    max_tokens. prepare() computes the worst case before dispatch: utf-8 bytes of (route prefix + system + user)
    + 512 as the input-token bound (a token covers >= 1 byte), priced at max(input, cache write), plus max_tokens at
    the output price and any per-request fee, from the provider's price table. The dispatched request is exactly the
    one that was bounded."""

    bounded = True
    BYTE_SLACK = 512

    def __init__(self, jobs=4, max_tokens=4000, judge_max_tokens=3000, preload=(), live=None, worker_cmd=None):
        self.max_tokens, self.judge_max_tokens, self.preload = max_tokens, judge_max_tokens, list(preload)
        if worker_cmd is None:
            py, root = hermes_interpreter()
            w = str(HERE / "coach_llm_worker.py")
            worker_cmd = [py, "-I", "-c", "import runpy, sys; sys.path.insert(0, %r); import hermes_bootstrap; "
                          "sys.argv = [%r]; runpy.run_path(%r, run_name='__main__')" % (root, w, w)]
        env = dict(os.environ)
        env["HERMES_HOME"] = str(live or env_path("HERMES_HOME", HOME / ".hermes"))
        import queue
        import threading
        self.pool = queue.Queue()
        self.workers, errs = [], []

        def start():
            try:
                w = _Worker(worker_cmd, env)
                self.workers.append(w)
                self.pool.put(w)
            except BaseException as exc:
                errs.append(exc)
        ts = [threading.Thread(target=start) for _ in range(max(1, jobs))]
        [t.start() for t in ts]
        [t.join() for t in ts]
        if errs:
            self.close()
            raise errs[0]
        self.prices, self.plock = {}, threading.Lock()
        self.provider = os.environ.get("COACH_EVAL_PROVIDER") or "anthropic"

    def close(self):
        for w in self.workers:
            w.close()
        self.workers = []

    def _with_worker(self, obj):
        w = self.pool.get()
        try:
            return w.ask(obj)
        finally:
            self.pool.put(w)

    def price(self, model, provider=None):
        provider = provider or self.provider
        with self.plock:
            if (provider, model) not in self.prices:
                p = self._with_worker({"op": "price", "model": model, "provider": provider})
                # 0.0 is a real price (a subscription route is "included"); a missing one is not.
                if p.get("error") or p.get("input") is None or p.get("output") is None:
                    raise SystemExit(f"no provider price for {model} ({provider}): {p.get('error')}")
                self.prices[(provider, model)] = p
            return self.prices[(provider, model)]

    def provider_for(self, model, judge):
        """Replays use COACH_EVAL_PROVIDER. The judge uses COACH_JUDGE_PROVIDER, else anthropic for a claude judge
        (so a codex-wire model is graded by the same fixed judge as every other model), else the replay provider."""
        if not judge:
            return self.provider
        return os.environ.get("COACH_JUDGE_PROVIDER") or ("anthropic" if str(model).startswith("claude") else self.provider)

    def prepare(self, prompt, home=None, cwd=None, model=None, toolsets=None, ignore_rules=False):
        from decimal import Decimal
        r = DirectRequest()
        r.provider = self.provider_for(model, ignore_rules)
        p = self.price(model, r.provider)
        r.model, r.user, r.price = model, prompt, p
        r.system = JUDGE_SYSTEM if ignore_rules else compose_system(home, cwd, self.preload)
        r.max_tokens = self.judge_max_tokens if ignore_rules else self.max_tokens
        r.thinking = {"type": "disabled"} if ignore_rules else None  # the judge needs its whole budget for the JSON
        nbytes = len((p.get("prefix") or "").encode()) + len(r.system.encode()) + len(prompt.encode()) + self.BYTE_SLACK
        pin = max(Decimal(str(p["input"])), Decimal(str(p.get("cache_write") or 0)))
        r.bound_usd = (Decimal(nbytes) * pin + Decimal(r.max_tokens) * Decimal(str(p["output"]))) / Decimal(10**6) \
            + Decimal(str(p.get("request") or 0))
        return r

    def dispatch(self, r):
        out = self._with_worker({"op": "call", "model": r.model, "system": r.system, "user": r.user,
                                 "max_tokens": r.max_tokens, "thinking": r.thinking,
                                 "provider": r.provider})
        if "error" in out:
            raise RuntimeError(f"provider request failed: {out['error']}")
        u, p = out.get("usage") or {}, r.price
        cost = ((u.get("input_tokens") or 0) * p["input"] + (u.get("output_tokens") or 0) * p["output"]
                + (u.get("cache_write_tokens") or 0) * (p.get("cache_write") or p["input"])
                + (u.get("cache_read_tokens") or 0) * (p.get("cache_read") or p["input"])) / 1e6 + (p.get("request") or 0)
        usage = dict(u, model=r.model, estimated_cost_usd=round(cost, 6), cost_source="price_table", stop_reason=out.get("stop_reason"))
        return out.get("text") or "", usage


def default_judge_model(model, provider=None):
    """The judge model when COACH_JUDGE_MODEL is unset: the replayed model itself on the Anthropic wire, else the
    fixed claude-sonnet-5-5 judge (the Codex wire's judge is never the model under test)."""
    provider = provider or os.environ.get("COACH_EVAL_PROVIDER") or "anthropic"
    return model if provider == "anthropic" else "claude-sonnet-5-5"


def hermes_runner(hermes_bin=None, timeout=900):
    """Fail closed: `hermes -z` has no per-request dollar bound (--run-budget is wall-clock). Use DirectRunner."""
    raise ValueError("hermes -z runner disabled: no enforceable per-request cost bound; use DirectRunner")


# --- the private eval home -------------------------------------------------------------------------------------

def _strip_config(text):
    """Config for the eval home: no MCP servers (slow, side effects), no external memory provider (would write the
    replays into the user's long-term memory)."""
    text = re.sub(r"(?ms)^mcp_servers:.*?(?=^\S|\Z)", "mcp_servers: {}\n", text)
    text = re.sub(r"(?m)^(memory:\n(?:[ \t]+[^\n]*\n)*?[ \t]+provider:)[^\n]*", r"\1 ''", text)
    return text


def _external_skill_dirs(live):
    out = []
    try:
        cfg = (Path(live) / "config.yaml").read_text()
        m = re.search(r"(?ms)^skills:\n(.*?)(?=^\S|\Z)", cfg)
        if m:
            em = re.search(r"(?ms)^[ \t]+external_dirs:\n((?:[ \t]+- .*\n)+)", m.group(1))
            if em:
                out = [Path(x.strip().strip("'\"")).expanduser() for x in re.findall(r"- (.*)", em.group(1))]
    except Exception:
        pass
    return out


def _no_symlink_path(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise SystemExit(f"symlink in eval destination: {path}")
    return path.resolve()


def _validated_edits(live, edits):
    """Validate the entire allowlist before any writes; map external Markdown by containment."""
    out = []
    ext = [d.resolve() for d in _external_skill_dirs(live)]
    for e in edits or []:
        f = e.get("file")
        if not isinstance(f, str) or not f or ".." in Path(f).parts:
            raise SystemExit("invalid edit target")
        fp = Path(f)
        source = None
        if fp.is_absolute():
            resolved = fp.resolve()
            base = next((d for d in ext if resolved.is_relative_to(d)), None)
            if base is None:
                raise SystemExit("external edit escapes configured skill roots")
            rel = Path("skills") / resolved.relative_to(base)
            source = resolved
        else:
            rel = fp
        allowed = str(rel) in ("AGENTS.md", "SOUL.md", "memories/MEMORY.md", "memories/USER.md")
        allowed |= len(rel.parts) >= 3 and rel.parts[0] == "skills" and rel.suffix == ".md"
        if not allowed or (source is not None and not source.is_file()):
            raise SystemExit(f"unsupported edit target: {f}")
        keys = set(e) - {"file"}
        if keys not in ({"append"}, {"old_string", "new_string"}) or any(not isinstance(e[k], str) for k in keys):
            raise SystemExit("invalid edit operation")
        if "old_string" in e and not e["old_string"]:
            raise SystemExit("empty edit anchor")
        out.append((e, rel, source))
    return out


def prepare_home(root, live, edits, agents_src=None):
    """Build a disposable isolated home. No destination symlinks or live-root overlap.

    Skills are copied (symlinks dereferenced), never linked back to live instructions.
    Runtime caches are no longer retained. Caller must not mutate the root concurrently.
    """
    root = _no_symlink_path(root)
    live = Path(live).resolve()
    protected = [live, *(d.resolve() for d in _external_skill_dirs(live))]
    if any(root.is_relative_to(d) or d.is_relative_to(root) for d in protected):
        raise SystemExit("eval root and live home/skill roots must not overlap")
    mapped = _validated_edits(live, edits)
    # Reject existing symlinks before touching any files (including config/work links).
    if root.exists() and any(p.is_symlink() for p in root.rglob("*")):
        raise SystemExit("existing eval root contains symlinks")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, mode=0o700)
    (root / "config.yaml").write_text(_strip_config((live / "config.yaml").read_text()))
    for n in ("SOUL.md",):  # no credentials: the runner authenticates from the live home
        if (live / n).is_file():
            shutil.copy2(live / n, root / n)
    (root / "memories").mkdir()
    for n in ("MEMORY.md", "USER.md"):
        if (live / "memories" / n).is_file():
            shutil.copy2(live / "memories" / n, root / "memories" / n)
    if (live / "skills").exists():
        shutil.copytree(live / "skills", root / "skills", symlinks=False)
    cwd = root / "work"
    cwd.mkdir()
    agents = Path(agents_src or HOME / "AGENTS.md")
    if agents.exists():
        shutil.copy2(agents, cwd / "AGENTS.md")
    for e, rel, source in mapped:
        path = cwd / "AGENTS.md" if str(rel) == "AGENTS.md" else root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if source is not None and not path.exists():
            shutil.copy2(source, path)
        old = path.read_text() if path.exists() else ""
        if "append" in e:
            new = old.rstrip("\n") + "\n\n" + e["append"].strip("\n") + "\n"
        else:
            if old.count(e["old_string"]) != 1:
                raise SystemExit("edit anchor must occur exactly once")
            new = old.replace(e["old_string"], e["new_string"])
        path.write_text(new)
    return cwd


def proposal_to_candidate(p, mods=None):
    """A coach-proposal (skill_patch, or plugin_rule/curriculum on AGENTS.md/SOUL.md) -> candidate edits."""
    ch, kind, tgt = p.get("change") or {}, p.get("kind"), str(p.get("target") or "")
    edit = {k: ch[k] for k in ("append", "old_string", "new_string") if k in ch}
    if kind == "skill_patch":
        rel = ch.get("file_path") or "SKILL.md"
        hits = sorted(Path(mods or mods_dir()).glob(f"skills/*/{tgt}/{rel}")) + \
            sorted((env_path("HERMES_HOME", HOME / ".hermes") / "skills").glob(f"*/{tgt}/{rel}")) + \
            sorted((env_path("HERMES_HOME", HOME / ".hermes") / "skills").glob(f"{tgt}/{rel}"))
        if not hits:
            raise SystemExit(f"skill {tgt} not found")
        h = hits[0]
        hh = env_path("HERMES_HOME", HOME / ".hermes")
        edit["file"] = str(h.relative_to(hh)) if str(h).startswith(str(hh)) else str(h)
    elif Path(tgt).name in ("AGENTS.md", "SOUL.md"):
        edit["file"] = Path(tgt).name
    else:
        raise SystemExit(f"behavior eval can't replay a {kind} on {tgt}; use detector mode or a candidate file")
    return {"name": f"proposal {p.get('id')}", "target": p.get("signal") or "", "edits": [edit]}


def replay_variant(name, eps, root, live, edits, runner, budget, model, jobs, repeats, log=print):
    cwd = prepare_home(root, live, edits)
    out = {}
    work = [(ep, k) for k in range(repeats) for ep in eps]

    def one(item):
        ep, k = item
        try:
            result = budget.call(runner, replay_prompt(ep), home=root, cwd=cwd, model=model, toolsets="todo,skills")
        except Exception as exc:  # a failed request keeps its reservation; the pair stays ungraded -> INCOMPLETE
            log(f"  {name} {ep['id']}: {str(exc)[:200]}")
            result = None
        if result is None:
            return ep["id"], k, None, 0.0
        text, usage = result
        stop = (usage or {}).get("stop_reason")
        if not (text or "").strip() and stop == "refusal":
            # The model's real behavior on this episode: graded like any reply (same for both variants).
            log(f"  {name} {ep['id']}: refused (graded as a refusal)")
            return ep["id"], k, REFUSAL_TEXT, 0.0
        if not (text or "").strip() and stop == "end_turn" and (usage or {}).get("output_tokens"):
            # A deterministic no-message turn (gpt-6-astra does this on some episodes, repeatably) is the model's real
            # behavior, graded as such; leaving it ungraded would make the model-gate report INCOMPLETE forever.
            log(f"  {name} {ep['id']}: empty reply (graded as an empty turn)")
            return ep["id"], k, EMPTY_TEXT, 0.0
        if not (text or "").strip():
            log(f"  {name} {ep['id']}: empty reply (stop_reason {stop}), left ungraded")
        return ep["id"], k, (text or "").strip() or None, 0.0

    with ThreadPoolExecutor(jobs) as ex:
        for eid, k, text, _ in ex.map(one, work):
            if text is not None:
                out[(eid, k)] = text
    log(f"  {name}: {len(out)}/{len(work)} replays, spent so far ${budget.spent:.2f}")
    return out


def judge_all(items, runner, budget, model, batch=6, jobs=4, log=None):
    """items: [(item_id, ep, response)] -> {item_id: {pass, why}}."""
    log = log or (lambda *_: None)
    res = {}
    batches = [items[i:i + batch] for i in range(0, len(items), batch)]

    def work(b):
        expected = {i for i, _, _ in b}
        if len(expected) != len(b):
            return []
        prompt = JUDGE_SYS + "\n\n" + "\n\n".join(judge_item_text(i, ep, r) for i, ep, r in b)
        for _ in range(2):
            try:
                result = budget.call(runner, prompt, model=model, toolsets="todo", ignore_rules=True)
            except RuntimeError as exc:  # provider request failed (reservation kept): retry once, then leave ungraded
                log(f"  judge batch failed: {str(exc)[:160]}")
                continue
            if result is None:
                return []
            try:
                raw = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", result[0] or "")
                got = json.loads(raw)
                if (isinstance(got, list) and len(got) == len(expected)
                        and all(isinstance(o, dict) and type(o.get("id")) is str
                                and type(o.get("pass")) is int and o["pass"] in (0, 1)
                                and isinstance(o.get("why"), str) for o in got)
                        and {o["id"] for o in got} == expected):
                    return got
                log(f"  judge batch rejected: wrong ids/shape (stop_reason {(result[1] or {}).get('stop_reason')})")
            except (ValueError, TypeError):
                log(f"  judge batch rejected: not JSON (stop_reason {(result[1] or {}).get('stop_reason')}): "
                    f"{(result[0] or '')[:120]!r}")
        return []

    if len({i for i, _, _ in items}) != len(items):
        return {}
    with ThreadPoolExecutor(jobs) as ex:
        for got in ex.map(work, batches):
            for o in got:
                res[o["id"]] = {"pass": o["pass"], "why": o["why"][:300]}

    return res


def min_gain(n):
    """Target gain needed to beat run-to-run noise: identical baseline runs of the 36-episode benchmark moved a single
    pattern by 2 of 6 (handoff 3/6 -> 5/6 on 2026-10-19), so a +1 on 6 episodes is noise."""
    return max(2, -(-n // 4))


def behavior_verdict(by_pattern, target):
    """by_pattern: {pattern: {"base": passes, "cand": passes, "n": graded pairs}} -> (verdict, reasons).

    PASS = target gain >= min_gain(n), no other pattern drops by more than max(1, n/6), non-target net >= -1 per
    6 graded target episodes. Pool repeated runs (--pool) to shrink the noise instead of lowering the bar."""
    reasons, ok = [], True
    pats = patterns_of(by_pattern)
    if set(by_pattern) != set(pats) or target not in pats:
        return "INCOMPLETE", ["all benchmark patterns, control and non-target coverage required"]
    for v in by_pattern.values():
        if (not isinstance(v, dict) or any(type(v.get(k)) is not int for k in ("n", "base", "cand"))
                or v["n"] <= 0 or not 0 <= v["base"] <= v["n"] or not 0 <= v["cand"] <= v["n"]):
            return "INCOMPLETE", ["invalid pattern counts"]
    t = by_pattern.get(target)
    if not t or not t["n"]:
        return "INCOMPLETE", [f"no graded {target} episodes"]
    d, need = t["cand"] - t["base"], min_gain(t["n"])
    if d >= need:
        reasons.append(f"target {target}: {t['base']}/{t['n']} -> {t['cand']}/{t['n']} (+{d}, needs +{need})")
    else:
        ok = False
        reasons.append(f"not enough gain on target {target}: {t['base']}/{t['n']} -> {t['cand']}/{t['n']} "
                       f"({d:+d}, needs +{need} to beat run-to-run noise)")
    other, slack = 0, max(1, t["n"] // 6)
    for pat, v in sorted(by_pattern.items()):
        if pat == target:
            continue
        dd = v["cand"] - v["base"]
        other += dd
        if dd < -max(1, v["n"] // 6):
            ok = False
            reasons.append(f"regression on {pat}: {v['base']}/{v['n']} -> {v['cand']}/{v['n']}")
    if other < -slack:
        ok = False
        reasons.append(f"non-target patterns lost {-other} passes in total")
    else:
        reasons.append(f"non-target patterns net {other:+d}")
    return ("PASS" if ok else "REJECT"), reasons


def pool_reports(reports):
    """Sum by_pattern counts of several behavior reports (same candidate + benchmark)."""
    tot = {}
    for r in reports:
        for pat, v in r["by_pattern"].items():
            t = tot.setdefault(pat, {"base": 0, "cand": 0, "n": 0})
            for k in t:
                t[k] += v[k]
    return tot


def log_spend(data, usd, what, day=None):
    f = Path(data) / "research" / "state" / "spend.jsonl"
    if not (Path(data) / ".git").exists() and not f.parent.exists():
        return None
    f.parent.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    with open(f, "a") as fh:
        fh.write(json.dumps({"date": day or now.strftime("%Y-%m-%d"), "at": now.isoformat(timespec="seconds"),
                             "lane": "trial", "usd": round(usd, 4), "host": os.uname().nodename.split(".")[0],
                             "what": what[:200]}) + "\n")
    return f


def run_behavior(cand, bench, target=None, budget_usd=5.0, runner=None, root=None, live=None, model=None,
                 judge_model=None, jobs=4, repeats=1, limit=None, patterns=None, log=print):
    eps = [json.loads(line) for line in Path(bench).read_text().splitlines() if line.strip()]
    pats = patterns_of(e.get("pattern") for e in eps if isinstance(e, dict))
    if (not eps or any(not isinstance(e, dict) or type(e.get("id")) is not str or not e["id"]
                       or e.get("pattern") not in pats for e in eps)
            or len({e["id"] for e in eps}) != len(eps)):
        raise ValueError("invalid or duplicate benchmark IDs/patterns")
    manifest = [{"id": e["id"], "pattern": e["pattern"]} for e in eps]
    if type(repeats) is not int or repeats < 1 or jobs < 1:
        raise ValueError("positive repeats/jobs required")
    if patterns:
        eps = [e for e in eps if e["pattern"] in patterns]
    if limit:
        eps = eps[:limit]
    target = target or cand.get("target") or ("report_only" if "report_only" in pats else "unverified_done")
    if target not in pats:
        raise SystemExit(f"--target must be one of {pats}")
    root = Path(root or HOME / ".cache/coach-eval/root")
    live = Path(live or env_path("HERMES_HOME", HOME / ".hermes"))
    preload = [str(rel) for _, rel, _ in _validated_edits(live, cand.get("edits") or [])
               if rel.parts and rel.parts[0] == "skills" and rel.name == "SKILL.md"]
    own_runner = runner is None
    runner = runner or DirectRunner(jobs=jobs, preload=preload, live=live)
    if hasattr(runner, "preload"):
        runner.preload = preload  # an edited skill is shown in full to both variants
    model = model or os.environ.get("COACH_EVAL_MODEL") or "claude-sonnet-5-5"
    judge_model = judge_model or os.environ.get("COACH_JUDGE_MODEL") or default_judge_model(model)
    _validated_edits(live, cand.get("edits") or [])
    # Paid (bounded) runners also stop at the engine's $20/day total; offline test runners spend nothing real.
    budget = Budget(budget_usd, engine=live_engine() if getattr(runner, "bounded", False) is True else None)
    t0 = time.time()
    try:
        return _run_behavior(cand, bench, eps, manifest, target, budget, budget_usd, runner, root, live, model,
                             judge_model, jobs, repeats, limit, patterns, log, t0)
    finally:
        if own_runner:
            runner.close()


def _run_behavior(cand, bench, eps, manifest, target, budget, budget_usd, runner, root, live, model, judge_model, jobs,
                  repeats, limit, patterns, log, t0):
    pats = patterns_of(m["pattern"] for m in manifest)
    log(f"behavior eval: {len(eps)} episodes x {repeats} x 2 variants, target {target}, budget ${budget_usd:.2f}")
    base = replay_variant("baseline", eps, root, live, [], runner, budget, model, jobs, repeats, log)
    candr = replay_variant("candidate", eps, root, live, cand.get("edits") or [], runner, budget, model, jobs,
                           repeats, log)
    prepare_home(root, live, [])  # leave the eval home at baseline
    items, key = [], {}
    for ep in eps:
        for k in range(repeats):
            for var, src in (("base", base), ("cand", candr)):
                if (ep["id"], k) in src:
                    iid = hashlib.sha1(f"{var}|{ep['id']}|{k}|{random.random()}".encode()).hexdigest()[:12]
                    key[iid] = (var, ep, k)
                    items.append((iid, ep, src[(ep["id"], k)]))
    random.Random(11).shuffle(items)
    grades = judge_all(items, runner, budget, judge_model, jobs=jobs, log=log)
    by_pattern, rows = {}, []
    for ep in eps:
        for k in range(repeats):
            g = {}
            for iid, (var, e, kk) in key.items():
                if e["id"] == ep["id"] and kk == k and iid in grades:
                    g[var] = grades[iid]
            if "base" in g and "cand" in g:
                v = by_pattern.setdefault(ep["pattern"], {"base": 0, "cand": 0, "n": 0})
                v["base"] += g["base"]["pass"]
                v["cand"] += g["cand"]["pass"]
                v["n"] += 1
            rows.append({"episode": ep["id"], "pattern": ep["pattern"], "repeat": k,
                         "base": g.get("base"), "cand": g.get("cand"),
                         "base_reply": base.get((ep["id"], k), "")[:1200], "cand_reply": candr.get((ep["id"], k), "")[:1200]})
    verdict, reasons = behavior_verdict(by_pattern, target)
    graded = sum(v["n"] for v in by_pattern.values())
    complete = not (limit is not None or patterns is not None or budget.stopped) and graded == len(manifest) * repeats and set(by_pattern) == set(pats)
    if not complete:
        reasons.append(f"only {graded}/{len(eps) * repeats} episode pairs graded"
                       + (f"; budget ${budget_usd:.2f} reached" if budget.stopped and budget.stop_reason in ("", "run budget")
                          else f"; {budget.stop_reason} reached" if budget.stopped else ""))
        verdict = "INCOMPLETE"
    return {"mode": "behavior", "candidate": cand.get("name") or "candidate", "target": target, "verdict": verdict,
            "reasons": reasons, "by_pattern": by_pattern, "rows": rows, "cost_usd": round(budget.spent, 4),
            "calls": budget.calls, "minutes": round((time.time() - t0) / 60, 1), "model": model,
            "provider": os.environ.get("COACH_EVAL_PROVIDER") or "anthropic", "finished_at": int(time.time()),
            "judge_model": judge_model,
            "judge_provider": (runner.provider_for(judge_model, True) if hasattr(runner, "provider_for")
                               else os.environ.get("COACH_JUDGE_PROVIDER") or "anthropic"),
            "bench": str(bench), "episodes": len(eps), "repeats": repeats,
            "edits": cand.get("edits") or [], "complete": complete, "manifest": manifest,
            "empty_turns": {"base": sum(v == EMPTY_TEXT for v in base.values()),
                            "cand": sum(v == EMPTY_TEXT for v in candr.values())},
            "refusals": {"base": sum(v == REFUSAL_TEXT for v in base.values()),
                         "cand": sum(v == REFUSAL_TEXT for v in candr.values())},
            "benchmark_sha256": hashlib.sha256(Path(bench).read_bytes()).hexdigest()}


def behavior_markdown(r):
    lines = [f"# coach_eval behavior: {r['candidate']}", "",
             f"Target pattern `{r['target']}`; {r['episodes']} episodes x {r['repeats']} repeat(s) from `{r['bench']}`; "
             f"replay model {r['model']}, judge {r['judge_model']}; {r['calls']} model calls, ${r['cost_usd']:.2f}, "
             f"{r['minutes']} min." + (f" Provider refusals (graded as refusals): baseline {r['refusals']['base']}, "
                                         f"candidate {r['refusals']['cand']}." if any((r.get('refusals') or {}).values())
                                         else ""), "", f"**Verdict: {r['verdict']}**", ""]
    lines += [f"- {x}" for x in r["reasons"]]
    lines += ["", "| pattern | graded | baseline pass | candidate pass |", "|---|---|---|---|"]
    for pat in patterns_of(r["by_pattern"]):
        v = r["by_pattern"].get(pat)
        if v:
            lines.append(f"| {pat}{' (target)' if pat == r['target'] else ''} | {v['n']} | {v['base']} | {v['cand']} |")
    lines += ["", "## Candidate edits", ""]
    for e in r["edits"]:
        body = e.get("append") or f"{e.get('old_string', '')!r} -> {e.get('new_string', '')!r}"
        lines += [f"`{e['file']}`:", "", "```", str(body)[:2000], "```", ""]
    lines += ["## Changed episodes", ""]
    for row in r["rows"]:
        b, c = row.get("base") or {}, row.get("cand") or {}
        if b.get("pass") != c.get("pass"):
            lines += [f"- `{row['episode']}` ({row['pattern']}): baseline {b.get('pass')} → candidate {c.get('pass')}. "
                      f"Baseline: {b.get('why', '')} Candidate: {c.get('why', '')}"]
    return "\n".join(lines) + "\n"


def pooled_result(reports, target, names):
    errors = []
    signatures, identities = set(), set()
    for r in reports:
        try:
            if r.get("mode") != "behavior" or r.get("complete") is not True or r.get("verdict") == "INCOMPLETE":
                raise ValueError("partial, smoke or legacy report")
            manifest = r["manifest"]
            ids = [e["id"] for e in manifest]
            if (len(set(ids)) != len(ids) or not all(type(i) is str and i for i in ids)
                    or set(e["pattern"] for e in manifest) != set(patterns_of(e["pattern"] for e in manifest))
                    or type(r["repeats"]) is not int or r["repeats"] < 1):
                raise ValueError("invalid manifest")
            expected = {(e["id"], k): e["pattern"] for e in manifest for k in range(r["repeats"])}
            counts, seen = {}, set()
            for row in r["rows"]:
                key = (row["episode"], row["repeat"])
                if type(row["repeat"]) is not int or key in seen or expected.get(key) != row["pattern"]:
                    raise ValueError("invalid/duplicate row")
                seen.add(key)
                v = counts.setdefault(row["pattern"], {"n": 0, "base": 0, "cand": 0})
                for variant in ("base", "cand"):
                    grade = row[variant]["pass"]
                    if type(grade) is not int or grade not in (0, 1):
                        raise ValueError("nonbinary grade")
                    v[variant] += grade
                v["n"] += 1
            if seen != set(expected) or counts != r["by_pattern"] or r["episodes"] != len(manifest):
                raise ValueError("missing coverage or inconsistent totals")
            identity = json.dumps([r["benchmark_sha256"], manifest, r["edits"], r["model"], r["judge_model"]], sort_keys=True)
            identities.add(identity)
            signature = json.dumps(r, sort_keys=True)
            if signature in signatures:
                raise ValueError("duplicate report")
            signatures.add(signature)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"ineligible pool input: {exc}")
    if len(identities) != 1 or not reports:
        errors.append("pool requires the same benchmark, edits and models")
    tot = pool_reports(reports) if not errors else {}
    verdict, reasons = ("INCOMPLETE", errors) if errors else behavior_verdict(tot, target)
    return {"mode": "behavior-pooled", "candidate": reports[-1].get("candidate") if reports else None, "target": target,
            "verdict": verdict, "reasons": reasons, "by_pattern": tot, "runs": [
                {"report": str(n), "verdict_own_target": r.get("verdict"), "own_target": r.get("target"),
                 "by_pattern": r.get("by_pattern", {}), "cost_usd": r.get("cost_usd")} for n, r in zip(names, reports)],
            "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in reports), 4)}


def pooled_markdown(r):
    lines = [f"# coach_eval behavior (pooled {len(r['runs'])} runs): {r['candidate']}", "",
             f"Target pattern `{r['target']}`; total spend ${r['cost_usd']:.2f}.", "",
             f"**Verdict: {r['verdict']}**", ""]
    lines += [f"- {x}" for x in r["reasons"]]
    hdr = " | ".join(f"run {i + 1}" for i in range(len(r["runs"])))
    lines += ["", f"| pattern | pooled base/cand (n) | {hdr} |", "|---|---|" + "---|" * len(r["runs"])]
    for pat in patterns_of(r["by_pattern"]):
        v = r["by_pattern"].get(pat)
        if v:
            cells = " | ".join(f"{x['by_pattern'].get(pat, {}).get('base', '-')}->"
                               f"{x['by_pattern'].get(pat, {}).get('cand', '-')}" for x in r["runs"])
            lines.append(f"| {pat}{' (target)' if pat == r['target'] else ''} | {v['base']}/{v['cand']} ({v['n']}) "
                         f"| {cells} |")
    lines += [""] + [f"- run {i + 1}: `{x['report']}` (own target {x['own_target']}: {x['verdict_own_target']}, "
                     f"${float(x['cost_usd'] or 0):.2f})" for i, x in enumerate(r["runs"])]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(prog="coach_eval.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detector")
    d.add_argument("--set", choices=sorted(SETS), default="replyset")
    d.add_argument("--rule")
    d.add_argument("--baseline", default="")
    d.add_argument("--candidate", required=True)
    d.add_argument("--sets")
    d.add_argument("--json", action="store_true")
    d.add_argument("--out")
    b = sub.add_parser("bench-build")
    b.add_argument("--out", default=None)
    b.add_argument("--sets")
    b.add_argument("--per", type=int, default=6)
    b.add_argument("--v2", metavar="EVAL_SET", help="build bench-v2 (report_only + control) from the labeled "
                   "report-only eval_set.jsonl instead of the v1 benchmark")
    h = sub.add_parser("behavior")
    g = h.add_mutually_exclusive_group(required=True)
    g.add_argument("--candidate")
    g.add_argument("--proposal")
    h.add_argument("--target")
    h.add_argument("--bench")
    h.add_argument("--budget", type=float, default=5.0)
    h.add_argument("--repeats", type=int, default=1)
    h.add_argument("--jobs", type=int, default=4)
    h.add_argument("--limit", type=int)
    h.add_argument("--patterns", help="comma list: only these benchmark patterns (smoke runs)")
    h.add_argument("--model")
    h.add_argument("--out")
    h.add_argument("--json", action="store_true")
    h.add_argument("--no-log", action="store_true", help="deprecated; mandatory reservation accounting cannot be disabled")
    h.add_argument("--pool", action="append", default=[],
                   help="earlier report .json of the same candidate + benchmark: the verdict is on the pooled counts")
    v = sub.add_parser("verdict", help="recompute the (pooled) verdict of saved behavior reports; no model calls")
    v.add_argument("reports", nargs="+")
    v.add_argument("--target", required=True)
    v.add_argument("--out")
    v.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "detector":
        r = run_detector(a.set, a.rule, a.baseline, a.candidate, a.sets)
        md = detector_markdown(r)
    elif a.cmd == "verdict":
        reps = [json.loads(Path(f).read_text()) for f in a.reports]
        r = pooled_result(reps, a.target, a.reports)
        md = pooled_markdown(r)
    elif a.cmd == "bench-build":
        out = a.out or data_dir() / "eval" / ("bench-v2.jsonl" if a.v2 else "bench-v1.jsonl")
        if Path(out).exists():
            raise SystemExit(f"{out} exists: the benchmark is frozen. Write a new version (--out .../bench-v2.jsonl).")
        n, by = bench_build_v2(out, a.v2) if a.v2 else bench_build(out, a.sets, per=a.per)
        print(f"wrote {n} episodes to {out}: {by}")
        return 0
    else:
        if a.proposal:
            sys.path.insert(0, str(HERE))
            import coach_proposals as cp
            p = cp.state(str(data_dir())).get(a.proposal)
            if not p:
                raise SystemExit(f"no proposal {a.proposal}")
            cand = proposal_to_candidate(p)
        else:
            cand = json.loads(Path(a.candidate).read_text())
        bench = a.bench or data_dir() / "eval" / ("bench-v2.jsonl" if a.target == "report_only" else "bench-v1.jsonl")
        r = run_behavior(cand, bench, a.target, a.budget, jobs=a.jobs, repeats=a.repeats, limit=a.limit,
                         patterns=a.patterns.split(",") if a.patterns else None,
                         model=a.model, log=lambda s: print(s, file=sys.stderr, flush=True))
        # Calls are already durably reserved; never double-log a run or pooled report.
        md = behavior_markdown(r)
        if a.pool:
            run_md = md
            r = pooled_result([json.loads(Path(f).read_text()) for f in a.pool] + [r], r["target"],
                              a.pool + ["(this run)"])
            md = pooled_markdown(r) + "\n---\n\n" + run_md
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(md)
        Path(a.out).with_suffix(".json").write_text(json.dumps(r, indent=1, ensure_ascii=False, default=str))
    print(json.dumps(r, indent=1, default=str) if a.json else md)
    return {"PASS": 0, "REJECT": 1}.get(r["verdict"], 2)


if __name__ == "__main__":
    sys.exit(main())
