"""plugin-suites.yml must run every changed test-all suite on GitHub-hosted runners, on both runtime Pythons.

Before it, 52 of 57 plugin suites ran in no CI at all (only plugins with their own workflow did), so pushes
that ran no tests landed red suites unnoticed. This pins the workflow's shape (triggers, matrix, no
self-hosted runner on this public repo, test-all as the runner) and dry-runs its suite selection
(scripts/ci-plugin-suites) on this tree.
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "plugin-suites.yml"
SELECTOR = ROOT / "scripts" / "ci-plugin-suites"


def _load_selector():
    loader = importlib.machinery.SourceFileLoader("ci_plugin_suites", str(SELECTOR))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("ci_plugin_suites", loader))
    loader.exec_module(mod)
    return mod


def _section(text, key):
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.rstrip() == key + ":")
    out = []
    for ln in lines[start + 1:]:
        if ln and not ln.startswith((" ", "\t")):
            break
        out.append(ln)
    return out


class PluginSuitesWorkflow(unittest.TestCase):
    def setUp(self):
        self.assertTrue(WF.is_file(), f"missing {WF.relative_to(ROOT)}")
        self.text = WF.read_text()
        self.on = _section(self.text, "on")

    def test_triggers_push_main_pr_dispatch_and_weekly_schedule(self):
        keys = {ln.strip() for ln in self.on}
        for trigger in ("push:", "pull_request:", "workflow_dispatch:", "schedule:"):
            self.assertIn(trigger, keys, self.on)
        self.assertTrue(any(re.fullmatch(r"\s*branches:\s*\[main\]", ln) for ln in self.on), self.on)
        cron = [ln for ln in self.on if "cron:" in ln]
        self.assertEqual(len(cron), 1, self.on)
        fields = cron[0].split("cron:", 1)[1].strip().strip("'\"").split()
        self.assertEqual(len(fields), 5, cron)
        self.assertNotEqual(fields[4], "*", "schedule must be weekly (one day-of-week)")
        self.assertFalse([ln for ln in self.on if re.match(r"\s*(paths|paths-ignore):", ln)],
                         "selection is done by scripts/ci-plugin-suites, never a paths filter")

    def test_github_hosted_only(self):
        code = "\n".join(ln.split("#", 1)[0] for ln in self.text.splitlines())
        self.assertNotIn("self-hosted", code)
        runs_on = re.findall(r"^\s*runs-on:\s*(.+)$", self.text, re.M)
        self.assertTrue(runs_on)
        self.assertEqual(set(runs_on), {"ubuntu-latest"}, runs_on)

    def test_matrix_runs_both_runtime_pythons_with_uv(self):
        self.assertRegex(self.text, r"python: \['3\.11', '3\.14'\]")
        self.assertIn("fail-fast: false", self.text)
        self.assertIn("astral-sh/setup-uv@v6", self.text)
        self.assertIn("uv python install '${{ matrix.python }}'", self.text)

    def test_sqlite_fixtures_enable_privileged_read_only_holder_scan(self):
        step = self.text.split('      - name: Run selected suites', 1)[1]
        env = step.split('        env:', 1)[1].split('        run:', 1)[0]
        self.assertRegex(env, r"(?m)^          SWAP_TEST_PRIVILEGED_SCAN: ['\"]1['\"]$")

    def test_full_history_and_selection_per_event(self):
        self.assertIn("fetch-depth: 0", self.text)
        self.assertIn('select --base "$PR_BASE"', self.text)
        self.assertIn("github.event.pull_request.base.sha", self.text)
        self.assertIn('select --base "$PUSH_BEFORE"', self.text)
        self.assertIn("github.event.before", self.text)
        self.assertIn("select --all", self.text)  # schedule + workflow_dispatch

    def test_gitleaks_is_provisioned_pinned_and_sha256_checked_off_the_desktop_serve_path(self):
        # live-checkout-guard's real pre-commit tests need gitleaks; without it they skipped silently here
        # (28 of them, PR #61/#62's end-to-end proofs included). Same pin and digest as gitleaks.yml.
        step = self.text.split("      - name: Suite provisions", 1)[1].split("\n      - name:", 1)[0]
        version_m = re.search(r"(?m)^\s+gl_version=(\S+)$", step)
        digest_m = re.search(r"(?m)^\s+gl_sha256=([0-9a-f]{64})$", step)
        assert version_m and digest_m, step
        version, digest = version_m.group(1), digest_m.group(1)
        self.assertIn('sha256sum -c -', step)
        self.assertIn('>> "$GITHUB_PATH"', step)
        self.assertIn('"${RUNNER_TEMP}/tools"', step)  # not /usr/bin: tests simulate a PATH without it
        scan = (ROOT / ".github" / "workflows" / "gitleaks.yml").read_text()
        self.assertIn(f"/download/v{version}/gitleaks_{version}_linux_x64.tar.gz", scan)
        self.assertIn(f"{digest}  gitleaks.tar.gz\" | sha256sum -c -", scan)
        skill_publish = (ROOT / "plugins" / "live-checkout-guard" / "tests_skill_publish.py").read_text()
        self.assertIn(f"gitleaks v{version}", skill_publish)

    def test_browser_harness_is_provisioned_at_one_pin_for_both_chrome_workflows(self):
        # invisible-chrome-agent's real stop/retirement suites skipped in CI for want of it (5 tests).
        pins = set(re.findall(r"browser-harness==([0-9.]+)", self.text))
        self.assertEqual(len(pins), 1, pins)
        for name in ("invisible-chrome-agent.yml", "skill-tests.yml"):  # the other runners of that suite
            other = (ROOT / ".github" / "workflows" / name).read_text()
            self.assertEqual(set(re.findall(r"browser-harness==([0-9.]+)", other)), pins, name)
        self.assertIn('uv tool dir --bin)" >> "$GITHUB_PATH"', self.text)

    def test_runs_each_suite_through_test_all_without_core(self):
        self.assertIn("scripts/test-all --exact", self.text)
        self.assertIn("--json results.json", self.text)
        self.assertIn("scripts/ci-plugin-suites summary results.json", self.text)
        self.assertNotIn("--hermes-core", self.text)
        self.assertNotIn("continue-on-error", self.text)


WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow_code(text):
    """Workflow text without YAML comments (`#` at line start or after whitespace)."""
    return "\n".join(re.sub(r"(^|\s)#.*$", "", line) for line in text.splitlines())


class GitleaksHistoryScope(unittest.TestCase):
    def test_missing_binary_fails_in_ci(self):
        with patch.dict(os.environ, {"CI": "true"}), patch.object(shutil, "which", return_value=None):
            try:
                with self.assertRaisesRegex(AssertionError, "CI must provision"):
                    self.test_checked_out_history_and_unignored_branch_controls()
            except unittest.SkipTest as exc:
                # SkipTest escapes assertRaisesRegex, so a fail->skip change would otherwise report OK (skipped=1).
                self.fail(f"missing gitleaks under CI=true must fail, not skip: {exc}")

    def test_missing_binary_skips_outside_ci(self):
        with patch.dict(os.environ, {"CI": ""}), patch.object(shutil, "which", return_value=None):
            with self.assertRaises(unittest.SkipTest):
                self.test_checked_out_history_and_unignored_branch_controls()

    def test_every_workflow_running_tests_dir_provisions_the_verified_archive(self):
        # tests/ runs in repo-guards.yml directly and in every workflow that runs scripts/test-all (it adds the
        # repo-guards suite to each -k run). Each must put the archive gitleaks.yml pins on PATH before its tests.
        scan = (WORKFLOWS / "gitleaks.yml").read_text()
        version = re.search(r"/download/v([0-9.]+)/", scan)
        digest = re.search(r"\b([0-9a-f]{64})  gitleaks\.tar\.gz", scan)
        assert version and digest, scan
        runners = {"repo-guards.yml"} | {path.name for path in WORKFLOWS.glob("*.yml")
                                         if "scripts/test-all" in _workflow_code(path.read_text())}
        self.assertLessEqual({"plugin-suites.yml", "repo-guards.yml", "skill-tests.yml"}, runners)
        for name in sorted(runners):
            with self.subTest(workflow=name):
                code = _workflow_code((WORKFLOWS / name).read_text())
                pinned = code.find(digest.group(1))
                self.assertGreaterEqual(pinned, 0, f"{name} does not pin the gitleaks.yml archive sha256")
                assigned = re.search(r"\bgl_version=([0-9.]+)", code)  # plugin-suites.yml's variable form
                expanded = code.replace("${gl_version}", assigned.group(1)) if assigned else code
                self.assertIn(f"/gitleaks/gitleaks/releases/download/v{version.group(1)}/", expanded)
                self.assertIn("sha256sum -c -", code)
                self.assertIn('>> "$GITHUB_PATH"', code)
                runs = [i for i in (code.find("scripts/test-all"), code.find("unittest discover")) if i >= 0]
                self.assertTrue(runs, f"{name} runs no tests")
                self.assertLess(pinned, min(runs), f"{name} provisions gitleaks after its tests start")

    def test_checked_out_history_and_unignored_branch_controls(self):
        binary = shutil.which("gitleaks")
        if binary is None:
            if os.environ.get("CI", "").lower() == "true":
                self.fail("CI must provision the workflow-pinned gitleaks binary")
            self.skipTest("install the workflow-pinned gitleaks binary")
        workflow = (ROOT / ".github/workflows/gitleaks.yml").read_text()
        version_match = re.search(r"/download/v([0-9.]+)/", workflow)
        assert version_match, workflow
        version = version_match.group(1)
        self.assertEqual(subprocess.check_output([binary, "version"], text=True).strip(), version)
        command = re.findall(r"(?m)^\s+(\./gitleaks git .+)$", workflow)
        self.assertEqual(len(command), 1)
        argv = shlex.split(command[0])
        # Run the workflow's exact argv, including its relative binary/config paths.
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            env.update(GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.com",
                       GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.com")

            def git(*args):
                return subprocess.check_output(["git", "-C", directory, *args],
                                               env=env, stderr=subprocess.STDOUT, text=True).strip()

            def commit(message):
                git("add", ".")
                git("-c", "core.hooksPath=/dev/null", "commit", "-qm", message)

            def scan(expected):
                result = subprocess.run(argv, cwd=repo, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

            git("init", "-q", "-b", "main")
            # Deterministic synthetic rule: no real credential or entropy-dependent fixture.
            (repo / ".gitleaks.toml").write_text(
                '[[rules]]\nid = "fixture"\ndescription = "scope fixture"\n'
                'regex = "SCOPE_TEST_[A-Z]+"\n')
            (repo / ".gitleaksignore").write_text("")
            (repo / ".gitignore").write_text("gitleaks\n")
            (repo / "gitleaks").symlink_to(binary)
            commit("base")
            git("branch", "branch-b")
            (repo / "main.txt").write_text("SCOPE_TEST_MAIN\n")
            # Path/rule/line fingerprints allow the ignore and finding in the same commit.
            (repo / ".gitleaksignore").write_text("main.txt:fixture:1\n")
            commit("main finding and matching ignore")
            main = git("rev-parse", "HEAD")
            scan(0)
            git("checkout", "-q", "branch-b")
            self.assertNotIn(main, git("rev-list", "HEAD").splitlines())
            self.assertIn(main, git("rev-list", "--all").splitlines())
            scan(0)  # RED with implicit --all: unrelated main finding, old ignore tree.
            (repo / "branch.txt").write_text("SCOPE_TEST_BRANCH\n")
            commit("unignored branch finding")
            scan(1)  # push-shaped HEAD must still detect a real branch finding.
            git("merge", "--no-ff", "-qm", "pull request merge", "main")
            scan(1)  # merge-shaped PR HEAD must detect it too.


class SuiteSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_selector()
        cls.suites = cls.mod.list_suites(ROOT)

    def pick(self, *paths):
        return self.mod.select(ROOT, self.suites, list(paths))[0]

    def test_list_is_test_all_list(self):
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "test-all"), "--list"],
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(self.suites, [ln.split("  [skip:")[0] for ln in out.splitlines() if ln.strip()])
        self.assertIn("plugins/mac-heavy-guard", self.suites)

    def test_cpu_load_guard_only_change_selects_only_its_suite(self):
        self.assertEqual(self.pick("plugins/cpu-load-guard/guard.py", "plugins/cpu-load-guard/README.md"),
                         ["plugins/cpu-load-guard"])

    def test_mac_heavy_guard_test_change_selects_its_suite(self):
        # tailnet's runner.py names plugins/mac-heavy-guard (it overrides its lexer), so it is selected too (8c59b46d).
        self.assertEqual(self.pick("plugins/mac-heavy-guard/tests_guard.py"),
                         ["plugins/mac-heavy-guard", "plugins/tailnet"])

    def test_shared_runner_helpers_and_workflow_select_every_suite(self):
        for path in ("scripts/test-all", "scripts/ci-plugin-suites", "tests/test_test_all.py",
                     "lib/anything.py", ".github/workflows/plugin-suites.yml"):
            self.assertEqual(self.pick("README.md", path), self.suites, path)

    def test_unknown_base_selects_every_suite(self):
        self.assertEqual(self.mod.select(ROOT, self.suites, None)[0], self.suites)
        self.assertIsNone(self.mod.changed_paths(ROOT, "0" * 40, "HEAD"))
        self.assertIsNone(self.mod.changed_paths(ROOT, "", "HEAD"))
        self.assertIsNone(self.mod.changed_paths(ROOT, "f" * 40, "HEAD"))

    def test_docs_only_change_selects_nothing(self):
        self.assertEqual(self.pick("README.md", "core/README.md"), [])

    def test_importers_of_a_changed_plugin_are_selected(self):
        picked = self.pick("plugins/ask-gate/__init__.py")
        self.assertIn("plugins/ask-gate", picked)
        self.assertIn("plugins/coach", picked)  # coach imports ask-gate
        self.assertNotIn("tests", picked)  # repo-guards.yml runs tests/ on every push

    def test_skill_and_desktop_paths_map_to_their_suites(self):
        # assertIn, not equality: a plugin whose files name the skill (push-guard names github-ci-wait's
        # ci_run_cancel.py since c89ec6c6) is selected with it.
        picked = self.pick("skills/devops/github-ci-wait/SKILL.md")
        self.assertIn("skills/devops/github-ci-wait", picked)
        # ask-gate now references push-guard, which references this skill: it is a
        # legitimate transitive dependent, not a stable unrelated-suite control.
        self.assertIn("plugins/push-guard", picked)
        self.assertIn("plugins/ask-gate", picked)
        self.assertIn("plugins/inbox", self.pick("desktop-plugins/inbox/plugin.js"))

    def test_skill_dependency_closure_excludes_unrelated_suite(self):
        # Keep negative coverage independent of the evolving repository graph.
        suites = ["skills/devops/example", "plugins/direct", "plugins/transitive",
                  "plugins/unrelated", "repo-guards", "tests"]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for suite, source in {
                "plugins/direct": 'SKILL = "skills/devops/example/scripts/check.py"',
                "plugins/transitive": 'DEPENDENCY = "plugins/direct/helper.py"',
                "plugins/unrelated": 'VALUE = 1',
            }.items():
                directory = root / suite
                directory.mkdir(parents=True)
                (directory / "helper.py").write_text(source)
            picked, _ = self.mod.select(root, suites, ["skills/devops/example/SKILL.md"])
            self.assertEqual(picked, suites[:3])


class ChangedPaths(unittest.TestCase):
    def test_diff_from_merge_base_to_head(self):
        mod = _load_selector()
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}

            def git(*a):
                return subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, text=True,
                                      env=env).stdout.strip()
            git("init", "-q", "-b", "main")
            Path(d, "a.txt").write_text("a")
            git("add", "."), git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            git("checkout", "-qb", "pr")
            Path(d, "plugins", "x").mkdir(parents=True)
            Path(d, "plugins", "x", "f.py").write_text("x")
            git("add", "."), git("commit", "-qm", "pr")
            git("checkout", "-q", "main")
            Path(d, "b.txt").write_text("b")  # base moved on: not part of the PR's change
            git("add", "."), git("commit", "-qm", "main moves")
            moved = git("rev-parse", "HEAD")
            self.assertEqual(mod.changed_paths(Path(d), moved, "pr"), ["plugins/x/f.py"])
            self.assertEqual(mod.changed_paths(Path(d), base, "pr"), ["plugins/x/f.py"])
            # A break then its revert nets to no diff, but the suite it touched must still run.
            git("checkout", "-q", "pr")
            Path(d, "plugins", "y").mkdir(parents=True)
            Path(d, "plugins", "y", "t.py").write_text("break")
            git("add", "."), git("commit", "-qm", "break y")
            git("rm", "-q", "plugins/y/t.py"), git("commit", "-qm", "revert y")
            self.assertEqual(mod.changed_paths(Path(d), moved, "pr"), ["plugins/x/f.py", "plugins/y/t.py"])


class Summary(unittest.TestCase):
    def test_table_counts_and_failure_exit(self):
        mod = _load_selector()
        with tempfile.TemporaryDirectory() as d:
            res = Path(d, "r.json")
            res.write_text(json.dumps({"python": "3.11.9", "suites": [
                {"name": "plugins/a", "status": "PASS", "passed": 5, "failed": 0, "errors": 0, "skipped": 2},
                {"name": "plugins/b", "status": "FAIL", "passed": 3, "failed": 1, "errors": 0, "skipped": 0,
                 "reason": ""},
                {"name": "tests/integration_f3", "status": "SKIP", "reason": "needs a core checkout"}]}))
            out = StringIO()
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(Path(d, "summary.md"))}), \
                    redirect_stdout(out):
                rc = mod.summary(res)
            text = out.getvalue()
            self.assertEqual(rc, 1)
            self.assertRegex(text, r"plugins/a\s+3\.11\.9\s+PASS\s+5\s+0\s+0\s+2")
            self.assertRegex(text, r"TOTAL\s+3\.11\.9\s+3 suites\s+8\s+1\s+0\s+2")
            self.assertIn("::error title=plugins/b failed on Python 3.11.9::plugins/b FAIL on Python 3.11.9: 1 failed", text)
            self.assertIn("suite skipped: tests/integration_f3: needs a core checkout", text)
            self.assertIn("| plugins/b | 3.11.9 | FAIL | 3 | 1 | 0 | 0 |", Path(d, "summary.md").read_text())
            res.write_text(json.dumps({"python": "3.14.0", "suites": [
                {"name": "plugins/a", "status": "PASS", "passed": 1, "failed": 0, "errors": 0, "skipped": 0}]}))
            with redirect_stdout(StringIO()):
                self.assertEqual(mod.summary(res), 0)


if __name__ == "__main__":
    unittest.main()
