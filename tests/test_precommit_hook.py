"""Guard: the pre-commit hook must REFUSE a commit when its privacy checks cannot run.

Root cause it guards: the hook treated a missing gitleaks as a warning and looked for the private denylist only at
the Mac path ~/code/hermes/mods-denylist.txt. On a Linux host (no brew, state repo at
~/.hermes/repos/hermes-state/) every commit to this PUBLIC repo skipped both the secret scan and the denylist,
and exited 0.

Each test runs the real .githooks/pre-commit in a throwaway repo with HOME and PATH controlled, so no real
gitleaks or denylist on the machine running the tests can leak in.

Run: ``python3 -m pytest tests/test_precommit_hook.py`` or ``python3 tests/test_precommit_hook.py``.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".githooks" / "pre-commit"
WORD = "zorblaxquux"  # a made-up denylisted word


def _tool_dir(tmp: Path) -> Path:
    """A PATH dir holding the tools the hook needs (sh, git, grep, ...) and NOT gitleaks."""
    d = tmp / "toolbin"
    d.mkdir()
    for name in ("sh", "git", "grep", "cut", "head", "mktemp", "rm", "cat", "uname", "python3", "dirname",
                 "basename", "env", "tr", "sed"):
        p = shutil.which(name)
        if p:
            (d / name).symlink_to(p)
    return d


class PreCommitHook(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        (self.repo / ".githooks").mkdir(parents=True)
        shutil.copy(HOOK, self.repo / ".githooks" / "pre-commit")
        shutil.copy(ROOT / ".gitleaks.toml", self.repo / ".gitleaks.toml")
        self.path = str(_tool_dir(self.tmp))
        self.git("init", "-q")
        self.git("add", ".")

    def env(self, **extra):
        e = {"HOME": str(self.home), "PATH": self.path, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@example.com"}
        e.update(extra)
        return e

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, env=self.env())

    def git_out(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True,
                              env=self.env()).stdout

    def commit_base(self, name, text):
        (self.repo / name).write_text(text)
        self.git("add", name)
        self.git("commit", "-q", "-m", "base")

    def stage(self, text):
        (self.repo / "f.txt").write_text(text)
        self.git("add", "f.txt")

    def fake_gitleaks(self):
        """A gitleaks stand-in that records each call and passes."""
        self.called = self.tmp / "gitleaks-called"
        p = Path(self.path) / "gitleaks"
        p.write_text(f"#!/bin/sh\necho run >> '{self.called}'\nexit 0\n")
        p.chmod(0o755)

    def hub_denylist(self):
        d = self.home / ".hermes" / "repos" / "hermes-state"
        d.mkdir(parents=True)
        (d / "mods-denylist.txt").write_text(f"# test list\n{WORD}\n")

    def run_hook(self, *args, **env):
        return subprocess.run(["sh", ".githooks/pre-commit", *args], cwd=self.repo, capture_output=True,
                              text=True, env=self.env(**env))

    def test_missing_gitleaks_and_denylist_refuses_commit(self):
        self.stage("hello\n")
        r = self.run_hook()
        self.assertEqual(r.returncode, 1, f"hook let an unscanned commit through:\n{r.stderr}")
        self.assertIn("gitleaks", r.stderr)
        self.assertIn("denylist", r.stderr)

    def test_missing_denylist_alone_refuses_commit(self):
        self.fake_gitleaks()
        self.stage("hello\n")
        r = self.run_hook()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("denylist", r.stderr)

    def test_install_hint_fits_the_os(self):
        self.stage("hello\n")
        r = self.run_hook()
        if os.uname().sysname == "Darwin":
            self.assertIn("brew install gitleaks", r.stderr)
        else:
            self.assertNotIn("brew install", r.stderr)
            self.assertIn("install.sh --tools", r.stderr)

    def test_hub_denylist_blocks_a_denylisted_word(self):
        self.fake_gitleaks()
        self.hub_denylist()
        self.stage(f"note about {WORD}\n")
        r = self.run_hook()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("match the private denylist", r.stderr)
        self.assertTrue(self.called.exists(), "gitleaks was not run")

    def test_clean_commit_passes_with_both_checks_run(self):
        self.fake_gitleaks()
        self.hub_denylist()
        self.stage("hello\n")
        r = self.run_hook()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.called.exists(), "gitleaks was not run")

    def test_env_denylist_wins(self):
        self.fake_gitleaks()
        deny = self.tmp / "custom-deny.txt"
        deny.write_text(WORD + "\n")
        self.stage(f"{WORD}\n")
        r = self.run_hook(HERMES_MODS_DENYLIST=str(deny))
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn(str(deny), r.stderr)

    # The denylist scan must see every line a commit adds, whatever shape the staged diff takes.
    BODY = "".join(f"line {i} of a file long enough to stay a rename after one more line\n" for i in range(20))

    def test_denylist_scans_lines_added_in_a_renamed_file(self):
        """A rename+edit is an R entry, which `--diff-filter=AM` dropped whole: 21c46445 renamed a skill and added
        a denylisted word in the same commit, and the hook let it into the public tree."""
        self.fake_gitleaks()
        self.hub_denylist()
        self.commit_base("old.md", self.BODY)
        self.git("mv", "old.md", "new.md")
        (self.repo / "new.md").write_text(self.BODY + f"e.g. {WORD} assistants\n")
        self.git("add", "new.md")
        self.assertTrue(self.git_out("diff", "--cached", "--name-status").startswith("R"),
                        "fixture is not a rename, so it would not exercise the rename path")
        r = self.run_hook()
        self.assertEqual(r.returncode, 1, f"a rename+edit slipped past the denylist:\n{r.stderr}")
        self.assertIn("match the private denylist", r.stderr)

    def test_clean_rename_passes(self):
        self.fake_gitleaks()
        self.hub_denylist()
        self.commit_base("old.md", self.BODY)
        self.git("mv", "old.md", "new.md")
        r = self.run_hook()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_pure_rename_rescans_the_file_it_republishes(self):
        """A file that already leaks (committed before a pattern joined the list, or past an older hook) is
        refused when it moves, so the move scrubs it instead of republishing it under a new path."""
        self.fake_gitleaks()
        self.commit_base("old.md", self.BODY + f"e.g. {WORD} assistants\n")
        self.hub_denylist()
        self.git("mv", "old.md", "new.md")
        self.assertTrue(self.git_out("diff", "--cached", "--name-status").startswith("R100"),
                        "fixture is not a pure rename")
        r = self.run_hook()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("match the private denylist", r.stderr)

    def test_denylist_scans_a_new_files_path(self):
        self.fake_gitleaks()
        self.hub_denylist()
        (self.repo / f"{WORD}-notes.md").write_text("hello\n")
        self.git("add", f"{WORD}-notes.md")
        r = self.run_hook()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("match the private denylist", r.stderr)

    def test_denylist_scans_a_symlink_replaced_by_a_file(self):
        """A type change (T) was dropped by `--diff-filter=AM` too."""
        self.fake_gitleaks()
        self.hub_denylist()
        self.commit_base("target.md", "hello\n")
        (self.repo / "link.md").symlink_to("target.md")
        self.git("add", "link.md")
        self.git("commit", "-q", "-m", "link")
        (self.repo / "link.md").unlink()
        (self.repo / "link.md").write_text(f"{WORD}\n")
        self.git("add", "link.md")
        self.assertTrue(self.git_out("diff", "--cached", "--name-status").startswith("T"),
                        "fixture is not a type change")
        r = self.run_hook()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("match the private denylist", r.stderr)

    def test_denylist_scans_an_added_line_that_starts_with_plus_plus(self):
        """`grep -v '^+++'`, meant for the `+++ b/<path>` header, also dropped added lines that start with `++`."""
        self.fake_gitleaks()
        self.hub_denylist()
        self.stage(f"++ {WORD}\n")
        r = self.run_hook()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("match the private denylist", r.stderr)

    def test_denylist_scan_ignores_color_config(self):
        """With color.diff=always every diff line starts with an escape code, so `grep '^+'` matched nothing."""
        self.fake_gitleaks()
        self.hub_denylist()
        self.git("config", "color.diff", "always")
        self.stage(f"{WORD}\n")
        r = self.run_hook()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("match the private denylist", r.stderr)

    def test_denylist_scan_ignores_an_external_diff_driver(self):
        """A configured diff.external replaces git's patch output, so the scan read whatever that tool printed."""
        self.fake_gitleaks()
        self.hub_denylist()
        self.git("config", "diff.external", shutil.which("true"))
        self.stage(f"{WORD}\n")
        r = self.run_hook()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("match the private denylist", r.stderr)

    def test_explicit_override_passes_loudly(self):
        self.stage("hello\n")
        r = self.run_hook(HERMES_MODS_ALLOW_UNSCANNED="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("WARNING", r.stderr)
        self.assertIn("HERMES_MODS_ALLOW_UNSCANNED", r.stderr)

    def test_check_tools_mode_reports_missing(self):
        """hermes-sync calls `pre-commit --check-tools` to log/notify hosts that cannot commit safely."""
        r = self.run_hook("--check-tools")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "gitleaks denylist")
        self.fake_gitleaks()
        self.hub_denylist()
        r = self.run_hook("--check-tools")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""))


if __name__ == "__main__":
    unittest.main()
