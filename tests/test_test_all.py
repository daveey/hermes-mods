"""scripts/test-all: one broken plugin is reported by name, never swallowed, and the exit code says so."""
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
import tempfile

RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "test-all"


def make_tree(root: Path, plugins: dict[str, str]) -> None:
    for name, body in plugins.items():
        d = root / "plugins" / name
        d.mkdir(parents=True)
        # Same basename in every plugin: a single-process run would collide on it.
        (d / "test_load.py").write_text(textwrap.dedent(body))


def run(root: Path, *args: str):
    env = {k: v for k, v in os.environ.items() if k != "HERMES_CORE_CHECKOUT"}
    env["HERMES_MODS_PYTHON"] = sys.executable
    return subprocess.run([sys.executable, str(RUNNER), "--root", str(root), "--hermes-core", "/nonexistent", *args],
                          capture_output=True, text=True, env=env, timeout=120)


class TestAll(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_same_named_test_modules_do_not_collide_and_all_pass(self):
        make_tree(self.root, {"a": "def test_a():\n    assert True\n", "b": "def test_b():\n    assert True\n"})
        r = run(self.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2 PASS", r.stdout)

    def test_collection_error_in_one_plugin_is_reported_by_name_and_fails_the_run(self):
        make_tree(self.root, {"good": "def test_ok():\n    assert True\n",
                              "broken": "import module_that_does_not_exist\n\ndef test_x():\n    pass\n"})
        r = run(self.root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"FAIL\s+plugins/broken")
        self.assertRegex(r.stdout, r"PASS\s+plugins/good")
        self.assertIn("module_that_does_not_exist", r.stdout)
        self.assertIn("FAILED: plugins/broken", r.stdout)

    def test_process_that_dies_during_collection_is_labelled_silent_exit_not_swallowed(self):
        # The real incident: a test module re-exec'd/exited the interpreter at import, so pytest
        # printed nothing and exited 1.
        make_tree(self.root, {"dies": "import os\nos._exit(1)\n", "good": "def test_ok():\n    pass\n"})
        r = run(self.root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"SILENT-EXIT\s+plugins/dies")
        self.assertIn("exit 1 with no pytest summary", r.stdout)

    def test_hanging_suite_times_out_and_is_reported(self):
        make_tree(self.root, {"hang": "import time\ntime.sleep(60)\n"})
        r = run(self.root, "--timeout", "3")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"TIMEOUT\s+plugins/hang")

    def test_integration_f3_is_skipped_with_reason_without_a_core_checkout(self):
        f3 = self.root / "tests" / "integration_f3"
        f3.mkdir(parents=True)
        (f3 / "test_f3.py").write_text("def test_f3():\n    raise SystemExit('needs core')\n")
        (self.root / "tests" / "test_top.py").write_text("def test_top():\n    pass\n")
        r = run(self.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("skipped tests/integration_f3: needs a core checkout", r.stdout)


if __name__ == "__main__":
    unittest.main()
