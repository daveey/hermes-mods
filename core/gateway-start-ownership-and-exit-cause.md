# Gateway exit causes, start-race refusals and fallback-dir cleanup

Patches: 1158 1168 1174 1175 1192 1198 1200 1202.

## Behavior

- Every `False` return of `start_gateway()` now names its cause. `gateway.exit_nonzero` rows in `gateway-exit-diag.log` carry `cause` (and `exit_reason`, max 500 chars), or `cause: "unrecorded"` when an exit site noted none. Exit codes are unchanged. (1158)
- A start that loses the profile-reservation race to a live gateway of the same home is `gateway.start_refused` (`reason: profile_reservation`, `owner_pid`), not a crash. Exit code stays 1. A non-gateway or maintenance (`purpose`) holder keeps `exit_nonzero` with `cause=profile_reservation_conflict`. (1175)
- A gateway whose home path is too long for a Unix socket binds under `hermes-gw-<hash>` in the shared temp dir. A clean stop now removes that dir, only if this server created it, it is still the same inode and it is empty. (1168)
- The next fallback bind reaps stale sibling `hermes-gw-*` dirs left by SIGKILLed gateways: real 0700 dir, own uid, older than 10 min, holding only a `control.sock` that refuses connect, never its own dir, never a symlink, at most 200 candidates per call. (1192)
- A one-off launchd gateway job (`start_gateway_job`) now runs under the signed Hermes Python.app when one matches the command or `sys.executable`, so macOS privacy prompts are charged to it, not to `osascript`; no signed app keeps the osascript wrapper. (1202)
- 1174, 1198, 1200 change tests only (see below).

## How it works

`gateway/run_exit_reason.py` adds `note_exit_cause(cause, reason)` (first note per process wins), `last_exit_cause()` and `reset_exit_cause()`; `nonzero_exit_record()` in `hermes_cli/gateway_exit_diag.py` reads it. Notes were added at the replace, host-decision, multiplex, reservation, PID-claim, runner-start and runner-failure sites. 1175 adds `live_gateway_lock_holder(home)` in `gateway/runtime_ownership.py` and `_reservation_conflict_gateway_holder` in `run_bootstrap.py`; a live holder calls `note_start_refused`. 1168 records the created dir's `(st_dev, st_ino)` in `GatewayControlServer._bind_posix`. 1192 adds `_reap_stale_fallback_dirs` and `_socket_refuses`, unlinking only the probed socket inode. 1202 adds `responsible_app(command)` (stdlib only, usable from the bare restart watcher) and passes it as `responsible=` to `launchd_program_arguments`.

## Code

- `gateway/run_exit_reason.py`, `gateway/run_bootstrap.py`, `gateway/run.py`, `gateway/run_shutdown.py`, `hermes_cli/gateway_exit_diag.py` (1158, 1175)
- `gateway/runtime_ownership.py` (1175)
- `gateway/control_socket.py` (1168, 1192)
- `hermes_cli/gateway_launchd_runtime.py` (1202)

## Tests

- 1158 `tests/hermes_cli/test_gateway_exit_signal.py`: `test_failure_verdict_records_cause_and_exit_reason`, `test_false_exit_with_no_cause_note_says_unrecorded`, `test_early_start_gateway_false_records_its_cause`, and the AST guard `test_every_false_exit_path_notes_a_cause` over ten exit-path functions. Most other noted sites have no dedicated test.
- 1175 same file: `test_reservation_conflict_with_live_gateway_holder_is_start_refused`, `test_reservation_conflict_with_non_gateway_holder_stays_exit_nonzero` (real flock held by a child process).
- 1168 `tests/gateway/test_control_socket.py`: `test_stop_removes_the_fallback_dir_it_created`, `test_stop_keeps_a_fallback_dir_it_did_not_create`, `test_stop_keeps_a_fallback_dir_replaced_or_refilled_after_start`; `tests/gateway/test_runtime_token_identity.py` asserts the dir is gone after SIGINT.
- 1192 same file: `test_reaper_removes_only_dead_aged_private_dirs`, `test_reaper_scan_is_bounded`, `test_fallback_bind_reaps_a_sigkilled_siblings_dir`. Fixtures `stop_child` (SIGTERM, 10 s, then kill) and `TMPDIR` passthrough are used by the live-gateway fixtures.
- 1174 deflakes `tests/gateway/test_loop_liveness_watchdog.py` (the fake exit disarms the watchdog itself), the `operator_cross_surface_peer.py` fixture (polls replay until both `message.complete` events), and `tests/tools/test_finite_delegation_outcomes.py` (teardown wait 5 s to 60 s).
- 1198 `tests/gateway/test_replace_ownership_guard.py`: stalled-writer sleep 5 s to 1 s, below the 5 s production flush wait.
- 1200 `tests/gateway/fixtures/replay_gap_owner.py`: waits until the viewer socket is blocked before publishing, so the 256-frame fanout cap overflows deterministically; re-attach wait 5 s to 30 s.
- 1202 `tests/hermes_cli/test_gateway_launchd_runtime.py`: `test_job_is_owned_by_the_signed_python_app_when_the_command_runs_it`, `test_job_is_owned_by_the_signed_twin_of_the_store_python`, `test_job_keeps_the_osascript_wrapper_without_a_signed_app` (plist `ProgramArguments` with `launchctl` stubbed). The live launchd test is macOS-only and not run on Linux.

## Applying

Apply the complete numbered series at the merge-base with `git am -3 --empty=keep`; entries are not independent (1175 and 1192 build on 1158 and 1168, 1202 on the signed-wrapper `responsible=` support). See [README.md](README.md).

## Gotchas

- First note wins: an inner site's cause is not overwritten by the caller's generic note.
- The reaper skips anything younger than 10 min, with extra files, or whose socket does not return ECONNREFUSED (a timeout reads as maybe alive).
- Reservation-conflict holder lookup is best-effort; any error leaves `exit_nonzero`.
- `responsible_app` can only pick a bundle that is installed; hosts without Hermes Python.app behave as before.
- The 1198/1200 deflakes change timing in tests, not product code.

## Plugin?

No, core change.
