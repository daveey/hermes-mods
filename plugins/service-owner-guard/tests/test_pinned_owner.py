"""service-owner-guard: a pinned-owner unit is never stopped by an agent's raw command.

hermes-hub 2026-11-11: hermes-gateway-askeve-builder stopped five times by agent-run hold scripts and
recovery units. The unit has a ``50-askeve-pinned-owner.conf`` drop-in but no owner card, so the owner
guard let every raw stop through, and a stop of one coupled owner made the operator checks refuse.
Fixtures only: ``run_cmd`` is stubbed; no real service is touched.
"""
import os
import subprocess
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

    def test_binary_or_unreadable_script_does_not_raise_and_is_allowed(self):
        binary = Path(self.tmp.name) / "blob.py"
        binary.write_bytes(b"\x00\x01systemctl\xff")
        self.assertIsNone(self.verdict(f"python3 {binary}"))
        self.assertIsNone(self.verdict(f"bash {self.tmp.name}"))  # a directory
        self.assertIsNone(self.verdict("bash /nonexistent/x.sh"))

    def test_script_path_with_env_var_is_expanded(self):
        script = Path(self.tmp.name) / "hold.sh"
        script.write_text(f"systemctl --user stop {PINNED}\n")
        with mock.patch.dict(os.environ, {"HOLDDIR": self.tmp.name}):
            self.assertIsNotNone(self.verdict("bash $HOLDDIR/hold.sh"))

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

    def test_wrapper_option_values_do_not_hide_the_stop(self):
        for cmd in (f"sudo -u ubuntu systemctl --user stop {PINNED}",
                    f"sudo -u ubuntu XDG_RUNTIME_DIR=/run/user/1000 systemctl --user stop {PINNED}",
                    f"sudo -E -u ubuntu -- systemctl --user stop {PINNED}",
                    f"nice -n 10 systemctl --user stop {PINNED}", f"timeout -s KILL 10 systemctl --user stop {PINNED}",
                    f"timeout --signal=KILL 10 systemctl --user stop {PINNED}",
                    f"timeout -k 5 10 systemctl --user stop {PINNED}", f"env -u FOO systemctl --user stop {PINNED}",
                    f"env -C /tmp systemctl --user stop {PINNED}"):
            self.assertIsNotNone(self.verdict(cmd), cmd)

    def test_systemctl_option_values_do_not_hide_the_verb_or_pose_as_units(self):
        for cmd in (f"systemctl --user -s KILL kill {PINNED}", f"systemctl --user --signal KILL kill {PINNED}",
                    f"systemctl --user --kill-whom main kill {PINNED}"):
            self.assertIsNotNone(self.verdict(cmd), cmd)
        self.shown.clear()
        self.verdict(f"systemctl --user kill -s KILL {PINNED}")
        self.assertEqual([a[-1] for a in self.shown if "DropInPaths" in a], [PINNED + ".service"])

    def test_hermes_home_of_a_pinned_profile_names_its_gateway(self):
        for cmd in ("HERMES_HOME=/home/u/.hermes/profiles/askeve-builder hermes gateway stop",
                    "env HERMES_HOME=/home/u/.hermes/profiles/askeve-builder/ hermes gateway stop"):
            self.assertIsNotNone(self.verdict(cmd), cmd)
        self.assertIsNone(self.verdict("HERMES_HOME=/home/u/.hermes/profiles/askeve-builder hermes gateway status"))

    def _fake_proc(self, pid, unit, user=True, ppid=1):
        proc = Path(self.tmp.name) / "proc"
        (proc / str(pid)).mkdir(parents=True, exist_ok=True)
        slice_ = "user.slice/user-1000.slice/user@1000.service/app.slice" if user else "system.slice"
        (proc / str(pid) / "cgroup").write_text(f"0::/{slice_}/{unit}\n")
        (proc / str(pid) / "status").write_text(f"Name:\tx\nPPid:\t{ppid}\n")
        self.g.PROC = proc
        # Fixed fixture pids are not this host's processes. The owner-card lookup reads the real ps table and
        # /proc/<pid>/{cwd,environ}, so a live same-user process holding pid 4243 or 4244 (a Hermes worker with
        # HERMES_OWNER_CARD, or a cwd with OWNER.md) would refuse a kill these tests expect allowed.
        self.host_lookups = []
        self.g._lookup_pids = lambda act: self.host_lookups.append(act) or {"status": "none"}

    def test_fixed_pid_fixture_never_reads_the_host_process_table(self):
        self._fake_proc(4243, PLAIN + ".service")
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("host ps read")) as run:
            self.assertIsNone(self.verdict("kill 4243"))
        run.assert_not_called()
        self.assertEqual([a.get("pid") for a in self.host_lookups], [4243])

    def test_kill_of_a_child_the_pinned_gateway_spawned_allowed(self):
        """An agent inside the pinned gateway kills its own dev server: same cgroup, not the unit's main process."""
        self._fake_proc(4242, PINNED + ".service")
        self._fake_proc(4245, PINNED + ".service", ppid=4242)
        self.assertIsNone(self.verdict("kill 4245"))
        self.assertIsNotNone(self.verdict("kill 4245 4242"))

    def test_kill_of_a_pinned_units_process_refused(self):
        self._fake_proc(4242, PINNED + ".service")
        self._fake_proc(4243, PLAIN + ".service")
        self._fake_proc(4244, "init.scope")
        self.assertIn("pinned owner", self.verdict("kill 4242"))
        self.assertIsNotNone(self.verdict("kill -9 4243 4242"))
        self.assertIsNone(self.verdict("kill 4243"))
        self.assertIsNone(self.verdict("kill 4244"))
        self.assertIsNone(self.verdict("kill -0 4242"))  # existence probe

    def test_pkill_matching_a_pinned_units_process_refused(self):
        proc = subprocess.Popen(["sleep", "137.25"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        self._fake_proc(proc.pid, PINNED + ".service")
        self.assertIn("pinned owner", self.verdict("pkill -f 'sleep 137.25'"))
        self.assertIsNone(self.verdict("pkill -f 'sleep 999.75'"))

    def test_guard_off_switch_still_disables(self):
        with mock.patch.dict(os.environ, {"SERVICE_OWNER_GUARD": "off"}):
            self.assertIsNone(self.verdict(f"systemctl --user stop {PINNED}"))

    def test_lookup_failure_with_no_dropin_on_disk_is_fail_open_for_stop(self):
        self.g.run_cmd = lambda argv, timeout=5: (1, "")
        self.assertIsNone(self.verdict(f"systemctl --user stop {PINNED}"))


if __name__ == "__main__":
    unittest.main()
