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
             works (--proposal <id>). The edits are applied ONLY inside a private eval HERMES_HOME
             (~/.hermes/coach/eval/root: config copy without MCP servers or memory provider, auth/.env linked,
             no plugins, its own state.db), never to the live home.
             Each episode = user ask + what the agent did (tool log) or the failed-fix exchange; the replay asks
             `hermes -z` (tools: todo, skills) for the next tool calls + the message it would send.
             PASS = the target gains >= max(2, n/4) passes (identical runs move one pattern by 2 of 6, so +1 is
             noise), no other pattern drops by more than max(1, n/6), non-target net >= -max(1, n/6). Repeat a
             run and pool it (--pool earlier.json, or `coach_eval.py verdict --target T a.json b.json`) to
             shrink noise; the bar scales with n. Spend is summed from --usage-file reports (token
             estimate when the provider reports none), capped by --budget (default $5), and logged to
             research/state/spend.jsonl in the data repo as lane "trial".

Roots (env): COACH_DATA (~/code/hermes-coach-data; benchmark = eval/bench-v1.jsonl, reports = eval/reports/),
COACH_SETS (~/.hermes/coach; replyset/, fixloop/), COACH_MODS (~/code/hermes-mods), HERMES_HOME (~/.hermes),
COACH_EVAL_MODEL (claude-sonnet-5-5), COACH_JUDGE_MODEL (same).
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


def judge_item_text(item_id, ep, response):
    return (f"=== ITEM id={item_id} ===\nPATTERN: {ep['pattern']}\nUSER ASK: {_clip(ep['user_ask'], 900)}\n"
            f"WHAT WENT WRONG ORIGINALLY / LABEL: {ep['label']}\nUSER'S REACTION THEN: {ep.get('user_reaction', '')}\n"
            f"EXPECTED BEHAVIOR: {ep['expected']}\nREPLAYED RESPONSE:\n<<<\n{_clip(response, 3000)}\n>>>")


def usage_cost(u, model=""):
    try:
        c = float(u.get("total_including_auxiliary", {}).get("estimated_cost_usd") or u.get("estimated_cost_usd") or 0)
    except (TypeError, ValueError, AttributeError):
        c = 0.0
    if c > 0:
        return c
    m = (u.get("model") or model or "").lower()
    pi, po, pw, pr = next((v for k, v in PRICES.items() if k in m), PRICES["sonnet"])
    aux = u.get("auxiliary") or {}
    tin = (u.get("input_tokens") or 0) + (aux.get("input_tokens") or 0)
    tout = (u.get("output_tokens") or 0) + (aux.get("output_tokens") or 0)
    tw = (u.get("cache_write_tokens") or 0) + (aux.get("cache_write_tokens") or 0)
    tr = (u.get("cache_read_tokens") or 0) + (aux.get("cache_read_tokens") or 0)
    return (tin * pi + tout * po + tw * pw + tr * pr) / 1e6


class Budget:
    """Thread-safe spend cap: a call starts only while spent < cap, so at most `jobs` in-flight calls overshoot."""

    def __init__(self, usd):
        import threading
        self.cap, self.spent, self.calls, self.stopped = float(usd), 0.0, 0, False
        self.lock = threading.Lock()

    def ok(self):
        with self.lock:
            if self.spent >= self.cap:
                self.stopped = True
            return not self.stopped

    def add(self, usd):
        with self.lock:
            self.spent += usd
            self.calls += 1


def hermes_runner(hermes_bin=None, timeout=900):
    """Default runner: (prompt, home, cwd, model, toolsets, ignore_rules) -> (text, usage dict)."""
    hb = hermes_bin or shutil.which("hermes") or str(HOME / ".local/bin/hermes")

    def run(prompt, home=None, cwd=None, model=None, toolsets="todo", ignore_rules=False):
        uf = Path(os.environ.get("TMPDIR") or "/tmp") / f"coach-eval-{os.getpid()}-{random.randrange(1 << 30)}.json"
        args = [hb, "-z", prompt.replace("\x00", ""), "-m", model, "-t", toolsets, "--usage-file", str(uf)]
        if ignore_rules:
            args.append("--ignore-rules")
        env = dict(os.environ)
        if home:
            env["HERMES_HOME"] = str(home)
        if cwd:
            env["TERMINAL_CWD"] = str(cwd)  # project-context (AGENTS.md) discovery follows TERMINAL_CWD
        for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKSPACE", "HERMES_SESSION_ID"):
            env.pop(k, None)
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=str(cwd or HOME), env=env)
            text = r.stdout
        except subprocess.TimeoutExpired:
            text = ""
        try:
            usage = json.loads(uf.read_text())
        except Exception:
            usage = {}
        uf.unlink(missing_ok=True)
        return text, usage
    return run


# --- the private eval home -------------------------------------------------------------------------------------

def _strip_config(text):
    """Config for the eval home: no MCP servers (slow, side effects), no external memory provider (would write the
    replays into the user's long-term memory)."""
    text = re.sub(r"(?ms)^mcp_servers:.*?(?=^\S|\Z)", "mcp_servers: {}\n", text)
    text = re.sub(r"(?m)^(memory:\n(?:[ \t]+[^\n]*\n)*?[ \t]+provider:)[^\n]*", r"\1 ''", text)
    return text


def _materialize(root, rel):
    """Make every directory on root/rel a real dir (a symlinked dir becomes a dir of symlinks to its children) so a
    file under it can be replaced without touching the symlink target."""
    cur = Path(root)
    for part in Path(rel).parts[:-1]:
        nxt = cur / part
        if nxt.is_symlink():
            target = nxt.resolve()
            nxt.unlink()
            nxt.mkdir()
            for child in target.iterdir():
                (nxt / child.name).symlink_to(child)
        elif not nxt.exists():
            nxt.mkdir()
        cur = nxt
    f = cur / Path(rel).name
    if f.is_symlink():
        src = f.resolve()
        f.unlink()
        if src.is_file():
            shutil.copy2(src, f)
    return f


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


def prepare_home(root, live, edits, agents_src=None):
    """(Re)build the eval HERMES_HOME at root for one variant; returns the cwd (holds AGENTS.md). The runtime's
    install cache under root (installs/, cache/) survives between variants and runs: rebuilding it costs minutes."""
    root, live = Path(root), Path(live)
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(_strip_config((live / "config.yaml").read_text()))
    for n in ("auth.json", ".env"):
        p = root / n
        if p.is_symlink() or p.exists():
            p.unlink()
        if (live / n).exists():
            p.symlink_to(live / n)
    for n in ("SOUL.md",):
        (root / n).unlink(missing_ok=True)
        if (live / n).exists():
            shutil.copy2(live / n, root / n)
    mem = root / "memories"
    shutil.rmtree(mem, ignore_errors=True)
    mem.mkdir()
    for n in ("MEMORY.md", "USER.md"):
        if (live / "memories" / n).exists():
            shutil.copy2(live / "memories" / n, mem / n)
    sk = root / "skills"
    if sk.is_symlink():
        sk.unlink()
    shutil.rmtree(sk, ignore_errors=True)
    if (live / "skills").exists():
        sk.symlink_to(live / "skills")
    shutil.rmtree(root / "plugins", ignore_errors=True)
    cwd = root.parent / "work"
    shutil.rmtree(cwd, ignore_errors=True)
    cwd.mkdir(parents=True)
    agents = Path(agents_src or HOME / "AGENTS.md")
    if agents.exists():
        shutil.copy2(agents, cwd / "AGENTS.md")
    ext = _external_skill_dirs(live)
    for e in edits or []:
        f = str(e["file"])
        if f == "AGENTS.md":
            path = cwd / "AGENTS.md"
        else:
            fp = Path(f).expanduser()
            if fp.is_absolute():
                base = next((d for d in ext if str(fp).startswith(str(d.resolve()) + "/") or
                             str(fp).startswith(str(d) + "/")), None)
                if not base:
                    raise SystemExit(f"edit target {f} is outside the eval home and the external skill dirs")
                rel = Path("skills") / fp.relative_to(base.resolve() if str(fp).startswith(str(base.resolve()))
                                                      else base)
                # an external skill is shadowed by a same-path local copy: copy the whole skill dir in
                skill_dir = fp.parent
                dest = _materialize(root, rel).parent
                for child in skill_dir.iterdir():
                    t = dest / child.name
                    if not t.exists():
                        (shutil.copytree if child.is_dir() else shutil.copy2)(child, t)
                path = dest / fp.name
                if not path.exists():
                    shutil.copy2(fp, path)
            else:
                path = _materialize(root, f)
        old = path.read_text() if path.exists() else ""
        if "append" in e:
            new = old.rstrip("\n") + "\n\n" + e["append"].strip("\n") + "\n"
        else:
            if old.count(e["old_string"]) != 1:
                raise SystemExit(f"edit on {f}: old_string found {old.count(e['old_string'])} times (need exactly 1)")
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
        if not budget.ok():
            return ep["id"], k, None, 0.0
        text, usage = runner(replay_prompt(ep), home=root, cwd=cwd, model=model, toolsets="todo,skills")
        budget.add(usage_cost(usage, model))
        return ep["id"], k, (text or "").strip() or None, 0.0

    with ThreadPoolExecutor(jobs) as ex:
        for eid, k, text, _ in ex.map(one, work):
            if text is not None:
                out[(eid, k)] = text
    log(f"  {name}: {len(out)}/{len(work)} replays, spent so far ${budget.spent:.2f}")
    return out


def judge_all(items, runner, budget, model, batch=8, jobs=4):
    """items: [(item_id, ep, response)] -> {item_id: {pass, why}}."""
    res = {}
    batches = [items[i:i + batch] for i in range(0, len(items), batch)]

    def work(b):
        if not budget.ok():
            return [], 0.0
        prompt = JUDGE_SYS + "\n\n" + "\n\n".join(judge_item_text(i, ep, r) for i, ep, r in b)
        for _ in range(2):
            text, usage = runner(prompt, model=model, toolsets="todo", ignore_rules=True)
            budget.add(usage_cost(usage, model))
            m = re.search(r"\[\s*\{.*\}\s*\]", text or "", re.S)
            if m:
                try:
                    return json.loads(m.group(0)), 0.0
                except Exception:
                    pass
        return [], 0.0

    with ThreadPoolExecutor(jobs) as ex:
        for got, _ in ex.map(work, batches):
            for o in got:
                if isinstance(o, dict) and o.get("id"):
                    try:
                        res[str(o["id"])] = {"pass": int(o.get("pass") or 0), "why": str(o.get("why") or "")[:300]}
                    except (TypeError, ValueError):
                        pass
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
    eps = load_jsonl(bench)
    if patterns:
        eps = [e for e in eps if e["pattern"] in patterns]
    if limit:
        eps = eps[:limit]
    target = target or cand.get("target") or "unverified_done"
    if target not in PATTERNS:
        raise SystemExit(f"--target must be one of {PATTERNS}")
    runner = runner or hermes_runner()
    root = Path(root or sets_dir() / "eval" / "root")
    live = Path(live or env_path("HERMES_HOME", HOME / ".hermes"))
    model = model or os.environ.get("COACH_EVAL_MODEL") or "claude-sonnet-5-5"
    judge_model = judge_model or os.environ.get("COACH_JUDGE_MODEL") or model
    budget = Budget(budget_usd)
    t0 = time.time()
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
    grades = judge_all(items, runner, budget, judge_model, jobs=jobs)
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
    if budget.stopped or graded < len(eps) * repeats * 0.8:
        reasons.append(f"only {graded}/{len(eps) * repeats} episode pairs graded"
                       + (f"; budget ${budget_usd:.2f} reached" if budget.stopped else ""))
        if verdict == "PASS" and budget.stopped:
            verdict = "INCOMPLETE"
    return {"mode": "behavior", "candidate": cand.get("name") or "candidate", "target": target, "verdict": verdict,
            "reasons": reasons, "by_pattern": by_pattern, "rows": rows, "cost_usd": round(budget.spent, 4),
            "calls": budget.calls, "minutes": round((time.time() - t0) / 60, 1), "model": model,
            "judge_model": judge_model, "bench": str(bench), "episodes": len(eps), "repeats": repeats,
            "edits": cand.get("edits") or []}


def behavior_markdown(r):
    lines = [f"# coach_eval behavior: {r['candidate']}", "",
             f"Target pattern `{r['target']}`; {r['episodes']} episodes x {r['repeats']} repeat(s) from `{r['bench']}`; "
             f"replay model {r['model']}, judge {r['judge_model']}; {r['calls']} model calls, ${r['cost_usd']:.2f}, "
             f"{r['minutes']} min.", "", f"**Verdict: {r['verdict']}**", ""]
    lines += [f"- {x}" for x in r["reasons"]]
    lines += ["", "| pattern | graded | baseline pass | candidate pass |", "|---|---|---|---|"]
    for pat in PATTERNS:
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
    tot = pool_reports(reports)
    verdict, reasons = behavior_verdict(tot, target)
    return {"mode": "behavior-pooled", "candidate": reports[-1].get("candidate"), "target": target,
            "verdict": verdict, "reasons": reasons, "by_pattern": tot, "runs": [
                {"report": str(n), "verdict_own_target": r.get("verdict"), "own_target": r.get("target"),
                 "by_pattern": r["by_pattern"], "cost_usd": r.get("cost_usd")} for n, r in zip(names, reports)],
            "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in reports), 4)}


def pooled_markdown(r):
    lines = [f"# coach_eval behavior (pooled {len(r['runs'])} runs): {r['candidate']}", "",
             f"Target pattern `{r['target']}`; total spend ${r['cost_usd']:.2f}.", "",
             f"**Verdict: {r['verdict']}**", ""]
    lines += [f"- {x}" for x in r["reasons"]]
    hdr = " | ".join(f"run {i + 1}" for i in range(len(r["runs"])))
    lines += ["", f"| pattern | pooled base/cand (n) | {hdr} |", "|---|---|" + "---|" * len(r["runs"])]
    for pat in PATTERNS:
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
    h.add_argument("--no-log", action="store_true")
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
        out = a.out or data_dir() / "eval" / "bench-v1.jsonl"
        if Path(out).exists():
            raise SystemExit(f"{out} exists: the benchmark is frozen. Write a new version (--out .../bench-v2.jsonl).")
        n, by = bench_build(out, a.sets, per=a.per)
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
        bench = a.bench or data_dir() / "eval" / "bench-v1.jsonl"
        r = run_behavior(cand, bench, a.target, a.budget, jobs=a.jobs, repeats=a.repeats, limit=a.limit,
                         patterns=a.patterns.split(",") if a.patterns else None,
                         model=a.model, log=lambda s: print(s, file=sys.stderr, flush=True))
        if not a.no_log and r["cost_usd"] > 0:
            log_spend(data_dir(), r["cost_usd"], f"coach_eval behavior {r['candidate']} ({r['verdict']})")
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
