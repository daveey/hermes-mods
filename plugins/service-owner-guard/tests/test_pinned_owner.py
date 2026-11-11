"""service-owner-guard: a pinned-owner unit is never stopped by an agent's raw command.

hermes-hub 2026-11-11: hermes-gateway-askeve-builder stopped five times by agent-run hold scripts and
recovery units. The unit has a ``50-askeve-pinned-owner.conf`` drop-in but no owner card, so the owner
guard let every raw stop through, and a stop of one coupled owner made the operator checks refuse.
Fixtures only: ``run_cmd`` is stubbed; no real service is touched.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_guard import Base  # noqa: E402

PINNED = "hermes-gateway-askeve-builder"
PLAIN = "hermes-gateway"
DROPIN = f"/home/u/.config/systemd/user/{PINNED}.service.d/50-askeve-pinned-owner.conf"


class PinnedOwner(Base):
    def setUp(self):
        super().setUp()
        self.shown = []

        def run(argv, timeout=5):
            if argv[0] == "systemctl" and "show" in argv:
                self.shown.append(argv)
                unit = argv[-1]
                if unit == PINNED or unit == PINNED + ".service":
                    return 0, DROPIN + "\n"
                return 0, "\n"
            return 1, ""
        self.g.run_cmd = run

    def test_raw_systemctl_stop_refused_and_logged(self):
        msg = self.verdict(f"systemctl --user stop {PINNED}")
        self.assertIn("pinned owner", msg)
        self.assertIn("owner terminal", msg)
        self.assertIn("pinned-owner", self.log.read_text())

    def test_service_suffix_and_flags_refused(self):
        self.assertIsNotNone(self.verdict(f"systemctl --user --no-block stop {PINNED}.service"))

    def test_owner_approved_token_does_not_override(self):
        self.assertIsNotNone(self.verdict(f"systemctl --user stop {PINNED} # owner-approved=t_abc123"))

    def test_unit_without_pinned_dropin_allowed(self):
        self.assertIsNone(self.verdict(f"systemctl --user stop {PLAIN}"))

    def test_start_restart_and_status_never_refused(self):
        for verb in ("start", "restart", "status", "is-active", "reload"):
            self.assertIsNone(self.verdict(f"systemctl --user {verb} {PINNED}"), verb)

    def test_hermes_gateway_stop_for_pinned_profile_refused(self):
        self.assertIsNotNone(self.verdict("hermes -p askeve-builder gateway stop"))
        self.assertIsNotNone(self.verdict("~/.local/bin/hermes --profile askeve-builder gateway stop"))
        self.assertIsNone(self.verdict("hermes gateway status"))
        self.assertIsNone(self.verdict("hermes -p askeve-builder gateway status"))

    def test_script_that_stops_the_unit_refused_when_run(self):
        script = Path(self.tmp.name) / "hold.sh"
        script.write_text(f"#!/bin/bash\nset -u\nsystemctl --user stop {PINNED} && echo stopped\n"
                          f"sleep 3600\nsystemctl --user start {PINNED}\n")
        for run in (f"bash {script}", f"/bin/bash {script} > /dev/null 2>&1", f"nohup sh {script} &"):
            self.assertIsNotNone(self.verdict(run), run)

    def test_script_without_a_stop_allowed(self):
        script = Path(self.tmp.name) / "ok.sh"
        script.write_text(f"#!/bin/bash\nsystemctl --user start {PINNED}\n")
        self.assertIsNone(self.verdict(f"bash {script}"))

    def test_python_script_with_literal_stop_refused(self):
        script = Path(self.tmp.name) / "hold.py"
        script.write_text(f"import subprocess\nsubprocess.run(['systemctl', '--user', 'stop', '{PINNED}'])\n")
        self.assertIsNotNone(self.verdict(f"python3 {script}"))

    def test_execute_code_path_refused(self):
        code = f"import subprocess\nsubprocess.run(['systemctl','--user','stop','{PINNED}'])\n"
        self.assertIsNotNone(self.verdict(code, tool="execute_code"))

    def test_nested_tool_call_path_refused(self):
        r = self.g._pre_tool_call("tool_call", {"calls": [
            {"name": "terminal", "arguments": {"command": f"systemctl --user stop {PINNED}"}}]})
        self.assertEqual(r["action"], "block")

    def test_heredoc_script_written_and_run_in_one_command_refused(self):
        cmd = (f"cat > /tmp/hold.sh <<'EOF'\nsystemctl --user stop {PINNED}\nEOF\nbash /tmp/hold.sh")
        self.assertIsNotNone(self.verdict(cmd))

    def test_guard_off_switch_still_disables(self):
        with mock.patch.dict(os.environ, {"SERVICE_OWNER_GUARD": "off"}):
            self.assertIsNone(self.verdict(f"systemctl --user stop {PINNED}"))

    def test_lookup_failure_is_fail_open_for_stop(self):
        self.g.run_cmd = lambda argv, timeout=5: (1, "")
        self.assertIsNone(self.verdict(f"systemctl --user stop {PINNED}"))


if __name__ == "__main__":
    unittest.main()
