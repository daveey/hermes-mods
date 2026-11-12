"""Offline M1 regressions: unittest discovery or pytest; command strings are data.

All state lives in temporary directories. No shell/network/AWS command executes.
"""
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import pwd
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
CMD = "aws ssm send-command --instance-ids i-example"
READ = "aws sts get-caller-identity"
DENIAL = ("User: arn:aws:sts::111122223333:assumed-role/example/session "
          "is not authorized to perform: ssm:SendCommand on resource: "
          "arn:aws:ec2:us-east-1:111122223333:instance/i-example with an explicit deny")
UNKNOWN = "User: arn:aws:iam::111122223333:role/example is not authorized to perform"


def load(file="guard.py"):
    spec = importlib.util.spec_from_file_location("m1_fixture", HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def worker(root, start, count, cap):
    g = load()
    g.MAX_LEDGER_ROWS = cap
    for n in range(start, start + count):
        g.record_from_result("terminal", {"command": CMD},
                             DENIAL.replace("i-example", "i-fixture-" + str(n)), root=root)


class M1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="denial-m1-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.g = load()

    def record(self, command=CMD, result=DENIAL):
        self.g.record_from_result("terminal", {"command": command}, result, root=self.root)

    def classify(self, command=CMD):
        return self.g.classify("terminal", {"command": command}, root=self.root)

    def controls(self):
        self.assertIsNone(self.classify(READ))
        self.assertIsNone(self.classify("git status"))

    def test_actual_root_resolver_and_registration_across_profiles(self):
        account = self.root / "account"
        plugin = load("__init__.py")
        hooks = {}
        with patch.object(pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(account))):
            with patch.dict(os.environ, {"HOME": str(self.root / "ignored"), "HERMES_HOME": str(account / ".hermes/profiles/one")}):
                plugin.register(SimpleNamespace(register_hook=lambda name, fn: hooks.update({name: fn})))
                self.assertEqual(plugin.guard.resolve_root(), account / ".hermes")
                hooks["post_tool_call"](tool_name="terminal", args={"command": CMD}, result=DENIAL)
            with patch.dict(os.environ, {"HERMES_HOME": str(account / ".hermes/profiles/two")}):
                self.assertEqual(hooks["pre_tool_call"](tool_name="terminal", args={"command": CMD})["action"], "block")
        self.assertEqual(set(hooks), {"pre_tool_call", "post_tool_call"})
        self.assertTrue((account / ".hermes/denial-ledger.jsonl").is_file())
        self.assertFalse((account / ".hermes/profiles").exists())

    def test_unregistered_account_profile_and_symlink_refused(self):
        account = self.root / "account"
        account.mkdir()
        link = self.root / "alias"
        link.symlink_to(account / ".hermes")
        with patch.object(pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(account))):
            for root in (None, account / ".hermes", account / ".hermes/profiles/example", link):
                with self.subTest(root=root):
                    with self.assertRaises(PermissionError):
                        self.g.record_from_result("terminal", {"command": CMD}, DENIAL, root=root)
            self.assertEqual(self.g.read_ledger().status, "UNHEALTHY")
        self.assertFalse((account / ".hermes").exists())

    def test_unknown_fingerprints_both_orders_repeat_no_widening(self):
        for order in (("i-one", "i-two"), ("i-two", "i-one")):
            with self.subTest(order=order):
                root = self.root / order[0]
                commands = [CMD.replace("i-example", item) for item in order]
                for command in commands + commands[:1]:
                    self.g.record_from_result("terminal", {"command": command}, UNKNOWN, root=root)
                self.assertEqual(len(self.g.read_ledger(root).rows), 2)
                for command in commands:
                    self.assertTrue(self.g.classify("terminal", {"command": command}, root=root))
                for command in (CMD, READ, "git status"):
                    self.assertIsNone(self.g.classify("terminal", {"command": command}, root=root))

    def test_markers_are_unhealthy_regardless_of_content(self):
        for marker_type in ("empty", "corrupt", "directory"):
            root = self.root / marker_type
            root.mkdir()
            marker = root / "denial-ledger.cap"
            if marker_type == "directory":
                marker.mkdir()
            else:
                marker.write_text("garbage" if marker_type == "corrupt" else "")
            self.assertTrue(self.g.classify("terminal", {"command": CMD}, root=root))
            for command in (READ, "git status"):
                self.assertIsNone(self.g.classify("terminal", {"command": command}, root=root))

    def test_append_and_marker_failure_latch(self):
        with patch.object(self.g.os, "write", side_effect=OSError("synthetic append failure")):
            with self.assertRaises(OSError):
                self.record()
        self.assertTrue(self.classify())
        self.controls()
        other = self.root / "marker-failure"
        self.g.MAX_LEDGER_ROWS = 0
        with patch.object(self.g, "_cap", side_effect=OSError("synthetic marker failure")):
            with self.assertRaises(OSError):
                self.g.record_from_result("terminal", {"command": CMD}, DENIAL, root=other)
        self.assertIn(str(other), self.g._UNHEALTHY)
        self.assertFalse((other / "denial-ledger.cap").exists())

    def test_malformed_ledger_narrow_hold(self):
        (self.root / "denial-ledger.jsonl").write_text('{"partial":')
        self.assertTrue(self.classify())
        self.controls()

    def test_native_kwargs_and_unavailable_receipt(self):
        plugin = load("__init__.py")
        # Register against a synthetic account, not a mocked classifier.
        with patch.object(pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(self.root))):
            plugin.register(SimpleNamespace(register_hook=lambda *args: None))
            kwargs = dict(session_id="synthetic-session", task_id="synthetic-task", tool_call_id="synthetic-call",
                          turn_id=1, duration_ms=2, status="success", error_type=None, error_message=None,
                          middleware_trace=[])
            plugin._post_tool_call(tool_name="terminal", args={"command": CMD}, result={"output": DENIAL}, **kwargs)
            self.assertEqual(plugin._pre_tool_call(tool_name="terminal", args={"command": CMD}, **kwargs)["action"], "block")
            self.assertIsNone(plugin._pre_tool_call(tool_name="terminal", args=None, **kwargs))
        self.assertEqual(self.g.verify_receipt({}, {}), "UNAVAILABLE")

    def run_workers(self, root, cap):
        ctx = multiprocessing.get_context("spawn")
        procs = [ctx.Process(target=worker, args=(str(root), n * 60, 60, cap)) for n in range(8)]
        try:
            for proc in procs:
                proc.start()
            for proc in procs:
                proc.join(45)
            exits = [p.exitcode for p in procs]
            print("M1_WORKER_EXITS", json.dumps({"cap": cap, "exitcodes": exits}), flush=True)
            self.assertEqual(exits, [0] * 8)
        finally:
            for proc in procs:
                if proc.is_alive():
                    proc.terminate()
                    proc.join(5)

    def test_eight_workers_no_lost_rows(self):
        self.run_workers(self.root, 20000)
        rows = self.g.read_ledger(self.root).rows
        self.assertEqual(len(rows), 480)
        self.assertEqual(len({r["event_id"] for r in rows}), 480)

    def test_multiprocess_cap_survives_raised_reader_cap(self):
        self.run_workers(self.root, 7)
        reader = load()
        reader.MAX_LEDGER_ROWS = 100000
        self.assertTrue((self.root / "denial-ledger.cap").exists())
        self.assertEqual(reader.read_ledger(self.root).status, "UNHEALTHY")
        for command in (CMD, "aws ec2 terminate-instances --instance-ids i-other"):
            self.assertTrue(reader.classify("terminal", {"command": command}, root=self.root))
        self.controls()

    def test_legacy_unknown_without_fingerprint_is_unhealthy(self):
        row = dict(action="UNKNOWN", resource="UNKNOWN", principal="synthetic", at=1, explicit=None)
        (self.root / "denial-ledger.jsonl").write_text(json.dumps(row) + "\n")
        self.assertEqual(self.g.read_ledger(self.root).status, "UNHEALTHY")
        self.assertTrue(self.classify())
        self.controls()

    def test_overflow_evidence_is_bounded(self):
        self.g.MAX_LEDGER_ROWS = 0
        for _ in range(300):
            self.record()
        self.assertLessEqual(len(self.g._OVERFLOW[str(self.root)]), 128)
        self.assertTrue(self.classify())

    def test_new_ledger_directory_is_fsynced(self):
        import stat
        fsync = os.fsync
        kinds = []
        def observe(fd):
            kinds.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
            fsync(fd)
        with patch.object(self.g.os, "fsync", side_effect=observe):
            self.record()
        self.assertIn("directory", kinds)
        self.assertLess(kinds.index("file"), kinds.index("directory"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
