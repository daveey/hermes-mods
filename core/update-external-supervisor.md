# `hermes update` hands launchd-run gateways and dashboards back to launchd

Patches:

- `patches/0082-fix-update-hand-an-external-supervisor-stale-survivo.patch`: update. A stale gateway that runs
  with `--external-supervisor` and is found in the verify phase is handed back to its supervisor. It is no longer
  counted as a manual gateway that needs a restart.
- `patches/0083-fix-update-see-external-supervisor-inside-a-runpy-bo.patch`: update. The `--external-supervisor`
  flag is found even when the gateway was started through the Hermes Python.app `runpy` bootstrap, where the flag
  sits inside the `-c` source.
- `patches/0084-fix-update-hand-a-wrapper-script-launchd-dashboard-b.patch`: update. A dashboard or serve backend
  started by a launchd job whose plist runs a wrapper script is kickstarted through that job instead of being
  respawned from its argv. Backend argv is also read exactly on macOS.

What you see: you run a gateway or a `hermes dashboard`/`hermes serve` backend from your own launchd agent (custom
label, `KeepAlive`). After `hermes update`, launchd brings it back on the new code and the update says so. Before
these patches, the update also started its own copy. That copy raced launchd's respawn. The gateway could end up
running outside launchd, the update printed "Restart manually: hermes gateway run" for a gateway that was already
back, and the receipt kept a `gateway_restart` follow-up on every update. A wrapper-launched dashboard was booked
`failed` because the detached copy died with a `SyntaxError`.

Related: [fork-updates-and-relaunch.md](fork-updates-and-relaunch.md) covers the rest of the unattended-update
setup (fork merge, Desktop reopen, `serve --stop`).

## Behavior

- **Stale gateway survivor with `--external-supervisor` (0082).** The update's restart phase can miss a gateway.
  The verify phase then sends SIGUSR1 to the proven-stale survivor, which drains and exits 75. If that gateway
  declares an external supervisor, the update now prints `✓ <profile> (PID N): handed back to its external
  supervisor` and records the profile under `externally_supervised_profiles`. It is not added to the manual list,
  so there is no "Restart manually" line and no owed restart for a later CLI start to retry. Before, each update
  restarted such a gateway twice.
- **Restart plan (0082).** A gateway whose control-socket `identify` reports supervisor `external` is planned as
  `external-supervisor`: "SIGUSR1 drain, exits 75 back to its own supervisor (custom launchd/systemd), which
  relaunches it". Before, it had no mapping and fell through to a manual restart.
- **Runpy bootstrap (0083).** A gateway launched as `python -I -c "…; sys.argv = [… 'gateway', 'run',
  '--external-supervisor']; runpy.run_path(…)"` from an interpreter path with a space in it (`Hermes
  Python.app`) is now recognized as externally supervised. The update returns `external-supervisor` for it and
  arms no detached `gateway run --replace` watcher. Before, the watcher raced launchd: two `gateway.start` lines,
  the launchd child exited, and the survivor ran outside launchd.
- **An arbitrary `-c` program that merely carries the text `--external-supervisor` in an argument is not a
  declaration.** Only the Hermes inline bootstrap form is unwrapped.
- **Wrapper-script launchd dashboard (0084).** A launchd job whose `ProgramArguments` is a wrapper script that
  `exec`s `hermes dashboard …` (or runs it as a child) is now matched to the backend. The update stops the
  backend and brings it back with `launchctl kickstart` on that job. It does not replay the argv.
- **Terminal-launched backends are unchanged (0084).** A backend started from a terminal carries the terminal
  app's `XPC_SERVICE_NAME`, whose live pid is not the backend or one of its ancestors. It is still respawned from
  its argv.
- **Exact argv on macOS (0084).** The respawn argv of a backend is now read with psutil first. Before, `ps -o
  command=` plus `shlex` split the `Hermes Python.app` path and the `-c` source into separate words, so a respawn
  of a runpy-bootstrapped backend died with a `SyntaxError`.

## How it works

- **0082, `hermes_cli/update_cmd_stale_survivors.py`:** `signal_stale_fleet_survivors` calls
  `gateway_supervised_restart.gateway_declares_external_supervisor(pid)` before sending the signal, because the
  answer comes from the live process. It only asks for pids that are not in `_get_service_pids`. After a
  successful `_drain_or_signal_gateway_for_update`, an external pid goes to `restart.externally_supervised_profiles`
  instead of the `manual` list. The manual list is what fills `stopped_unmapped_pids` and the "Restart manually"
  hint.
- **0082, `hermes_cli/update_inventory.py`:** `_RESTART_MECHANISMS` gains `"external": "external-supervisor"`, and
  `_MECHANISM_DESCRIPTIONS` gains the text for `external-supervisor`. `external` is the supervisor that a gateway
  started with `gateway run --external-supervisor` reports through the control-socket `identify`.
- **0083, `hermes_cli/gateway.py`:**
  - `_capture_gateway_argv` checks the argv with `looks_like_gateway_command_line(argv_to_command_line(argv))`
    instead of `" ".join(argv)`. The plain join of a spaced interpreter path failed the matcher, so the function
    returned `None` and the flag was never seen.
  - New `argv_declares_external_supervisor(argv)`. It returns True when `--external-supervisor` is its own argv
    element. Otherwise, if `command_line_runs_inline_source` (from `gateway/status_inline_source.py`) says the argv
    runs inline source, it splits the elements after `argv[0]` on spaces, passes them to `inline_bootstrap_argv`,
    and looks for the flag in the result. `argv[0]` stays one token because the path can contain spaces. Any
    exception returns False.
  - `_prepare_profile_gateway_update_restart` uses the new helper.
- **0083, other callers:** `gateway_declares_external_supervisor` in `hermes_cli/gateway_supervised_restart.py` and
  `_supervisor_markers` in `hermes_cli/update_cmd_posix_pause.py` both switch from `"--external-supervisor" in
  argv` to `argv_declares_external_supervisor`.
- **0084, `hermes_cli/main_dashboard.py`:**
  - New `_launchd_job_from_launch_env(pid, ancestors)`, macOS only. It reads `XPC_SERVICE_NAME` from the process
    environment (`_pid_environ`). An empty name or `0` means no job. For `gui/<uid>` and then `user/<uid>`, it calls
    `_launchd_print_service_pid` (`launchctl print`). The first domain where the job is loaded decides: the job
    counts only if its live pid equals `pid` or is one of `ancestors`. It returns `(domain, label, live_pid)`. So a
    stale or inherited name never claims a process.
  - `_dashboard_cmdline_for_pid` tries `psutil.Process(pid).cmdline()` before falling back to `ps`. `ps` stays for
    processes psutil can't read (for example, another user's).
- **0084, `hermes_cli/dashboard_procs.py`:** in `_kill_stale_dashboard_processes`, `_launchd_owner` first tries the
  existing plist match (`_launchd_job_owning_backend`, only when `_loaded_launchd_backend_jobs` found any). If that
  finds nothing, it falls back to `_launchd_job_from_launch_env`. The launchd branches no longer require
  `launchd_jobs` to be non-empty. They run on any non-Windows platform, since a wrapper job never appears in that
  list. A matched pid lands in `pid_launchd` and is brought back by `_restart_launchd_job` (`launchctl kickstart`
  without `-k`).

## Tests

- `tests/hermes_cli/test_update_stale_survivor_external_supervisor.py` (new, 0082). Uses a real child process that
  exits 75 on SIGUSR1.
  - `test_externally_supervised_survivor_is_handed_back_not_owed`: no `stopped_unmapped_pids`, profile listed as
    externally supervised, no "Restart manually".
  - `test_unsupervised_survivor_is_still_owed_a_manual_restart`: control.
  - `test_self_declared_external_supervisor_plans_a_hand_back_not_manual`: `_restart_mechanism("external", …)`.
- `tests/hermes_cli/test_update_runpy_external_supervisor.py` (new, 0083).
  - `test_capture_gateway_argv_accepts_runpy_bootstrap_under_spaced_interpreter`
  - `test_argv_declares_external_supervisor`: bootstrap with and without the flag, plain `-m hermes_cli.main`
    argv, `None`, and an arbitrary `-c` program carrying the flag text (must be False).
  - `test_update_hands_runpy_external_supervisor_gateway_back_without_watcher`
  - `test_update_still_arms_watcher_for_unsupervised_runpy_gateway`: control.
  - `test_declares_external_supervisor_reads_runpy_argv_when_identify_is_silent`
- `tests/hermes_cli/test_update_dashboard_launchd_wrapper.py` (new, 0084, all marked `platforms("macos")`).
  - `test_cmdline_capture_keeps_exact_argv_of_spaced_runpy_backend`: psutil argv, `ps` never called.
  - `test_launchd_job_from_launch_env`: live pid match, ancestor match, other live pid, job not loaded, name `0`,
    no name.
  - `test_wrapper_script_launchd_dashboard_is_kickstarted_not_respawned`
  - `test_terminal_launched_dashboard_still_respawns_from_argv`: control.
- `tests/hermes_cli/test_update_stale_dashboard.py` (changed, 0084): the `_no_real_launchd_jobs` fixture also
  stubs `_launchd_job_from_launch_env`, and `TestCmdlineCapture` hides psutil so the `ps` path is still tested.

```sh
scripts/run_tests.sh tests/hermes_cli/test_update_stale_survivor_external_supervisor.py \
  tests/hermes_cli/test_update_runpy_external_supervisor.py \
  tests/hermes_cli/test_update_dashboard_launchd_wrapper.py tests/hermes_cli/test_update_stale_dashboard.py
```

These were not re-run while writing this doc.

## Applying

The three patches touch separate code paths and can be applied alone. 0082 handles gateways whose flag is a
plain argv element or whose `identify` reports `external`. A gateway started through the runpy bootstrap also
needs 0083. 0084 is independent of both. They build on the update fleet code already in the fork
(`update_cmd_stale_survivors.py`, `update_cmd_posix_pause.py`, `_launchd_job_owning_backend`), so apply them in
order with `git am -3 --empty=keep` on a tree that has that code.

## Gotchas

- **The gateway must declare its supervisor.** Start it with `hermes gateway run --external-supervisor` in your
  launchd agent. Without the flag (and without a non-`manual` `identify` answer), the update still treats it as
  manual and arms a detached restart.
- **The supervisor must relaunch on exit 75.** The gateway leaves on SIGUSR1 with exit code 75. Use `KeepAlive`
  (launchd) or `Restart=` (systemd) so it comes back. Nothing else restarts it.
- **0084's job lookup is macOS only.** On Linux the fallback returns None, and wrapper-launched backends there
  are matched only as before.
- **The job must still own the process.** `launchctl print` must show the job loaded with the backend, or one of
  its ancestors, as the live pid. A wrapper that forks the backend and then exits does not match.
- **A wedged `launchctl`** (timeout in `_launchd_print_service_pid`) makes `_launchd_job_from_launch_env` return
  None, so that backend falls back to the argv respawn.

## Plugin?

No. The update's restart planning, stale-survivor handling and dashboard respawn all run inside `hermes_cli`
during `hermes update`, and there are no plugin hooks there.
