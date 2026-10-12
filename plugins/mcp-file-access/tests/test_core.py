"""Unit tests for mcp-file-access. Run: python3 -m unittest discover -s tests (from the plugin dir)."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("mfa_core", Path(__file__).resolve().parents[1] / "core.py")
core = importlib.util.module_from_spec(_spec)
sys.modules["mfa_core"] = core
_spec.loader.exec_module(core)

UPLOAD = "mcp__chrome_devtools__upload_file"
SNAP = "mcp__chrome_devtools__take_snapshot"


class Base(unittest.TestCase):
    def setUp(self):
        core._asked.clear()
        core._staged.clear()
        self._td = tempfile.TemporaryDirectory()
        root = Path(self._td.name).resolve()
        self.server_tmp = root / "server-tmp"
        self.server_tmp.mkdir()
        self.photos = root / "home" / "Photos"
        (self.photos / "sub").mkdir(parents=True)
        self.photo = self.photos / "sub" / "me.jpg"
        self.photo.write_bytes(b"JPEGDATA")
        self.outdir = root / "home" / "out"
        self.outdir.mkdir(parents=True)
        self.roots = [str(self.server_tmp)]
        self.grants = set()

    def tearDown(self):
        self._td.cleanup()

    def approved(self, session, key):
        return key in self.grants

    def gate(self, tool, args, call_id="c1"):
        return core.gate(tool_name=tool, args=args, tool_call_id=call_id, _roots=self.roots,
                         _session_key="s", _is_approved=self.approved)

    def prepare(self, tool, args, call_id="c1"):
        return core.prepare(tool_name=tool, args=args, tool_call_id=call_id, _roots=self.roots,
                            _tmpdir=str(self.server_tmp))

    def run_call(self, tool, args, execute, call_id="c1", post_first=True):
        """Simulate Hermes: hooks in order, merge modify, execute, post + transform."""
        d = self.gate(tool, args, call_id)
        m = self.prepare(tool, args, call_id)
        eff = {**args, **(m["args"] if m else {})}
        result = execute(eff)
        if post_first:
            core.finish(tool_name=tool, tool_call_id=call_id)
            out = core.rewrite_result(tool_name=tool, tool_call_id=call_id, result=result)
        else:
            out = core.rewrite_result(tool_name=tool, tool_call_id=call_id, result=result)
            core.finish(tool_name=tool, tool_call_id=call_id)
        return d, eff, (out if out is not None else result)

    def staging_empty(self):
        d = self.server_tmp / core._STAGE_DIRNAME
        return not d.exists() or not any(d.iterdir())


class GateTests(Base):
    def test_outside_path_requests_approval_for_its_folder(self):
        d = self.gate(UPLOAD, {"uid": "1", "filePaths": [str(self.photo)]})
        self.assertEqual(d["action"], "approve")
        self.assertEqual(d["rule_key"], core.rule_key_for("read", str(self.photo.parent)))
        self.assertIn("read files from", d["message"])

    def test_path_inside_native_root_is_ignored(self):
        f = self.server_tmp / "x.jpg"
        f.write_bytes(b"x")
        self.assertIsNone(self.gate(UPLOAD, {"filePaths": [str(f)]}))
        self.assertIsNone(self.prepare(UPLOAD, {"filePaths": [str(f)]}))

    def test_unrelated_tool_ignored(self):
        self.assertIsNone(self.gate("mcp__chrome_devtools__click", {"uid": "1"}))
        self.assertIsNone(self.gate("read_file", {"path": str(self.photo)}))

    def test_ancestor_grant_reuses_ancestor_rule_key(self):
        self.grants.add("plugin_rule:" + core.rule_key_for("read", str(self.photos)))
        d = self.gate(UPLOAD, {"filePaths": [str(self.photo)]})
        self.assertEqual(d["rule_key"], core.rule_key_for("read", str(self.photos)))

    def test_read_grant_does_not_cover_write(self):
        self.grants.add("plugin_rule:" + core.rule_key_for("read", str(self.outdir)))
        d = self.gate(SNAP, {"filePath": str(self.outdir / "s.txt")})
        self.assertEqual(d["rule_key"], core.rule_key_for("write", str(self.outdir)))
        self.assertIn("save files to", d["message"])


class FlowTests(Base):
    def test_read_is_staged_and_result_shows_real_path(self):
        seen = {}

        def execute(a):
            p = a["filePaths"][0]
            seen["path"], seen["bytes"] = p, Path(p).read_bytes()
            return f"File uploaded from {p}."
        _, eff, out = self.run_call(UPLOAD, {"uid": "1", "filePaths": [str(self.photo)]}, execute)
        self.assertTrue(seen["path"].startswith(str(self.server_tmp)))
        self.assertEqual(seen["bytes"], b"JPEGDATA")
        self.assertEqual(eff["uid"], "1")
        self.assertEqual(out, f"File uploaded from {self.photo}.")
        self.assertTrue(self.staging_empty())
        self.assertEqual(core._staged, {})

    def test_write_lands_at_requested_path_either_hook_order(self):
        for post_first in (True, False):
            dest = self.outdir / f"snap-{post_first}.txt"

            def execute(a):
                Path(a["filePath"]).write_text("tree")
                return f"Saved snapshot to {a['filePath']}."
            _, _, out = self.run_call(SNAP, {"filePath": str(dest)}, execute, call_id=f"c-{post_first}",
                                      post_first=post_first)
            self.assertEqual(dest.read_text(), "tree")
            self.assertEqual(out, f"Saved snapshot to {dest}.")
            self.assertTrue(self.staging_empty())
        self.assertEqual(core._staged, {})

    def test_denied_call_leaves_nothing_behind(self):
        self.gate(UPLOAD, {"filePaths": [str(self.photo)]})
        self.assertIsNotNone(self.prepare(UPLOAD, {"filePaths": [str(self.photo)]}))
        core.finish(tool_name=UPLOAD, tool_call_id="c1")  # Hermes blocked it; post hook still fires
        self.assertTrue(self.staging_empty())

    def test_prepare_without_gate_does_nothing(self):
        # gate crashed / never ran: no staging, server keeps denying.
        self.assertIsNone(self.prepare(UPLOAD, {"filePaths": [str(self.photo)]}))
        self.assertTrue(self.staging_empty())

    def test_gate_record_is_single_use_and_call_specific(self):
        self.gate(UPLOAD, {"filePaths": [str(self.photo)]}, call_id="c1")
        self.assertIsNone(self.prepare(UPLOAD, {"filePaths": [str(self.photo)]}, call_id="other"))
        self.assertIsNotNone(self.prepare(UPLOAD, {"filePaths": [str(self.photo)]}, call_id="c1"))
        self.assertIsNone(self.prepare(UPLOAD, {"filePaths": [str(self.photo)]}, call_id="c1"))


class ConfigTests(unittest.TestCase):
    def test_roots_from_args_and_tmpdir(self):
        cfg = {"args": ["-y", "x", "--filesystem-root", "/a/b", "--workspace=/c"], "env": {"TMPDIR": "/t"}}
        self.assertEqual(core.native_roots(cfg), [os.path.realpath(p) for p in ("/t", "/a/b", "/c")])


if __name__ == "__main__":
    unittest.main()
