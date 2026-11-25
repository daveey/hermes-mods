"""plugin-suites.yml must run every changed test-all suite on GitHub-hosted runners, on both runtime Pythons.

Before it, 52 of 57 plugin suites ran in no CI at all (only plugins with their own workflow did), so pushes
that ran no tests landed red suites unnoticed. This pins the workflow's shape (triggers, matrix, no
self-hosted runner on this public repo, test-all as the runner, public core on the 3.14 leg) and dry-runs its suite selection
(scripts/ci-plugin-suites) on this tree.
"""
import importlib.machinery
import importlib.util
import json
import os
import posixpath
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

    def test_runs_each_suite_through_test_all(self):
        self.assertIn("scripts/test-all --exact", self.text)
        self.assertIn("--json results.json", self.text)
        self.assertIn("scripts/ci-plugin-suites summary results.json", self.text)
        self.assertNotIn("--hermes-core", self.text)  # core comes only from the checkout step's env
        self.assertNotIn("continue-on-error", self.text)

    def test_coach_ci_sharding_is_explicit_without_raising_suite_timeout(self):
        step = self.text.split('      - name: Run selected suites', 1)[1]
        command = next(line for line in step.splitlines()
                       if '"$HERMES_MODS_PYTHON" scripts/test-all --exact' in line)
        self.assertIn('--coach-shards 2', command)
        self.assertNotIn('--timeout', command)

    def test_core_backed_tests_run_against_the_public_core_on_the_314_leg(self):
        # The hub used to run these (hub-mods-suite, hermes-sync mods-suite-gate); now core is public, CI does.
        step = self.text.split("      - name: Check out public core (3.14 leg)\n", 1)[1].split("\n      - ", 1)[0]
        self.assertRegex(step, r"(?m)^        if: matrix\.python == '3\.14'$")
        self.assertIn('fetch -q --depth 1 -- https://github.com/daveey/hermes-agent.git "$CORE_REF"', step)  # ref never an option
        self.assertNotIn("${{ inputs", step.split("run: |", 1)[1])  # inputs reach the shell only through env
        self.assertIn("--locked --group dev --group test", step)  # core's own venv, as the hub syncs it
        self.assertIn('echo "HERMES_MODS_PYTHON=${core}/.venv/bin/python" >> "$GITHUB_ENV"', step)
        self.assertIn("CORE_REF: ${{ inputs.core_ref || 'main' }}", step)
        self.assertIn('echo "HERMES_CORE_CHECKOUT=${core}" >> "$GITHUB_ENV"', step)
        self.assertNotIn("secrets.", step)  # public mirror: no credential reaches this public repo's CI
        self.assertLess(self.text.index("      - name: Check out public core"),
                        self.text.index("      - name: Run selected suites"))
        # hermes-hub's hub-core-mirror matches its dispatch run by this exact title
        self.assertIn("format('plugin-suites core={0}', inputs.core_ref || 'main')", self.text)
        inputs = self.text.split("  workflow_dispatch:\n", 1)[1].split("permissions:", 1)[0]
        self.assertIn("core_ref:", inputs)
        self.assertRegex(inputs, r"(?m)^        default: main$")

    def test_remote_card_composed_is_unconditional_and_uses_landed_inputs(self):
        job = self.text.split('  remote-card-composed:\n', 1)[1].split('\n  suites:', 1)[0]
        self.assertIn('ref: ${{ github.event.pull_request.head.sha || github.sha }}', job)
        # Core main 1c286c2d on the public mirror: carries hermes_cli/kanban_release.py (the Unblock click release).
        self.assertIn('ref: 1c286c2d3b85eeb7525eda8a226aa20fb540f3e6', job)
        self.assertNotIn('0397ba2abd1be2b28c41626152696f0901acab4f', job)
        self.assertIn('ref: 11058ac91990624059dd4214e420334cbcc5d006', job)
        courier_checkout = job.split('          repository: daveey/hermes-hub', 1)[1].split('      - uses:', 1)[0]
        self.assertIn('ssh-key: ${{ secrets.HERMES_HUB_COMPOSED_READ_KEY }}', courier_checkout)
        self.assertIn('persist-credentials: false', courier_checkout)
        self.assertNotIn('token:', courier_checkout)
        self.assertIn('--python 3.14 --locked --group dev --group test', job)
        self.assertIn('bash plugins/user-steps/run-core-integration.sh', job)
        self.assertIn('--remote-card-composed "$RUNNER_TEMP/remote-card-composed" -j 1', job)
        self.assertEqual(re.findall(r'(?m)^\s+if: (.+)$', job), ['always()'])
        self.assertNotIn('continue-on-error', job)
        self.assertIn('if-no-files-found: error', job)

    def test_failure_uploads_full_suite_logs_only(self):
        run = self.text.split("      - name: Run selected suites", 1)[1].split("      - name: Upload", 1)[0]
        self.assertRegex(run, r'--output-dir "\$logs"')
        self.assertIn('logs="${RUNNER_TEMP}/suite-logs"', run)
        step = self.text.split("      - name: Upload full suite logs", 1)[1]
        self.assertRegex(step, r"(?m)^        if: failure\(\)$")
        self.assertIn("uses: actions/upload-artifact@v4", step)
        self.assertIn("name: suite-logs-${{ matrix.python }}", step)
        self.assertIn("path: ${{ runner.temp }}/suite-logs", step)
        self.assertRegex(step, r"(?m)^          retention-days: \d+$")
        self.assertNotIn("continue-on-error", step)
        # Never a way to turn the red job green: the upload step is the last step and the run step still exits $rc.
        self.assertIn("exit $rc", run)


class SkillTestsRootEvidence(unittest.TestCase):
    def test_root_guards_emit_verbose_results_without_replacing_skill_suites(self):
        text = (ROOT / ".github/workflows/skill-tests.yml").read_text()
        step = text.split("      - name: Root guard node evidence\n", 1)[1].split("\n      - ", 1)[0]
        self.assertEqual(step, '''        run: |
          "$HERMES_MODS_PYTHON" -m unittest -v tests/test_plugin_suites_ci.py tests/test_sqlite_ro_guard.py''')
        self.assertLess(text.index('echo "${RUNNER_TEMP}/tools" >> "$GITHUB_PATH"'),
                        text.index("      - name: Root guard node evidence"))
        self.assertLess(text.index("      - name: Root guard node evidence"),
                        text.index("      - name: Skill suites"))
        self.assertIn('"$HERMES_MODS_PYTHON" scripts/test-all -k skills/ -j 4', text)
        self.assertNotIn("continue-on-error", text)


WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow_code(text):
    """Workflow text without YAML comments (`#` at line start or after whitespace)."""
    return "\n".join(re.sub(r"(^|\s)#.*$", "", line) for line in text.splitlines())


def _is_root_working_directory(value):
    """True unless a step working-directory literally names a directory below the repository root."""
    value = value.strip()
    if value[:1] in "|>":  # Block scalar: not resolved; over-report rather than mask a root test run.
        return True
    value = re.sub(r"^\$\{\{\s*github\.workspace\s*\}\}(?=/|$)", ".",
                   value.strip("\"'"), flags=re.I)
    # Only simple literal relative paths are understood. Unknown Actions expressions, YAML tags,
    # anchors/aliases, shell variables and absolute runner paths cannot prove a plugin-local cwd.
    # Deliberately over-report even quoted constructs rather than interpreting YAML or shell syntax.
    if not re.fullmatch(r"[A-Za-z0-9_./-]*", value) or value.startswith('/'):
        return True
    normalized = posixpath.normpath(value or ".")
    return normalized in (".", "..") or normalized.startswith('../')


def _tests_dir_runs(code):
    """Offsets for the supported runner spellings; deliberately not a YAML/shell parser."""
    # A literal step-local working-directory makes `pytest tests` plugin-local.
    # Preserve offsets while masking those steps; this is only the repository's text shape,
    # not general YAML job/defaults or shell-cd analysis.
    direct_code = code
    position = 0
    while step := re.compile(r"(?m)^( +)- [\w-]+:").search(code, position):
        # A step ends at the next line indented no deeper than its dash: the next step, or a dedent out
        # of the step list (including the next job's defaults). Deeper `- key:` items stay inside it.
        indent = len(step.group(1))
        boundary = re.compile(r"(?m)^ {0," + str(indent) + r"}\S").search(code, step.end())
        end = boundary.start() if boundary else len(code)
        position = end
        # Only the step's own key: on the dash line or at its key column, not a nested input or list item.
        # The value stays on the key's line: `[ \t]*`, not `\s*`, so an empty value never reads the next key.
        cwd = re.search(r"(?m)^(?: {%d}- | {%d})working-directory:[ \t]*([^\n]*)" % (indent, indent + 2),
                        code[step.start():end])
        if cwd and not _is_root_working_directory(cwd.group(1)):
            direct_code = direct_code[:step.start()] + " " * (end - step.start()) + direct_code[end:]
    # Join shell continuation lines without changing offsets used by provisioning checks.
    direct_code = direct_code.replace("\\\n", "  ")
    return ([match.start() for match in re.finditer(r"scripts/test-all\b", code)] +
            [match.start() for match in re.finditer(
                r"\b(?:unittest[ \t]+discover|pytest)[ \t]+[^\n]*?"
                r"(?<!\S)(?:\./)?tests/?(?=\s|$|[\"'])", direct_code, re.M)])


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
        runners = {path.name for pattern in ("*.yml", "*.yaml") for path in WORKFLOWS.glob(pattern)
                   if _tests_dir_runs(_workflow_code(path.read_text()))}
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
                runs = _tests_dir_runs(code)
                self.assertTrue(runs, f"{name} runs no tests")
                # Match the gitleaks tools directory, not browser-harness's earlier PATH export.
                extraction = re.search(r'tar -xzf [^\n]+ -C "\$\{RUNNER_TEMP\}/tools" gitleaks', code)
                path_export = re.search(r'echo "\$\{RUNNER_TEMP\}/tools" >> "\$GITHUB_PATH"', code)
                self.assertIsNotNone(extraction, f"{name} does not extract gitleaks into the tools directory")
                self.assertIsNotNone(path_export, f"{name} does not export the gitleaks tools directory")
                assert extraction and path_export
                for label, offset in (("pin", pinned), ("digest check", code.find("sha256sum -c -")),
                                      ("extraction", extraction.start()), ("PATH export", path_export.start())):
                    self.assertLess(offset, min(runs), f"{name} {label} after its tests start")

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


class GitleaksProvisionTripwireMutants(unittest.TestCase):
    """Exercise the text tripwire against isolated workflow mutations, not the live tree."""

    def check_workflows(self, workflows):
        case = GitleaksHistoryScope(
            "test_every_workflow_running_tests_dir_provisions_the_verified_archive")
        result = unittest.TestResult()
        with patch.dict(globals(), WORKFLOWS=workflows):
            case.run(result)
        self.assertFalse(result.errors, result.errors)
        return result

    def test_unprovisioned_consumers_fail(self):
        for filename, command in (
            ("future.yaml", "scripts/test-all"),
            ("future.yml", "python -m unittest discover -s tests"),
            ("future.yml", "pytest -q tests/"),
            ("future.yaml", "python -m pytest ./tests"),
        ):
            with self.subTest(filename=filename, command=command), tempfile.TemporaryDirectory() as directory:
                workflows = Path(directory) / "workflows"
                shutil.copytree(WORKFLOWS, workflows)
                (workflows / filename).write_text(
                    "name: future\non: push\njobs:\n  tests:\n    runs-on: ubuntu-latest\n"
                    f"    steps:\n      - run: {command}\n")
                result = self.check_workflows(workflows)
                self.assertEqual(len(result.failures), 1, result.failures)
                self.assertIn(filename, result.failures[0][1])
                self.assertIn("does not pin", result.failures[0][1])

    def test_late_provisioning_fails(self):
        for fragment in ('tar -xzf', 'echo "${RUNNER_TEMP}/tools" >> "$GITHUB_PATH"', 'sha256sum -c -'):
            with self.subTest(fragment=fragment), tempfile.TemporaryDirectory() as directory:
                workflows = Path(directory) / "workflows"
                shutil.copytree(WORKFLOWS, workflows)
                path = workflows / "skill-tests.yml"
                lines = path.read_text().splitlines()
                moved = next(line for line in lines if fragment in line)
                lines.remove(moved)
                lines.extend(['      - name: Too late', '        run: |', moved])
                path.write_text("\n".join(lines) + "\n")
                result = self.check_workflows(workflows)
                self.assertEqual(len(result.failures), 1, result.failures)
                self.assertIn("after its tests start", result.failures[0][1])

    def test_step_local_directory_does_not_hide_later_root_consumer(self):
        local = ('      - name: Plugin tests\n'
                 '        working-directory: plugins/example\n'
                 '        run: python -m pytest tests\n')
        self.assertEqual(_tests_dir_runs(local), [])
        for cwd in (".", "./", "${{ github.workspace }}"):
            root = (f'      - name: Root tests\n        working-directory: "{cwd}"\n'
                    '        run: python -m unittest discover -s tests\n')
            with self.subTest(cwd=cwd):
                offsets = _tests_dir_runs(local + root)
                self.assertEqual(offsets, [(local + root).index("unittest discover")])

    def check_root_step_after_local(self, first_key):
        local = ('      - name: Plugin tests\n'
                 '        working-directory: plugins/example\n'
                 '        run: python -m pytest tests\n')
        code = local + first_key + '        run: python -m unittest discover -s tests\n'
        self.assertEqual(_tests_dir_runs(code), [code.index('unittest discover')])

    def test_id_first_root_step(self):
        self.check_root_step_after_local('      - id: root\n')

    def test_if_first_root_step(self):
        self.check_root_step_after_local('      - if: always()\n')

    def test_env_first_root_step(self):
        self.check_root_step_after_local('      - env:\n          EXAMPLE: value\n')

    def test_later_job_defaults_do_not_mask_final_root_step(self):
        code = ('jobs:\n  root:\n    steps:\n'
                '      - run: python -m unittest discover -s tests\n'
                '  plugin:\n    defaults:\n      run:\n'
                '        working-directory: plugins/example\n'
                '    steps:\n      - run: echo example\n')
        self.assertEqual(_tests_dir_runs(code), [code.index('unittest discover')])

    def test_continued_pytest_command_is_root_consumer(self):
        code = ('      - run: |\n'
                '          pytest -q ' + '\\\n' +
                '            tests\n')
        self.assertEqual(_tests_dir_runs(code), [code.index('pytest')])

    def test_nested_list_does_not_split_local_step(self):
        local = ('      - name: Plugin tests\n'
                 '        working-directory: plugins/example\n'
                 '        with:\n'
                 '          items:\n'
                 '            - key: value\n'
                 '        run: python -m pytest tests\n')
        self.assertEqual(_tests_dir_runs(local), [])

    def test_root_run_after_nested_list_is_consumer(self):
        code = ('      - name: Root tests\n'
                '        with:\n'
                '          items:\n'
                '            - key: value\n'
                '            - working-directory: plugins/example\n'
                '        run: python -m unittest discover -s tests\n')
        self.assertEqual(_tests_dir_runs(code), [code.index('unittest discover')])

    def test_working_directory_first_local_step(self):
        local = ('      - working-directory: plugins/example\n'
                 '        run: python -m pytest tests\n')
        self.assertEqual(_tests_dir_runs(local), [])

    def test_working_directory_first_root_step_is_consumer(self):
        for cwd in (".", "./", "${{ github.workspace }}"):
            code = (f'      - working-directory: "{cwd}"\n'
                    '        run: python -m unittest discover -s tests\n')
            with self.subTest(cwd=cwd):
                self.assertEqual(_tests_dir_runs(code), [code.index('unittest discover')])

    def test_action_input_working_directory_does_not_mask_root_step(self):
        # `with: working-directory:` is an action input two columns below the step key, not the step's cwd.
        # Pins the key-column match: widening it to "that column or deeper" would hide this root run.
        code = ('      - name: Root tests\n'
                '        with:\n'
                '          working-directory: plugins/example\n'
                '        run: python -m unittest discover -s tests\n')
        self.assertEqual(_tests_dir_runs(code), [code.index('unittest discover')])

    def test_root_equivalent_working_directory_is_consumer(self):
        for cwd in ('./.', '"./."', "'./.'", './', '././', '"${{ github.workspace }}/"',
                    '${{github.workspace}}', '"${{ github.workspace }}/."', '""', ''):
            code = (f'      - name: Root tests\n        working-directory: {cwd}\n'
                    '        run: python -m unittest discover -s tests\n')
            with self.subTest(cwd=cwd):
                self.assertEqual(_tests_dir_runs(code), [code.index('unittest discover')])

    def test_block_scalar_working_directory_is_treated_as_root(self):
        # Block scalars are not resolved here; over-report rather than mask a genuine root test run.
        for indicator in ('>-', '|', '>', '|-', '|2'):
            for value in ('.', 'plugins/example'):
                code = (f'      - name: Root tests\n        working-directory: {indicator}\n'
                        f'          {value}\n'
                        '        run: python -m unittest discover -s tests\n')
                with self.subTest(indicator=indicator, value=value):
                    self.assertEqual(_tests_dir_runs(code), [code.index('unittest discover')])

    def test_unresolved_or_nonrelative_directories_are_consumers(self):
        # Do not evaluate YAML, Actions contexts, shell variables or runner-specific absolute paths.
        for cwd in ('${{ GITHUB.WORKSPACE }}', '${{ GiThUb.WoRkSpAcE }}/.',
                    '${{ env.ROOT }}', '${{ inputs.dir }}',
                    'plugins/${{ inputs.dir }}', '${{ env.ROOT }}/plugins/example',
                    '!!str .', '*root', '&root .', '!custom plugins/example',
                    '/home/runner/work/example/example', '/home/runner/work/example/example/plugins/example',
                    'C:\\actions\\example', '\\\\server\\share',
                    '$GITHUB_WORKSPACE', '${GITHUB_WORKSPACE}/plugins/example',
                    '$GITHUB_WORKSPACE/plugins/example', 'plugins/$SUBDIR',
                    '../example', 'plugins/../../example'):
            for quoted in (cwd, '"' + cwd + '"', "'" + cwd + "'"):
                code = (f'      - working-directory: {quoted}\n'
                        '        run: python -m pytest tests\n')
                with self.subTest(cwd=quoted):
                    self.assertEqual(_tests_dir_runs(code), [code.index('pytest')])

    def test_live_consumer_inventory_is_exact(self):
        self.assertEqual(
            {path.name for pattern in ('*.yml', '*.yaml') for path in WORKFLOWS.glob(pattern)
             if _tests_dir_runs(_workflow_code(path.read_text()))},
            {'plugin-suites.yml', 'repo-guards.yml', 'skill-tests.yml'})

    def test_plugin_local_spellings_stay_masked(self):
        for cwd in ('plugins/example', './plugins/example', 'plugins/example/../example',
                    '"plugins/example"', "'plugins/example'",
                    '"${{ github.workspace }}/plugins/example"',
                    '"${{ GITHUB.WORKSPACE }}/plugins/example"'):
            local = (f'      - name: Plugin tests\n        working-directory: {cwd}\n'
                     '        run: python -m pytest tests\n')
            with self.subTest(cwd=cwd):
                self.assertEqual(_tests_dir_runs(local), [])

    def test_comments_and_other_test_directories_are_not_consumers(self):
        with tempfile.TemporaryDirectory() as directory:
            workflows = Path(directory) / "workflows"
            shutil.copytree(WORKFLOWS, workflows)
            (workflows / "not-a-consumer.yaml").write_text(
                "# scripts/test-all\n# python -m unittest discover -s tests\n"
                "# pytest tests\n"
                "      - run: pytest plugins/example/tests # pytest tests\n"
                "      - run: python -m unittest discover -s tests_other\n")
            result = self.check_workflows(workflows)
            self.assertTrue(result.wasSuccessful(), result.failures)


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

    def test_failing_suite_names_its_failed_test_ids_and_log(self):
        # pytest's 4,500 skip rows push FAILED rows out of test-all's tail; the receipt carries them instead.
        mod = _load_selector()
        with tempfile.TemporaryDirectory() as d:
            res = Path(d, "r.json")
            res.write_text(json.dumps({"python": "3.14.10", "suites": [
                {"name": "plugins/coach", "status": "FAIL", "passed": 3067, "failed": 2, "errors": 1, "skipped": 4560,
                 "failed_ids": ["FAILED plugins/coach/tests/test_a.py::T::test_one",
                                "ERROR plugins/coach/tests/test_b.py::test_two"],
                 "log_path": "/x/plugins_coach-abc.log", "log_sha256": "f" * 64}]}))
            out = StringIO()
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(Path(d, "summary.md"))}), \
                    redirect_stdout(out):
                self.assertEqual(mod.summary(res), 1)
            text = out.getvalue()
            self.assertIn("::error title=plugins/coach failed test on Python 3.14.10::"
                          "FAILED plugins/coach/tests/test_a.py::T::test_one", text)
            self.assertIn("ERROR plugins/coach/tests/test_b.py::test_two", text)
            self.assertIn("plugins_coach-abc.log sha256 " + "f" * 64, text)
            self.assertIn("- `FAILED plugins/coach/tests/test_a.py::T::test_one`", Path(d, "summary.md").read_text())
            # A pre-existing receipt without failed_ids still summarizes.
            res.write_text(json.dumps({"python": "3.14.10", "suites": [
                {"name": "plugins/coach", "status": "FAIL", "passed": 1, "failed": 1, "errors": 0, "skipped": 0}]}))
            with redirect_stdout(StringIO()):
                self.assertEqual(mod.summary(res), 1)


if __name__ == "__main__":
    unittest.main()
