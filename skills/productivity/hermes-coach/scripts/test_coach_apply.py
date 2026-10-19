#!/usr/bin/env python3
"""Tests for rollout tiers, coach-apply auto, revert and stale handling:  python3 test_coach_apply.py
Uses temp git repos and a fake `hermes` binary (config get/set/unset + kanban create); never pushes."""
import json, os, subprocess, sys, tempfile, textwrap, time, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import coach_apply as ca  # noqa: E402
import coach_proposals as cp  # noqa: E402
import coach_weekly  # noqa: E402

FAKE_HERMES = r'''#!/usr/bin/env python3
import json, sys, os
f = os.environ["FAKE_CFG"]
cfg = json.load(open(f)) if os.path.exists(f) else {}
a = sys.argv[1:]
if a[:2] == ["config", "get"]:
    if a[2] in cfg: print(cfg[a[2]])
    else: sys.exit(1)
elif a[:2] == ["config", "set"]:
    cfg[a[2]] = a[3]
elif a[:2] == ["config", "unset"]:
    cfg.pop(a[2], None)
elif a[0] == "inbox":
    if os.environ.get("FAKE_INBOX_FAIL"): sys.exit(3)
    open(f + ".inbox", "a").write(json.dumps(a) + "\n")
elif a[0] == "kanban":
    open(f + ".cards", "a").write(json.dumps(a) + "\n")
    print(json.dumps({"id": "t_abc12345"}))
json.dump(cfg, open(f, "w"))
'''

SKILL = "---\nname: demo-skill\ndescription: Use when demoing.\n---\n\n# Demo\n\n- Verify end to end.\n"
RUNNABLE = textwrap.dedent("""\
    - id: device_checklist
      feature: One device checklist
      trigger: {text: "(?i)^done$"}
      tip: "Send one numbered checklist."
      adopted: {unit: sessions, min: 3}
    """)


def git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def init_repo(d):
    d.mkdir(parents=True)
    git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.email", "t@example.com")
    git(d, "config", "user.name", "t")
    git(d, "config", "core.hooksPath", "/dev/null")
    (d / "README").write_text("x\n")
    git(d, "add", ".")
    git(d, "commit", "-q", "-m", "init")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for n in ("data", "mods", "priv"):
            init_repo(self.tmp / n)
        (self.tmp / "data/reports").mkdir()
        sk = self.tmp / "mods/skills/demo/demo-skill"
        sk.mkdir(parents=True)
        (sk / "SKILL.md").write_text(SKILL)
        git(self.tmp / "mods", "add", ".")
        git(self.tmp / "mods", "commit", "-q", "-m", "skill")
        (self.tmp / "priv/shared-config.json").write_text("{}\n")
        git(self.tmp / "priv", "add", ".")
        git(self.tmp / "priv", "commit", "-q", "-m", "cfg")
        self.home = self.tmp / "home"
        (self.home / "coach").mkdir(parents=True)
        (self.home / "coach/curriculum.yaml").write_text("# overlay\n")
        fake = self.tmp / "hermes"
        fake.write_text(FAKE_HERMES)
        fake.chmod(0o755)
        os.environ["FAKE_CFG"] = str(self.tmp / "cfg.json")
        self.env = ca.Env(data=self.tmp / "data", mods=self.tmp / "mods", priv=self.tmp / "priv", home=self.home,
                          hermes=str(fake), push=False)
        self.n = 0

    def add(self, date=None, **p):
        self.n += 1
        p.setdefault("evidence", "macbook/default/x — t: q")
        rec = {"id": f"{date or time.strftime('%Y-%m-%d')}-{self.n}", "fp": cp.fingerprint(p),
               "date": date or time.strftime("%Y-%m-%d"), "status": "pending", "at": ca.now_iso(), **p}
        cp.append(self.env.data, rec)
        return rec["id"]

    def st(self, pid):
        return cp.state(self.env.data)[pid]

    def run_auto(self, now=None):
        return ca.auto(self.env, now=now, out=lambda *_: None)


class TierDefaults(unittest.TestCase):
    def t(self, **p):
        return cp.default_tier(p)

    def test_defaults(self):
        self.assertEqual(self.t(kind="curriculum", target="~/.hermes/coach/curriculum.yaml"), "auto")
        self.assertEqual(self.t(kind="skill_patch", target="babybear-ship-and-ops"), "auto")
        self.assertEqual(self.t(kind="config", target="coach.fix_loop.threshold"), "auto")
        self.assertEqual(self.t(kind="config", target="agent.max_turns"), "canary")
        self.assertEqual(self.t(kind="shared_config", target="display.busy_input_mode"), "canary")
        self.assertEqual(self.t(kind="plugin_rule", target="plugins/coach/fix_loop.py"), "canary")
        self.assertEqual(self.t(kind="core", target="agent loop"), "review")
        self.assertEqual(self.t(kind="config", target="model.default"), "review")
        self.assertEqual(self.t(kind="plugin_rule", target="~/.hermes/hermes-agent/run_agent.py"), "review")
        self.assertEqual(self.t(kind="curriculum", target="x.yaml", paid=True), "review")
        self.assertEqual(self.t(kind="curriculum", target="x.yaml", cost_usd_day=5), "review")
        self.assertEqual(self.t(kind="skill_patch", target="s", risk="high"), "review")

    def test_bot_may_only_raise(self):
        self.assertEqual(cp.effective_tier({"kind": "skill_patch", "target": "s", "tier": "canary"}), "canary")
        self.assertEqual(cp.effective_tier({"kind": "config", "target": "agent.max_turns", "tier": "auto"}), "canary")
        self.assertEqual(cp.effective_tier({"kind": "core", "target": "x", "tier": "auto"}), "review")

    def test_add_assigns_tier(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "reports").mkdir()
            rep = Path(d) / "reports/weekly-2026-10-25.md"
            blocks = [{"kind": "config", "target": "agent.max_turns", "change": {"value": 400}, "evidence": "e",
                       "tier": "auto"},
                      {"kind": "skill_patch", "target": "s", "change": {"old_string": "a", "new_string": "b"},
                       "evidence": "e"}]
            rep.write_text("\n".join(f"```coach-proposal\n{json.dumps(b)}\n```" for b in blocks))
            cp.main(["add", "--data", d, "--report", str(rep)])
            st = cp.state(d)
            self.assertEqual(st["2026-10-25-1"]["tier"], "canary")
            self.assertEqual(st["2026-10-25-2"]["tier"], "auto")

    def test_old_rows_get_default_tier(self):
        with tempfile.TemporaryDirectory() as d:
            cp.append(d, {"id": "a", "kind": "curriculum", "target": "c.yaml", "change": {"append": "x"},
                          "status": "pending", "date": "2026-10-14"})
            self.assertEqual(cp.state(d)["a"]["tier"], "auto")


class Inbox(Base):
    def posts(self):
        f = Path(os.environ["FAKE_CFG"] + ".inbox")
        rows = [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []
        return [(r[2], r[r.index("--key") + 1]) for r in rows]

    def test_applied_then_reverted_updates_one_card(self):
        pid = self.add(kind="skill_patch", target="demo-skill", title="Check the device",
                       change={"file_path": "SKILL.md", "old_string": "- Verify end to end.\n",
                               "new_string": "- Verify end to end.\n- Then check the device.\n"})
        self.run_auto()
        self.assertEqual(self.posts(), [("Rule: Check the device", f"coach-improvement:{pid}")])
        row = json.loads(Path(os.environ["FAKE_CFG"] + ".inbox").read_text().splitlines()[0])
        body = row[row.index("--body") + 1]
        self.assertIn(f"coach-apply revert {pid}", body)
        self.assertIn(f"coach-apply show {pid}", row[row.index("--prompt") + 1])
        ca.cmd_revert(self.env, type("A", (), {"id": pid, "note": ""})())
        self.assertEqual(self.posts()[-1], ("Undone · Rule: Check the device", f"coach-improvement:{pid}"))

    def test_canary_then_kept_same_key(self):
        pid = self.add(kind="config", target="agent.max_turns", change={"value": 400})
        self.run_auto()
        orig = ca.canary_verdict
        try:
            ca.canary_verdict = lambda env, p, now: "KEEP: cap hits/session -90%."
            self.run_auto()
        finally:
            ca.canary_verdict = orig
        heads = [t.split(":")[0] for t, _ in self.posts()]
        self.assertEqual(heads, ["Trying · Setting", "Kept · Setting"])
        self.assertEqual({k for _, k in self.posts()}, {f"coach-improvement:{pid}"})

    def test_cards_and_failures_never_post(self):
        self.add(kind="core", target="agent loop", change={"summary": "retry on 529"})
        self.add(kind="skill_patch", target="demo-skill", change={"old_string": "nope", "new_string": "y"})
        self.run_auto()
        self.assertEqual(self.posts(), [])

    def test_inbox_failure_does_not_block_apply(self):
        os.environ["FAKE_INBOX_FAIL"] = "1"
        try:
            pid = self.add(kind="skill_patch", target="demo-skill",
                           change={"file_path": "SKILL.md", "old_string": "- Verify end to end.\n",
                                   "new_string": "- Verify end to end.\n- X.\n"})
            self.run_auto()
        finally:
            os.environ.pop("FAKE_INBOX_FAIL")
        self.assertEqual(self.st(pid)["status"], "applied")

    def test_cli_mark_applied_posts(self):
        pid = self.add(kind="core", target="agent loop", title="Retry on 529", change={"summary": "x"})
        os.environ["COACH_HERMES"] = self.env.hermes
        try:
            cp.main(["mark", "--data", str(self.env.data), "--id", pid, "--status", "applied",
                     "--by", "kanban t_1", "--result", "shipped abc123"])
        finally:
            os.environ.pop("COACH_HERMES")
        self.assertEqual(self.posts(), [("Code: Retry on 529", f"coach-improvement:{pid}")])

    def row(self, i=-1):
        return json.loads(Path(os.environ["FAKE_CFG"] + ".inbox").read_text().splitlines()[i])

    def body(self, i=-1):
        r = self.row(i)
        return r[r.index("--body") + 1]

    def test_card_copy_now_before_and_literal_tip(self):
        pid = self.add(kind="curriculum", target=str(self.home / "coach/curriculum.yaml"),
                       title="Teach parking cards", short="Park cards on an outside blocker",
                       effect="When a chat mentions a billing failure, you get a tip to park the cards.",
                       before="93 Kanban runs retried the same billing error ($460).",
                       evidence="macbook/default/20261017_183803_d4f3fe — Dot UX 1: 'refused every job'",
                       change={"append": RUNNABLE})
        self.run_auto()
        self.assertEqual(self.st(pid)["status"], "applied")
        self.assertEqual(self.posts(), [("Tip: Park cards on an outside blocker", f"coach-improvement:{pid}")])
        b = self.body()
        self.assertTrue(b.startswith("**Now:** When a chat mentions a billing failure"), b)
        self.assertIn("**Before:** 93 Kanban runs retried", b)
        self.assertIn("> [!TIP]\n> Send one numbered checklist.", b)  # the tip text, unescaped
        self.assertIn("this Mac only", b)
        self.assertIn(f"Undo: `coach-apply revert {pid}`", b)
        self.assertIn("“Dot UX 1”", b)  # session not on this host: its title, not a raw id
        self.assertNotIn("20261017_183803_d4f3fe", b)
        self.assertNotIn("curriculum.yaml", b)  # no file path as "what changed"
        self.assertEqual(self.row()[self.row().index("--prompt-label") + 1], ca.PROMPT_LABEL)

    def test_card_copy_fallbacks_rule_text_and_core_left_out(self):
        pid = self.add(kind="skill_patch", target="demo-skill", title="Check the device",
                       evidence="macbook/default/20261016_190336_ccc9cb — Camera audio: 'every live check was silent'; "
                                "also 20261014_205123_3d7c36",
                       change={"file_path": "SKILL.md", "old_string": "- Verify end to end.\n",
                               "new_string": "- Verify end to end.\n- Then check the device.\n"})
        self.run_auto()
        b = self.body()
        self.assertIn("**Before:** 'every live check was silent'", b)  # evidence quote, without the session id
        self.assertIn("Added to the `demo-skill` skill:\n\n> Then check the device.", b)
        self.assertIn("all Macs (synced)", b)
        title, body = ca.inbox_card(self.env, {"id": "x-1", "kind": "core", "target": "tools/kanban_tools.py",
                                               "title": "Classify rejections", "at": "2026-10-19T17:08:40Z",
                                               "note": "part 2 declined: retry already blocks",
                                               "evidence": "e"}, "applied", "core 13c3780570: rejection class")
        self.assertEqual(title, "Code: Classify rejections")
        self.assertIn("**Shipped:** core 13c3780570: rejection class", body)
        self.assertIn("**Left out:** part 2 declined", body)
        self.assertIn("live Oct 19, 10:08 AM PDT", body)

    def test_old_plugin_without_prompt_label_still_posts(self):
        fake = Path(self.env.hermes)
        fake.write_text(FAKE_HERMES.replace('elif a[0] == "inbox":',
                                            'elif a[0] == "inbox" and "--prompt-label" in a:\n'
                                            '    sys.stderr.write("unrecognized arguments: --prompt-label"); sys.exit(2)\n'
                                            'elif a[0] == "inbox":'))
        pid = self.add(kind="skill_patch", target="demo-skill", title="Check the device",
                       change={"file_path": "SKILL.md", "old_string": "- Verify end to end.\n",
                               "new_string": "- Verify end to end.\n- X.\n"})
        self.run_auto()
        self.assertEqual(self.posts(), [("Rule: Check the device", f"coach-improvement:{pid}")])

    def test_opt_out(self):
        os.environ["COACH_INBOX"] = "0"
        try:
            self.add(kind="skill_patch", target="demo-skill",
                     change={"file_path": "SKILL.md", "old_string": "- Verify end to end.\n",
                             "new_string": "- Verify end to end.\n- X.\n"})
            self.run_auto()
        finally:
            os.environ.pop("COACH_INBOX")
        self.assertEqual(self.posts(), [])


class AutoApply(Base):
    def test_skill_patch_auto_applies_commits_and_reverts(self):
        pid = self.add(kind="skill_patch", target="demo-skill",
                       change={"file_path": "SKILL.md", "old_string": "- Verify end to end.\n",
                               "new_string": "- Verify end to end.\n- Then check the device.\n"})
        done = self.run_auto()
        self.assertEqual(done[0][1], "applied")
        p = self.st(pid)
        self.assertEqual(p["status"], "applied")
        self.assertEqual(p["by"], "coach-apply auto")
        self.assertTrue(p["commit"])
        f = self.tmp / "mods/skills/demo/demo-skill/SKILL.md"
        self.assertIn("check the device", f.read_text())
        week = "\n".join(coach_weekly.section_proposals(self.env.data))
        self.assertIn(f"coach-apply revert {pid}", week)
        cp.append(self.env.data, {"id": "hand-1", "kind": "curriculum", "target": "c.yaml", "change": {"append": "x"},
                                  "status": "applied", "date": time.strftime("%Y-%m-%d"), "at": ca.now_iso()})
        week = "\n".join(coach_weekly.section_proposals(self.env.data))
        self.assertIn("hand-1", week)
        self.assertNotIn("coach-apply revert hand-1", week)
        ca.main_env = None
        ca.cmd_revert(self.env, type("A", (), {"id": pid, "note": ""})())
        self.assertNotIn("check the device", f.read_text())
        self.assertEqual(self.st(pid)["status"], "reverted")

    def test_curriculum_local_file_backup_and_revert(self):
        pid = self.add(kind="curriculum", target="~/.hermes/coach/curriculum.yaml", change={"append": RUNNABLE})
        if not self._yaml():
            self.skipTest("no YAML parser")
        self.run_auto()
        f = self.home / "coach/curriculum.yaml"
        self.assertIn("device_checklist", f.read_text())
        p = self.st(pid)
        self.assertEqual((p["status"], p["tier"]), ("applied", "auto"))
        other = self.add(kind="curriculum", target="~/.hermes/coach/curriculum.yaml",
                         change={"append": RUNNABLE.replace("device_checklist", "second_tip")})
        self.run_auto()
        ca.cmd_revert(self.env, type("A", (), {"id": pid, "note": ""})())
        self.assertNotIn("device_checklist", f.read_text())
        self.assertIn("second_tip", f.read_text())  # a later edit to the same file survives
        ca.cmd_revert(self.env, type("A", (), {"id": other, "note": ""})())
        self.assertEqual(f.read_text(), "# overlay\n")

    def test_gate_rejects_prose_curriculum_and_bad_anchor(self):
        if not self._yaml():
            self.skipTest("no YAML parser")
        a = self.add(kind="curriculum", target="~/.hermes/coach/curriculum.yaml",
                     change={"append": "- feature: x\n  trigger: when it rains\n  tip: t\n"})
        b = self.add(kind="skill_patch", target="demo-skill", change={"old_string": "nope", "new_string": "y"})
        done = dict((d[0], d[1]) for d in self.run_auto())
        self.assertEqual(done, {a: "gate failed", b: "gate failed"})
        self.assertEqual(self.st(a)["status"], "pending")
        self.assertEqual((self.home / "coach/curriculum.yaml").read_text(), "# overlay\n")

    def test_canary_config_host_only_then_promote(self):
        pid = self.add(kind="shared_config", target="agent.max_turns", change={"value": 400}, signal="synthetic_turn_cap")
        self.run_auto()
        p = self.st(pid)
        self.assertEqual((p["status"], p["tier"], p["prev"]), ("canary", "canary", "__unset__"))
        self.assertEqual(json.load(open(os.environ["FAKE_CFG"]))["agent.max_turns"], "400")
        self.assertEqual(json.loads((self.tmp / "priv/shared-config.json").read_text()), {})  # MacBook only
        self.assertTrue(p["check"].startswith("agent.max_turns=400@"))
        self.assertIn(p["check"], coach_weekly.canary_checks(self.env.data))
        orig = ca.canary_verdict
        try:
            ca.canary_verdict = lambda env, p, now: "KEEP: cap hits/session -90%, cost/session +1%."
            self.run_auto()
        finally:
            ca.canary_verdict = orig
        p = self.st(pid)
        self.assertEqual(p["status"], "applied")
        self.assertEqual(json.loads((self.tmp / "priv/shared-config.json").read_text()), {"agent.max_turns": 400})
        ca.cmd_revert(self.env, type("A", (), {"id": pid, "note": ""})())
        self.assertEqual(json.loads((self.tmp / "priv/shared-config.json").read_text()), {})
        self.assertNotIn("agent.max_turns", json.load(open(os.environ["FAKE_CFG"])))

    def test_canary_revert_verdict(self):
        cfg = Path(os.environ["FAKE_CFG"])
        cfg.write_text(json.dumps({"agent.max_turns": "300"}))
        pid = self.add(kind="config", target="agent.max_turns", change={"value": 500})
        self.run_auto()
        orig = ca.canary_verdict
        try:
            ca.canary_verdict = lambda env, p, now: "REVERT: cost/session up +40% while cap hits/session only fell 5%."
            self.run_auto()
        finally:
            ca.canary_verdict = orig
        self.assertEqual(self.st(pid)["status"], "reverted")
        self.assertEqual(json.loads(cfg.read_text())["agent.max_turns"], "300")

    def test_review_tier_files_card(self):
        pid = self.add(kind="core", target="agent loop", change={"summary": "retry on 529"})
        self.run_auto()
        p = self.st(pid)
        self.assertEqual((p["status"], p["card"]), ("carded", "t_abc12345"))
        call = json.loads(Path(os.environ["FAKE_CFG"] + ".cards").read_text().splitlines()[0])
        self.assertEqual(call[:4], ["kanban", "--board", "hermes", "create"])
        self.assertIn(f"coach-proposal-{pid}", call)

    def test_stale_pending_dropped_with_reason(self):
        old = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 9 * 86400))
        pid = self.add(date=old, kind="skill_patch", target="demo-skill", change={"old_string": "zz", "new_string": "y"})
        fresh = self.add(kind="skill_patch", target="demo-skill", change={"old_string": "zz", "new_string": "q"})
        self.run_auto()
        p = self.st(pid)
        self.assertEqual(p["status"], "dropped")
        self.assertIn("stale", p["note"])
        self.assertIn("old_string found 0 times", p["note"])
        self.assertEqual(self.st(fresh)["status"], "pending")
        self.assertFalse([q for q in cp.state(self.env.data).values()
                          if q["status"] == "pending" and cp.age_days(q) > cp.STALE_DAYS])

    def test_stale_pending_that_passes_is_applied(self):
        old = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 9 * 86400))
        pid = self.add(date=old, kind="skill_patch", target="demo-skill",
                       change={"old_string": "# Demo\n", "new_string": "# Demo skill\n"})
        self.run_auto()
        self.assertEqual(self.st(pid)["status"], "applied")

    def test_precommit_failure_restores_file(self):
        hook = self.tmp / "hooks"
        hook.mkdir()
        (hook / "pre-commit").write_text("#!/bin/sh\necho 'denylist hit' >&2\nexit 1\n")
        (hook / "pre-commit").chmod(0o755)
        git(self.tmp / "mods", "config", "core.hooksPath", str(hook))
        pid = self.add(kind="skill_patch", target="demo-skill", change={"old_string": "# Demo\n", "new_string": "# D\n"})
        done = self.run_auto()
        self.assertEqual(done[0][1], "gate failed")
        self.assertIn("denylist hit", done[0][2])
        self.assertEqual((self.tmp / "mods/skills/demo/demo-skill/SKILL.md").read_text(), SKILL)
        self.assertEqual(self.st(pid)["status"], "pending")

    def _yaml(self):
        try:
            ca.yaml_load("a: 1")
            return True
        except ca.ApplyError:
            return False


if __name__ == "__main__":
    unittest.main()
