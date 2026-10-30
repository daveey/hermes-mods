"""commit-scope-guard: a commit spanning two mods is refused; a one-mod commit passes; override works.

Replays the shape of 74e8c4e (desktop-plugins/session-panel + deletions in plugins/session-mentions) through
the real .githooks/pre-commit in a throwaway repo, plus unit checks of the pure classifier.
Run: python3 -m pytest tests/test_commit_scope_guard.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / ".githooks" / "commit-scope-guard.py"
HOOK = ROOT / ".githooks" / "pre-commit"
sys.path.insert(0, str(ROOT / ".githooks"))
import importlib.util
_spec = importlib.util.spec_from_file_location("csg", GUARD)
csg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(csg)

ENV = {"HOME": "/nonexistent", "PATH": os.environ["PATH"], "GIT_CONFIG_GLOBAL": "/dev/null",
       "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


class Classifier(unittest.TestCase):
    def test_same_name_plugin_and_desktop_half_is_one_mod(self):
        mods, _ = csg.check(["M\tplugins/foo/__init__.py", "M\tdesktop-plugins/foo/plugin.js", "M\tREADME.md"])
        self.assertEqual(list(mods), ["foo"])

    def test_skills_keyed_by_name(self):
        mods, _ = csg.check(["M\tskills/devops/a/SKILL.md", "M\tskills/devops/b/SKILL.md"])
        self.assertEqual(sorted(mods), ["a", "b"])


class Hook(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        (self.repo / ".githooks").mkdir(parents=True)
        shutil.copy(GUARD, self.repo / ".githooks" / GUARD.name)
        shutil.copy(HOOK, self.repo / ".githooks" / "pre-commit")
        shutil.copy(ROOT / ".gitleaks.toml", self.repo / ".gitleaks.toml")
        self.g("init", "-q")
        self.g("config", "core.hooksPath", ".githooks")
        self.write("plugins/session-mentions/shepherd.py", "x = 1\n")
        self.write("desktop-plugins/session-panel/plugin.js", "a\n")
        self.g("add", ".")
        self.g("-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")

    def write(self, rel, text):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def g(self, *a, **env):
        return subprocess.run(["git", *a], cwd=self.repo, capture_output=True, text=True, env={**ENV, **env})

    def commit(self, **env):
        # Hook's later privacy steps may refuse for missing tools; allow-unscanned isolates the guard under test.
        return self.g("commit", "-qm", "c", HERMES_MODS_ALLOW_UNSCANNED="1", **env)

    def test_74e8c4e_shape_refused(self):
        self.write("desktop-plugins/session-panel/plugin.js", "b\n")
        self.g("rm", "-q", "plugins/session-mentions/shepherd.py")
        self.g("add", "desktop-plugins/session-panel/plugin.js")
        r = self.commit()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("span 2 unrelated mods", r.stderr)
        self.assertIn("DELETED", r.stderr)

    def test_single_plugin_commit_allowed(self):
        self.write("desktop-plugins/session-panel/plugin.js", "b\n")
        self.write("tests/test_x.py", "pass\n")
        self.g("add", ".")
        r = self.commit()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_override_allows_multi_mod(self):
        self.write("desktop-plugins/session-panel/plugin.js", "b\n")
        self.g("rm", "-q", "plugins/session-mentions/shepherd.py")
        self.g("add", ".")
        r = self.commit(HERMES_MODS_ALLOW_MULTI_MOD="1")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
