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
        self.shown = []  # every `systemctl show` the guard issued

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

    def test_start_restart_and_status_never_refused_and_never_looked_up(self):
        for verb in ("start", "restart", "status", "is-active", "reload", "show"):
            self.assertIsNone(self.verdict(f"systemctl --user {verb} {PINNED}"), verb)
        self.assertEqual(self.shown, [])

    def test_a_stop_looks_the_unit_up_once_per_unit(self):
        self.verdict(f"systemctl --user stop {PINNED}")
        self.assertEqual([a[-1] for a in self.shown if "DropInPaths" in a], [PINNED + ".service"])

    def test_shell_keywords_do_not_hide_the_stop(self):
        for cmd in (f"if true; then systemctl --user stop {PINNED}; fi",
                    f"while true; do systemctl --user stop {PINNED}; sleep 1; done",
                    f"{{ systemctl --user stop {PINNED}; }}", f"! systemctl --user stop {PINNED}",
                    f"time systemctl --user stop {PINNED}"):
            self.assertIsNotNone(self.verdict(cmd), cmd)

    def test_hold_script_with_loop_and_function_refused(self):
        script = Path(self.tmp.name) / "loop.sh"
        script.write_text(f"#!/bin/bash\nstop_it() {{ systemctl --user stop {PINNED}; }}\n"
                          f"for i in 1 2; do\n  stop_it\ndone\n")
        self.assertIsNotNone(self.verdict(f"bash {script}"))

    def test_script_option_values_and_run_forms(self):
        script = Path(self.tmp.name) / "hold.sh"
        script.write_text(f"systemctl --user stop {PINNED}\n")
        script.chmod(0o755)
        for cmd in (f"bash -o pipefail {script}", f"setsid -f bash {script}", f"source {script}", f". {script}",
                    f"{script}"):
            self.assertIsNotNone(self.verdict(cmd), cmd)

    def test_python_c_and_list_form_programs(self):
        self.assertIsNotNone(self.verdict(
            f"python3 -c \"import subprocess; subprocess.run(['systemctl','--user','stop','{PINNED}'])\""))
        code = "import subprocess\nsubprocess.run(['hermes','-p','askeve-builder','gateway','stop'])\n"
        self.assertIsNotNone(self.verdict(code, tool="execute_code"))
        script = Path(self.tmp.name) / "hold.sh"
        script.write_text(f"systemctl --user stop {PINNED}\n")
        code = f"import subprocess\nsubprocess.run(['bash','{script}'])\n"
        self.assertIsNotNone(self.verdict(code, tool="execute_code"))

    def test_hermes_gateway_stop_argument_orders(self):
        for cmd in ("hermes gateway stop -p askeve-builder", "hermes gateway --profile askeve-builder stop",
                    "hermes -p AskEve-Builder gateway stop", "hermes --profile=askeve-builder gateway stop"):
            self.assertIsNotNone(self.verdict(cmd), cmd)

    def test_hermes_gateway_stop_all_checks_every_gateway_unit(self):
        self.g.run_cmd = lambda argv, timeout=5: (0, DROPIN + "\n") if argv[-1] == "hermes-gateway-*.service" else (1, "")
        self.assertIsNotNone(self.verdict("hermes gateway stop --all"))

    def test_target_units_are_not_given_a_service_suffix(self):
        self.verdict("systemctl --user stop some.target")
        self.assertEqual([a[-1] for a in self.shown if "DropInPaths" in a], ["some.target"])

    def test_script_that_only_mentions_the_stop_in_a_comment_or_echo_allowed(self):
        script = Path(self.tmp.name) / "mention.sh"
        script.write_text(f"# systemctl --user stop {PINNED}\necho \"never systemctl --user stop {PINNED}\"\n")
        self.assertIsNone(self.verdict(f"bash {script}"))

    def test_no_bus_falls_back_to_the_unit_dropin_directory(self):
        drop = self.home / ".config/systemd/user" / f"{PINNED}.service.d"
        drop.mkdir(parents=True)
        (drop / "50-askeve-pinned-owner.conf").write_text("[Service]\n")
        self.g.run_cmd = lambda argv, timeout=5: (1, "Failed to connect to bus")
        self.assertIn("pinned owner", self.verdict(f"systemctl --user stop {PINNED}"))
        self.assertIsNone(self.verdict(f"systemctl --user stop {PLAIN}"))

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

    def test_heredoc_body_that_stops_the_unit_is_refused_even_if_only_written(self):
        """Known false positive, kept honest: the shared segmenter reads heredoc body lines as commands."""
        cmd = f"cat > /tmp/hold.sh <<'EOF'\nsystemctl --user stop {PINNED}\nEOF"
        self.assertIsNotNone(self.verdict(cmd))

    def test_guard_off_switch_still_disables(self):
        with mock.patch.dict(os.environ, {"SERVICE_OWNER_GUARD": "off"}):
            self.assertIsNone(self.verdict(f"systemctl --user stop {PINNED}"))

    def test_lookup_failure_with_no_dropin_on_disk_is_fail_open_for_stop(self):
        self.g.run_cmd = lambda argv, timeout=5: (1, "")
        self.assertIsNone(self.verdict(f"systemctl --user stop {PINNED}"))


if __name__ == "__main__":
    unittest.main()
