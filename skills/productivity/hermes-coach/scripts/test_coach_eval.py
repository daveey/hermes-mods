#!/usr/bin/env python3
"""Tests for coach_eval.py (detector + behavior replay eval) and the trial lane (coach_trial.py + coach_apply auto's
kind trial):  python3 test_coach_eval.py

Detector tests run on a synthetic labeled set (always) and on the real labeled sets under ~/.hermes/coach when they
exist (skipped elsewhere). Behavior tests use a fake model runner: no model calls, no cost."""
import json, os, random, sys, tempfile, time, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import coach_eval as ce  # noqa: E402
import coach_apply as ca  # noqa: E402
import coach_proposals as cp  # noqa: E402
import coach_trial as ct  # noqa: E402
from test_coach_apply import Base  # noqa: E402
from test_coach_fixer import kdb, sdb  # noqa: E402

REAL = Path(os.environ.get("COACH_SETS") or Path.home() / ".hermes/coach")


def synth_replyset(root, n=400, seed=3):
    """A labeled reply set where the judge's p separates gold well above ~0.7 and is noisy below: the shape the real
    2026-10-14 backtest had. Low thresholds buy recall with a precision collapse."""
    rnd = random.Random(seed)
    d = Path(root) / "replyset"
    d.mkdir(parents=True)
    rows, gold, det = [], [], []
    for i in range(n):
        rid = f"s{i}#1"
        split = "heldout" if i % 10 < 3 else "dev"
        g = {r: (1.0 if rnd.random() < 0.2 else 0.0) for r in ("handoff", "confirm", "unverified")}
        p = {}
        for r, gv in g.items():
            if gv:
                p[r] = rnd.choice([0.95, 0.9, 0.8, 0.75, 0.6, 0.5, 0.4, 0.3])
            else:
                p[r] = rnd.choice([0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.65, 0.1, 0.05])
        rows.append({"id": rid, "split": split, "reply": "x", "prefilter": ["handoff", "confirm", "unverified"],
                     "user_ask": "a", "tool_log": ["-> t()"], "next_user": "ok"})
        gold.append({"id": rid, **g})
        det.append({"id": rid, **p})
    for name, recs in (("sample", rows), ("gold", gold), ("detect2", det)):
        (d / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    return Path(root)


class DetectorSynthetic(unittest.TestCase):
    def setUp(self):
        self.root = synth_replyset(tempfile.mkdtemp())

    def test_known_bad_threshold_is_rejected(self):
        r = ce.run_detector("replyset", "handoff", "", "th=0.2", self.root)
        self.assertEqual(r["verdict"], "REJECT", r["reasons"])
        self.assertTrue(any("precision regression on handoff" in x for x in r["reasons"]), r["reasons"])

    def test_known_good_vs_worse_baseline_is_accepted(self):
        r = ce.run_detector("replyset", "handoff", "th=0.2", "", self.root)  # "" = the shipped config
        self.assertEqual(r["verdict"], "PASS", r["reasons"])

    def test_identical_variant_shows_no_gain(self):
        r = ce.run_detector("replyset", "handoff", "", "th=0.7", self.root)
        self.assertEqual(r["verdict"], "REJECT")
        self.assertIn("no gain", r["reasons"][0])

    def test_disabled_rule_never_fires(self):
        v = ce.parse_variant("rules=handoff", "replyset")
        s = ce.score_variant("replyset", *ce.load_set("replyset", self.root)[1:], v, self.root / "replyset")
        self.assertEqual(s["all"]["confirm"]["fired"], 0)
        self.assertGreater(s["all"]["handoff"]["fired"], 0)

    def test_variant_parsing(self):
        v = ce.parse_variant("th=handoff:0.6/confirm:0.8,detect=d.jsonl", "replyset")
        self.assertEqual((v["th"]["handoff"], v["th"]["confirm"], v["th"]["unverified"], v["detect"]),
                         (0.6, 0.8, 0.7, "d.jsonl"))
        self.assertEqual(ce.parse_variant("", "replyset")["rules"], ["handoff", "confirm"])
        with self.assertRaises(SystemExit):
            ce.parse_variant("bogus=1", "replyset")


@unittest.skipUnless((REAL / "replyset/gold.jsonl").exists() and (REAL / "fixloop/gold.jsonl").exists(),
                     "real labeled sets not on this host")
class DetectorReal(unittest.TestCase):
    """The done-criteria on the real 2026-10-14 sets: shipped handoff 0.7 (precision 0.91) is kept over 0.2."""

    def test_reply_check_threshold_02_rejected(self):
        r = ce.run_detector("replyset", "handoff", "", "th=0.2", REAL)
        self.assertEqual(r["verdict"], "REJECT", r["reasons"])

    def test_shipped_reply_check_beats_worse_baseline(self):
        r = ce.run_detector("replyset", "handoff", "th=0.2", "", REAL)
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertGreaterEqual(r["cand"]["all"]["handoff"]["precision"], 0.9)

    def test_fixloop_low_threshold_rejected_shipped_accepted(self):
        self.assertEqual(ce.run_detector("fixloop", None, "", "th=0.3", REAL)["verdict"], "REJECT")
        self.assertEqual(ce.run_detector("fixloop", None, "th=0.3", "", REAL)["verdict"], "PASS")


# ------------------------------------------------------------------------------------------------- behavior

def bench(path):
    eps = []
    for pat in ce.PATTERNS:
        for i in range(4):
            eps.append({"id": f"{pat}:{i}", "pattern": pat, "kind": "reply", "user_ask": f"ask {i}",
                        "tool_log": ["-> terminal(make install)", "   <- ok"], "label": "lbl", "user_reaction": "r",
                        "expected": ce.EXPECTED[pat]})
    Path(path).write_text("".join(json.dumps(e) + "\n" for e in eps))
    return path


class FakeRunner:
    """Replays answer from the eval home's AGENTS.md: a VERIFY rule makes unverified_done replies verify. The judge
    passes a reply containing VERIFIED (or any reply for non-target patterns unless `hurt` is set)."""

    offline_only = True

    @property
    def max_cost_usd(self):
        return self.cost

    def __init__(self, hurt=False, cost=0.01):
        self.hurt, self.cost, self.calls, self.homes = hurt, cost, 0, set()

    def __call__(self, prompt, home=None, cwd=None, model=None, toolsets="todo", ignore_rules=False):
        self.calls += 1
        usage = {"estimated_cost_usd": self.cost}
        if ignore_rules:  # judge
            items = []
            for block in prompt.split("=== ITEM id=")[1:]:
                iid = block.split(" ===", 1)[0]
                pat = block.split("PATTERN: ", 1)[1].split("\n", 1)[0]
                resp = block.split("REPLAYED RESPONSE:", 1)[1]
                if pat == "unverified_done":
                    ok = "VERIFIED" in resp
                else:
                    ok = "BROKE" not in resp
                items.append({"id": iid, "pass": int(ok), "why": "fake"})
            return json.dumps(items), usage
        self.homes.add(str(home))
        agents = (Path(cwd) / "AGENTS.md").read_text() if cwd and (Path(cwd) / "AGENTS.md").exists() else ""
        text = "Done."
        if "VERIFY-RULE" in agents:
            text = "-> terminal(check) VERIFIED. Done."
            if self.hurt:
                text += " BROKE"
        return text, usage


class Behavior(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.live = self.tmp / "live"
        (self.live / "memories").mkdir(parents=True)
        (self.live / "skills/cat/demo").mkdir(parents=True)
        (self.live / "skills/cat/demo/SKILL.md").write_text("---\nname: demo\n---\nbody\n")
        (self.live / "config.yaml").write_text(
            "model:\n  default: x\nmemory:\n  memory_enabled: true\n  provider: 'midbrain'\n  nudge_interval: 10\n"
            "mcp_servers: {a: {command: x}}\nskills:\n  external_dirs:\n    - " + str(self.tmp / "ext") + "\n")
        (self.live / "memories/MEMORY.md").write_text("mem\n")
        (self.live / "SOUL.md").write_text("soul\n")
        (self.tmp / "ext/cat2/extskill").mkdir(parents=True)
        (self.tmp / "ext/cat2/extskill/SKILL.md").write_text("---\nname: extskill\n---\nORIG\n")
        self.agents = self.tmp / "AGENTS.md"
        self.agents.write_text("# rules\n")
        self.bench = bench(self.tmp / "bench.jsonl")
        self.root = self.tmp / "evalhome" / "root"
        self.data = self.tmp / "data"
        (self.data / "research/state").mkdir(parents=True)
        from unittest.mock import patch
        env = patch.dict(os.environ, {"COACH_DATA": str(self.data)})
        env.start()
        self.addCleanup(env.stop)
        ce_home = ce.HOME
        self.addCleanup(setattr, ce, "HOME", ce_home)
        ce.HOME = self.tmp  # prepare_home copies HOME/AGENTS.md into the replay cwd
        (self.tmp / "AGENTS.md").write_text("# rules\n")

    def cand(self):
        return {"name": "verify", "target": "unverified_done",
                "edits": [{"file": "AGENTS.md", "append": "VERIFY-RULE: check before done"}]}

    def run_b(self, runner, cand=None, budget=5.0):
        return ce.run_behavior(cand or self.cand(), self.bench, None, budget, runner=runner, root=self.root,
                               live=self.live, jobs=2, log=lambda *_: None)

    def test_good_candidate_passes(self):
        r = self.run_b(FakeRunner())
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertEqual(r["by_pattern"]["unverified_done"], {"base": 0, "cand": 4, "n": 4})

    def test_candidate_that_hurts_other_patterns_is_rejected(self):
        r = self.run_b(FakeRunner(hurt=True))
        self.assertEqual(r["verdict"], "REJECT", r["reasons"])
        self.assertTrue(any("regression on control" in x or "regression on handoff" in x for x in r["reasons"]))

    def test_no_op_candidate_is_rejected(self):
        r = self.run_b(FakeRunner(), cand={"name": "noop", "target": "unverified_done", "edits": []})
        self.assertEqual(r["verdict"], "REJECT")

    def test_budget_stops_the_run(self):
        r = self.run_b(FakeRunner(cost=0.5), budget=2.0)
        self.assertLessEqual(r["cost_usd"], 2.0)
        self.assertIn(r["verdict"], ("INCOMPLETE", "REJECT"))
        self.assertTrue(any("graded" in x for x in r["reasons"]), r["reasons"])

    def test_eval_home_is_isolated_and_reset(self):
        runner = FakeRunner()
        self.run_b(runner, cand={"name": "skill", "target": "unverified_done", "edits": [
            {"file": "skills/cat/demo/SKILL.md", "old_string": "body", "new_string": "VERIFY-RULE body"},
            {"file": str(self.tmp / "ext/cat2/extskill/SKILL.md"), "append": "NEW"},
            {"file": "memories/MEMORY.md", "append": "more"}]})
        self.assertEqual(runner.homes, {str(self.root)})
        # live files untouched
        self.assertEqual((self.live / "skills/cat/demo/SKILL.md").read_text(), "---\nname: demo\n---\nbody\n")
        self.assertEqual((self.tmp / "ext/cat2/extskill/SKILL.md").read_text(), "---\nname: extskill\n---\nORIG\n")
        self.assertEqual((self.live / "memories/MEMORY.md").read_text(), "mem\n")
        # the eval home ends at baseline, without MCP servers or the memory provider
        cfg = (self.root / "config.yaml").read_text()
        self.assertIn("mcp_servers: {}", cfg)
        self.assertIn("provider: ''", cfg)
        self.assertFalse((self.root / "skills").is_symlink())
        self.assertEqual((self.root / "memories/MEMORY.md").read_text(), "mem\n")
        self.assertFalse((self.root / "plugins").exists())

    def test_skill_edit_lands_only_in_eval_home(self):
        cwd = ce.prepare_home(self.root, self.live, [
            {"file": str(self.tmp / "ext/cat2/extskill/SKILL.md"), "append": "NEW"},
            {"file": "skills/cat/demo/SKILL.md", "append": "X"}])
        self.assertIn("NEW", (self.root / "skills/cat2/extskill/SKILL.md").read_text())
        self.assertIn("X", (self.root / "skills/cat/demo/SKILL.md").read_text())
        self.assertTrue((cwd / "AGENTS.md").exists())
        self.assertNotIn("X", (self.live / "skills/cat/demo/SKILL.md").read_text())

    def test_bad_edit_anchor_fails(self):
        with self.assertRaises(SystemExit):
            ce.prepare_home(self.root, self.live, [{"file": "SOUL.md", "old_string": "nope", "new_string": "x"}])

    def test_spend_logged_as_trial_lane(self):
        f = ce.log_spend(self.data, 1.23, "coach_eval behavior x (PASS)")
        row = json.loads(f.read_text().splitlines()[-1])
        self.assertEqual((row["lane"], row["usd"]), ("trial", 1.23))

    def test_usage_cost_estimates_from_tokens(self):
        self.assertAlmostEqual(ce.usage_cost({"estimated_cost_usd": 0.0, "model": "claude-sonnet-5-5",
                                              "input_tokens": 1_000_000, "output_tokens": 0}), 3.0)
        self.assertEqual(ce.usage_cost({"total_including_auxiliary": {"estimated_cost_usd": 0.2}}), 0.2)


    def test_unsupported_targets_fail_before_any_write(self):
        for target in ("../outside.txt", "config.yaml", "auth.json", ".env", "memories/OTHER.md",
                       "skills/x/run.py", "skills/x/../../SOUL.md", "skills/file.md"):
            with self.subTest(target=target), self.assertRaises(SystemExit):
                ce.prepare_home(self.root, self.live, [
                    {"file": "SOUL.md", "append": "allowed"}, {"file": target, "append": "bad"}])
            self.assertFalse(self.root.exists())
        self.assertFalse((self.root.parent / "outside.txt").exists())

    def test_overlap_and_destination_symlinks(self):
        for root in (self.live, self.live / "eval", self.tmp, self.tmp / "ext",
                     self.tmp / "other/../live"):
            with self.assertRaises(SystemExit):
                ce.prepare_home(root, self.live, [])
        self.root.parent.mkdir(parents=True)
        self.root.symlink_to(self.live, target_is_directory=True)
        with self.assertRaises(SystemExit):
            ce.prepare_home(self.root, self.live, [])
        self.root.unlink()
        self.root.mkdir()
        (self.root / "config.yaml").symlink_to(self.live / "config.yaml")
        before = (self.live / "config.yaml").read_text()
        with self.assertRaises(SystemExit):
            ce.prepare_home(self.root, self.live, [])
        self.assertEqual((self.live / "config.yaml").read_text(), before)

    def test_external_symlink_escape_and_markdown_only(self):
        alias = self.tmp / "ext/escape.md"
        alias.symlink_to(self.live / "SOUL.md")
        for f in (alias, self.tmp / "ext/../live/SOUL.md", self.tmp / "ext/cat2/extskill/run.py"):
            with self.assertRaises(SystemExit):
                ce.prepare_home(self.root, self.live, [{"file": str(f), "append": "bad"}])
        self.assertFalse(self.root.exists())

    def test_live_skill_symlink_is_copied_not_written_through(self):
        link = self.live / "skills/cat/demo/link.md"
        link.symlink_to(self.live / "SOUL.md")
        ce.prepare_home(self.root, self.live, [{"file": "skills/cat/demo/link.md", "append": "new"}])
        self.assertEqual((self.live / "SOUL.md").read_text(), "soul\n")
        self.assertFalse((self.root / "skills/cat/demo/link.md").is_symlink())

    def test_reviewer_cost_1_2_cap_5(self):
        runner = FakeRunner(cost=1.2)
        r = self.run_b(runner)
        self.assertEqual(runner.calls, 4)
        self.assertEqual(r["cost_usd"], 4.8)
        self.assertEqual(r["verdict"], "INCOMPLETE")

    def test_partial_and_smoke_never_pass_or_pool(self):
        r = ce.run_behavior(self.cand(), self.bench, runner=FakeRunner(), root=self.root,
                            live=self.live, limit=4, log=lambda *_: None)
        self.assertEqual(r["verdict"], "INCOMPLETE")
        self.assertEqual(ce.pooled_result([r], "unverified_done", ["smoke"])["verdict"], "INCOMPLETE")
        class Missing(FakeRunner):
            def __call__(self, *args, **kwargs):
                text, usage = super().__call__(*args, **kwargs)
                if kwargs.get("ignore_rules"):
                    text = json.dumps(json.loads(text)[:-1])
                return text, usage
        self.assertEqual(self.run_b(Missing())["verdict"], "INCOMPLETE")

    def test_pool_checks_full_rows_counts_and_identity(self):
        import copy
        r = self.run_b(FakeRunner())
        self.assertEqual(ce.pooled_result([r], r["target"], ["r"])["verdict"], "PASS")
        for mutate in (lambda x: x["rows"].pop(),
                       lambda x: x["rows"].append(x["rows"][0]),
                       lambda x: x["rows"][0]["cand"].update({"pass": 2}),
                       lambda x: x.update({"complete": False})):
            bad = copy.deepcopy(r)
            mutate(bad)
            self.assertEqual(ce.pooled_result([bad], r["target"], ["r"])["verdict"], "INCOMPLETE")
        other = copy.deepcopy(r)
        other["edits"] = []
        self.assertEqual(ce.pooled_result([r, other], r["target"], ["r", "s"])["verdict"], "INCOMPLETE")
        self.assertEqual(ce.pooled_result([r, r], r["target"], ["r", "r"])["verdict"], "INCOMPLETE")

    def test_judge_strict_grades_ids_and_retry_reservations(self):
        ep = ce.load_jsonl(self.bench)[0]
        class Bad(FakeRunner):
            def __call__(self, *args, **kwargs):
                self.calls += 1
                return json.dumps(grades), {"estimated_cost_usd": self.cost}
        for grades in ([{"id": "id", "pass": x, "why": "x"}] for x in (True, "1", 2, -1, None)):
            runner = Bad(cost=1.2)
            budget = ce.Budget(2, ledger=self.tmp / (str(grades[0]["pass"]) + ".jsonl"))
            self.assertEqual(ce.judge_all([("id", ep, "r")], runner, budget, "fake"), {})
            self.assertEqual(runner.calls, 1)
            self.assertEqual(budget.spent, 1.2)
        for grades in ([{"id": "other", "pass": 1, "why": "x"}],
                       [{"id": "id", "pass": 1, "why": "x"}] * 2, []):
            runner = Bad(cost=0)
            self.assertEqual(ce.judge_all([("id", ep, "r")], runner,
                             ce.Budget(5, ledger=self.tmp / "zero.jsonl"), "fake"), {})

    def test_concurrent_calls_and_interruptions_are_durably_reserved(self):
        from concurrent.futures import ThreadPoolExecutor
        ledger = self.tmp / "spend.jsonl"
        class Broken(FakeRunner):
            def __call__(self, *args, **kwargs):
                raise KeyboardInterrupt("offline interruption")
        budgets = [ce.Budget(5, ledger=ledger, lane_allowance=5) for _ in range(2)]
        def call(i):
            try:
                return budgets[i % 2].call(Broken(cost=1.2), "fake")
            except KeyboardInterrupt:
                return "interrupted"
        with ThreadPoolExecutor(8) as ex:
            results = list(ex.map(call, range(20)))
        self.assertEqual(results.count("interrupted"), 4)
        rows = ce.load_jsonl(ledger)
        self.assertEqual(len(rows), 4)
        self.assertAlmostEqual(sum(x["usd"] for x in rows), 4.8)
        self.assertIsNone(ce.Budget(5, ledger=ledger, lane_allowance=5).call(FakeRunner(cost=1.2), "fake"))

    def test_cli_pool_and_mandatory_accounting(self):
        from unittest.mock import patch
        from contextlib import redirect_stdout
        import io
        report = self.run_b(FakeRunner())
        candidate = self.tmp / "candidate.json"
        candidate.write_text(json.dumps(self.cand()))
        previous = self.tmp / "old.json"
        previous.write_text(json.dumps(dict(report, complete=False)))
        ledger = self.data / "research/state/spend.jsonl"
        before = ledger.read_text()
        with patch.object(ce, "run_behavior", return_value=report), redirect_stdout(io.StringIO()) as stdout:
            code = ce.main(["behavior", "--candidate", str(candidate), "--pool", str(previous), "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())["verdict"], "INCOMPLETE")
        self.assertEqual(ledger.read_text(), before)  # no run-level double charge
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "disabled"):
            ce.main(["behavior", "--candidate", str(candidate), "--bench", str(self.bench), "--no-log"])
        self.assertEqual(ledger.read_text(), before)

    def test_exception_and_concurrent_judge_retries(self):
        ledger = self.tmp / "exceptions.jsonl"
        class Broken(FakeRunner):
            def __call__(self, *args, **kwargs):
                raise RuntimeError("request failed")
        budget = ce.Budget(5, ledger=ledger)
        with self.assertRaises(RuntimeError):
            budget.call(Broken(cost=1.2), "offline")
        self.assertEqual(budget.spent, 1.2)
        self.assertEqual(len(ce.load_jsonl(ledger)), 1)
        class Invalid(FakeRunner):
            def __call__(self, *args, **kwargs):
                self.calls += 1
                return "invalid JSON", {"estimated_cost_usd": self.cost}
        runner = Invalid(cost=1.2)
        ep = ce.load_jsonl(self.bench)[0]
        grades = ce.judge_all([(str(i), ep, "r") for i in range(20)], runner, budget, "fake", batch=1, jobs=8)
        self.assertEqual(grades, {})
        self.assertEqual(runner.calls, 3)
        self.assertEqual(budget.spent, 4.8)
        self.assertEqual(len(ce.load_jsonl(ledger)), 4)

    def test_paid_runner_is_refused_without_dispatch(self):
        with self.assertRaisesRegex(ValueError, "disabled"):
            ce.hermes_runner()
        runner = FakeRunner()
        runner.offline_only = False
        with self.assertRaisesRegex(ValueError, "disabled"):
            ce.Budget(5, ledger=self.tmp / "paid.jsonl").call(runner, "fake")
        self.assertEqual(runner.calls, 0)
        self.assertFalse((self.tmp / "paid.jsonl").exists())


class BehaviorVerdict(unittest.TestCase):
    def test_rules(self):
        def v(counts, target):
            full = {p: {"base": 6, "cand": 6, "n": 6} for p in ce.PATTERNS}
            full.update(counts)
            return ce.behavior_verdict(full, target)
        self.assertEqual(v({"unverified_done": {"base": 1, "cand": 3, "n": 6}, "control": {"base": 5, "cand": 4, "n": 6}},
                           "unverified_done")[0], "PASS")
        self.assertEqual(v({"unverified_done": {"base": 1, "cand": 3, "n": 6}, "control": {"base": 5, "cand": 3, "n": 6}},
                           "unverified_done")[0], "REJECT")
        self.assertEqual(v({"unverified_done": {"base": 2, "cand": 2, "n": 6}}, "unverified_done")[0], "REJECT")
        self.assertEqual(ce.behavior_verdict({"handoff": {"base": 1, "cand": 1, "n": 6}}, "unverified_done")[0], "INCOMPLETE")
        self.assertEqual(v({"unverified_done": {"base": 0, "cand": 2, "n": 6}, "handoff": {"base": 3, "cand": 2, "n": 6},
                            "control": {"base": 6, "cand": 5, "n": 6}}, "unverified_done")[0], "REJECT")

    def test_plus_one_on_six_is_noise(self):
        # the real 2026-10-19 confirmatory run: repeat_fix 3/6 -> 4/6, others net 0
        counts = {p: {"base": 6, "cand": 6, "n": 6} for p in ce.PATTERNS}
        counts["repeat_fix"] = {"base": 3, "cand": 4, "n": 6}
        r = ce.behavior_verdict(counts, "repeat_fix")
        self.assertEqual(r[0], "REJECT")
        self.assertIn("noise", r[1][0])
        self.assertEqual([ce.min_gain(n) for n in (6, 12, 18)], [2, 3, 5])

    def test_pooled_verdict(self):
        a = {"candidate": "c", "target": "x", "verdict": "REJECT", "cost_usd": 1,
             "by_pattern": {"repeat_fix": {"base": 3, "cand": 5, "n": 6}, "control": {"base": 6, "cand": 6, "n": 6}}}
        b = {"candidate": "c", "target": "x", "verdict": "PASS", "cost_usd": 2,
             "by_pattern": {"repeat_fix": {"base": 3, "cand": 4, "n": 6}, "control": {"base": 6, "cand": 6, "n": 6}}}
        r = ce.pooled_result([a, b], "repeat_fix", ["a.json", "b.json"])
        self.assertEqual((r["by_pattern"], r["verdict"], r["cost_usd"]),
                         ({}, "INCOMPLETE", 3))
        self.assertIn("pooled 2 runs", ce.pooled_markdown(r))


# ------------------------------------------------------------------------------------------------- trial lane

TRIAL = {"kind": "trial", "target": "verification-gates", "title": "Verification gates as code",
         "evidence": "reports/ideas.md 2026-10-14 #2: a check that fails the done claim",
         "change": {"idea": "reports/ideas.md 2026-10-14 #2", "gap": ["2026-10-14#9", "2026-10-14#12"],
                    "pattern": "unverified_done", "candidate": "AGENTS.md rule: done = a passing check"}}


class Trial(Base):
    def setUp(self):
        super().setUp()
        self.k, self.s = kdb(self.home), sdb(self.home)
        self.now = time.time()

    def card(self, tid, key, at=None, cost=0.0):
        at = at or self.now
        self.k.execute("insert into tasks values (?,?,?,?,?,?)", (tid, "t", "coach", key, "done", at))
        cur = self.k.execute("insert into task_runs (task_id, status, started_at, ended_at) values (?,?,?,?)",
                             (tid, "done", at, at + 600))
        self.k.execute("insert into worker_recovery_sessions values (?,?,?,?,?,?)",
                       (cur.lastrowid, tid, "default", str(self.home / "state.db"), f"s_{tid}", at))
        self.s.execute("insert into sessions values (?,?,?,?,?,?,?)", (f"s_{tid}", "kanban", "t", None, at, cost, None))
        self.k.commit(); self.s.commit()

    def cards_created(self):
        f = Path(os.environ["FAKE_CFG"] + ".cards")
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def test_validation_and_tier(self):
        self.assertEqual(cp.validate(TRIAL), [])
        self.assertEqual(cp.default_tier(TRIAL), "review")
        bad = dict(TRIAL, change={"idea": "", "gap": ["one"], "pattern": "nope", "candidate": ""})
        self.assertEqual(len(cp.validate(bad)), 4, cp.validate(bad))
        self.assertEqual(cp.validate(dict(TRIAL, change=dict(TRIAL["change"], pattern="detector:replyset:handoff"))), [])

    def test_auto_files_goal_mode_trial_card(self):
        pid = self.add(**TRIAL)
        self.run_auto(now=self.now)
        p = self.st(pid)
        self.assertEqual((p["status"], p["card"]), ("carded", "t_abc12345"))
        call = self.cards_created()[0]
        self.assertIn("--goal", call)
        for flag, val in (("--created-by", "coach"), ("--idempotency-key", "coach-trial:verification-gates"),
                          ("--max-runtime", "7200")):
            self.assertEqual(call[call.index(flag) + 1], val)
        body = call[call.index("--body") + 1]
        for part in ("BEHIND A FLAG", "coach_eval.py behavior", "--target unverified_done", "2026-10-14#9",
                     "kanban_attach", "coach_proposals.py add", "kanban_request_review"):
            self.assertIn(part, body)

    def test_one_trial_card_a_day(self):
        self.card("t_today", "coach-trial:other")
        pid = self.add(**TRIAL)
        done = self.run_auto(now=self.now)
        self.assertEqual(self.st(pid)["status"], "pending")
        self.assertIn("cap 1/day", [d for d in done if d[0] == pid][0][2])

    def test_spend_cap_counts_cards_and_eval_runs(self):
        self.card("t_old", "coach-trial:old", at=self.now - 3 * 86400, cost=50)  # another day: ignored
        ce.log_spend(self.env.data, 6.0, "coach_eval behavior x")
        self.card("t_y", "coach-trial:y", at=self.now - 86400 * 2, cost=1.0)
        ok, why, usd = ct.gate(self.home, "hermes", self.env.data, self.now)
        self.assertTrue(ok, why)
        ce.log_spend(self.env.data, 4.5, "coach_eval behavior y")
        ok, why, usd = ct.gate(self.home, "hermes", self.env.data, self.now)
        self.assertFalse(ok)
        self.assertIn("$10", why)

    def test_trial_cards_do_not_count_as_fixer_spend(self):
        self.card("t_tr", "coach-trial:z", cost=3.0)
        import coach_fixer as fx
        self.assertEqual(fx.spend_by_day(self.home, "hermes", 1, self.now)[fx.utc_day(self.now)]["usd"], 0.0)
        self.assertEqual(fx.spend_by_day(self.home, "hermes", 1, self.now, lane="trial")[fx.utc_day(self.now)]["usd"],
                         3.0)

    def test_card_spend_log_keeps_eval_rows(self):
        ce.log_spend(self.env.data, 2.0, "coach_eval behavior x")
        self.card("t_tr", "coach-trial:z", cost=3.0)
        import coach_fixer as fx
        for _ in range(2):
            fx.log_spend(self.env.data, fx.spend_by_day(self.home, "hermes", 1, self.now, lane="trial"), lane="trial")
        rows = [json.loads(x) for x in fx.spend_log_path(self.env.data).read_text().splitlines()]
        self.assertEqual(sorted((r["lane"], r["usd"], "cards" in r) for r in rows),
                         [("trial", 2.0, False), ("trial", 3.0, True)])

    def test_add_skips_trial_slug_already_pending(self):
        rep = self.env.data / "reports/weekly-2026-10-25.md"
        alt = dict(TRIAL, evidence="other", change=dict(TRIAL["change"], candidate="different"))
        rep.write_text("".join("```coach-proposal\n" + json.dumps(x) + "\n```\n" for x in (TRIAL, alt)))
        cp.cmd_add(type("A", (), {"data": str(self.env.data), "report": str(rep)})())
        self.assertEqual(len([p for p in cp.state(self.env.data).values() if p["kind"] == "trial"]), 1)


if __name__ == "__main__":
    unittest.main()
