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
    for pat in ("unverified_done", "handoff", "control"):
        for i in range(4):
            eps.append({"id": f"{pat}:{i}", "pattern": pat, "kind": "reply", "user_ask": f"ask {i}",
                        "tool_log": ["-> terminal(make install)", "   <- ok"], "label": "lbl", "user_reaction": "r",
                        "expected": ce.EXPECTED[pat]})
    Path(path).write_text("".join(json.dumps(e) + "\n" for e in eps))
    return path


class FakeRunner:
    """Replays answer from the eval home's AGENTS.md: a VERIFY rule makes unverified_done replies verify. The judge
    passes a reply containing VERIFIED (or any reply for non-target patterns unless `hurt` is set)."""

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
        self.tmp = Path(tempfile.mkdtemp())
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
        self.assertLessEqual(r["cost_usd"], 2.0 + 0.5 * 2)  # at most one in-flight call per job past the cap
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
        self.assertTrue((self.root / "skills").is_symlink())
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


class BehaviorVerdict(unittest.TestCase):
    def test_rules(self):
        v = ce.behavior_verdict
        self.assertEqual(v({"unverified_done": {"base": 1, "cand": 3, "n": 6}, "control": {"base": 5, "cand": 4, "n": 6}},
                           "unverified_done")[0], "PASS")
        self.assertEqual(v({"unverified_done": {"base": 1, "cand": 3, "n": 6}, "control": {"base": 5, "cand": 3, "n": 6}},
                           "unverified_done")[0], "REJECT")
        self.assertEqual(v({"unverified_done": {"base": 2, "cand": 2, "n": 6}}, "unverified_done")[0], "REJECT")
        self.assertEqual(v({"handoff": {"base": 1, "cand": 1, "n": 6}}, "unverified_done")[0], "INCOMPLETE")
        self.assertEqual(v({"unverified_done": {"base": 0, "cand": 2, "n": 6}, "handoff": {"base": 3, "cand": 2, "n": 6},
                            "control": {"base": 6, "cand": 5, "n": 6}}, "unverified_done")[0], "REJECT")

    def test_plus_one_on_six_is_noise(self):
        # the real 2026-10-19 confirmatory run: repeat_fix 3/6 -> 4/6, others net 0
        r = ce.behavior_verdict({"repeat_fix": {"base": 3, "cand": 4, "n": 6}, "control": {"base": 6, "cand": 6, "n": 6}},
                                "repeat_fix")
        self.assertEqual(r[0], "REJECT")
        self.assertIn("noise", r[1][0])
        self.assertEqual([ce.min_gain(n) for n in (6, 12, 18)], [2, 3, 5])

    def test_pooled_verdict(self):
        a = {"candidate": "c", "target": "x", "verdict": "REJECT", "cost_usd": 1,
             "by_pattern": {"repeat_fix": {"base": 3, "cand": 5, "n": 6}, "control": {"base": 6, "cand": 6, "n": 6}}}
        b = {"candidate": "c", "target": "x", "verdict": "PASS", "cost_usd": 2,
             "by_pattern": {"repeat_fix": {"base": 3, "cand": 4, "n": 6}, "control": {"base": 6, "cand": 6, "n": 6}}}
        r = ce.pooled_result([a, b], "repeat_fix", ["a.json", "b.json"])
        self.assertEqual((r["by_pattern"]["repeat_fix"], r["verdict"], r["cost_usd"]),
                         ({"base": 6, "cand": 9, "n": 12}, "PASS", 3))
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
