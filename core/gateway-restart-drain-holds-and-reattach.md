# Gateway restart: drained work is held or re-attached, not failed

Patches: 1228 1211 1213 1231.

## Behavior

- Managed Kanban workers that are waiting on a gateway which is draining for a restart/update keep waiting, up to the owner's own `agent.restart_after_turn_timeout`, instead of giving up after `OWNER_WAIT_S` (180 s). If the owner restarted and the turn is lost, the worker exits with `KANBAN_OWNER_RESTART_EXIT_CODE` (69). (1228)
- The dispatcher books exit 69 as kind `owner_restart`: run outcome `reclaimed` (metadata `owner_restart: true`), one `owner_restart_lost` event, card back to `ready`, `consecutive_failures` unchanged, excluded from the systemic-crash tally and from `_protocol_violation_streak`. Respawn is spaced by a 120 s cooldown (`HERMES_KANBAN_OWNER_RESTART_COOLDOWN_SECONDS`). (1228)
- Cron agent jobs are held (slot returned to `next_run_at`, `last_status` and `failure_streak` untouched, `last_fire_error` stamped) while the gateway is draining, starting, absent or fenced by an update, on every tick path that has no gateway owner bound. Manual fires still fail loudly. (1211)
- A claimed wake/admission row that was admitted before a drain flip still runs once after the restart instead of being settled `completed` unrun. (1213)
- Background processes started in a Docker sandbox are re-attached after a gateway restart (real exit code and output tail delivered). If the sandbox is unreachable, the owning session gets a one-shot "watcher lost at restart, status unknown" notice. Gateway-owned host jobs that exited while unwatched, or exit after re-adoption, also wake their session. (1231)

## How it works

1228: in `kanban_worker_client`, a `GatewayUnavailableError` in `_TRANSIENT_OWNER_STATES` (incl. `restart_wait`, `update_paused`) or a `GatewayRPCError` `runtime_draining`/`retired` counts as an owner answer (`restart_wait_budget()`, `note_refusal`). Genuine silence still fails at `OWNER_WAIT_S`. After an owner restart, giving up or an "unknown" receipt raises `OwnerRestartLost` -> exit 69. The dispatcher recognises 69 both when reaping live (`waitpid`) and when the next gateway reads the log trailer (`_is_owner_restart_run`); `check_respawn_guard` applies `owner_restart_cooldown`.

1211: `scheduler_tick` runs the gate whenever no gateway owner is bound in the process (decided by owner binding, not entry point, so new ticker paths are gated by default); the gateway's own ticker skips the probe. `scheduler_gateway_gate.refuse_agent_jobs_without_gateway(due, may_start)` holds agent jobs on a live update fence (`_update_in_progress`) even over a running gateway; an interactive ticker (`may_start`) may start an absent gateway outside an update; headless never spawns. `scheduler_authority` raises `CronFireNotAdmitted` for a `GatewayUnavailableError` before admission in draining/starting/absent (probe-vs-connect race); refusals after submit stay failures. `jobs.release_unadmitted_fire` returns the slot, drops the fire claim, undoes a one-shot's dispatch bump; the ledger row says "Not admitted". `settle_unbooked_fire` is the settle hook.

1213: `execute_admission` in `gateway/session_ingress.py` rebuilt the event without `_admitted_before_drain`, so `run_inbound_hooks`' drain gate refused a row the claim's in-transaction gate had already admitted. Claimed rows are now marked admitted.

1231: `spawn_via_env` records `env.reattach_handle()` (Docker: exe + container id) and the sandbox temp dir in `processes.json`. Recovery (`_recover_sandbox_entry`) re-attaches via `tools/environments/reattach.py` (`reattach_environment`, `ReattachedDockerSandbox`: `docker exec` only, never creates or restarts a container) when the container is running and the job's wrapper files exist, then restarts the poller and gateway watcher. Otherwise `_deliver_unwatched_exit` sends the lost-watcher notice (gateway: recovered watcher; Desktop: re-adopt sink). Docker-level exec failure raises `SandboxGoneError` and marks the job lost, not exited. Entries owned by another writer (Desktop) are left for their owner.

## Code

- 1228: `hermes_cli/kanban_worker_client.py`, `kanban_db.py`, `kanban_db_dispatch.py`, `kanban_db_dispatch_dead_worker.py`, `kanban_db_dispatch_exit_code.py`, `kanban_db_dispatch_allowlist.py`
- 1211: `cron/scheduler_gateway_gate.py`, `scheduler_tick.py`, `scheduler.py`, `scheduler_authority.py`, `jobs.py`
- 1213: `gateway/session_ingress.py`
- 1231: `tools/environments/reattach.py` (new), `docker.py`, `tools/process_registry.py`, `process_registry_checkpoint.py`, `process_registry_notifications.py`, `process_registry_readopt.py`

## Tests

- 1228 `tests/hermes_cli/test_kanban_worker_owner_restart.py`: `test_a_worker_outlasts_a_restart_wait_longer_than_owner_wait`, `test_a_restart_wait_past_its_own_cap_ends_the_worker_as_an_owner_restart`, `test_an_owner_silent_with_no_draining_answer_still_fails_at_owner_wait`, `test_a_turn_the_restart_killed_reads_as_an_owner_restart`, `test_an_unknown_turn_with_no_restart_stays_a_failure`, `test_main_exits_with_the_owner_restart_code`, `test_eleven_workers_lost_to_one_restart_are_all_requeued_uncounted`, `test_a_requeued_owner_restart_respawns_after_its_cooldown`, `test_same_error_ordinary_crashes_still_trip_the_systemic_hold`, `test_a_restart_lost_exit_booked_by_the_next_gateway_is_requeued_too`, `test_a_plain_nonzero_exit_is_still_a_crash`. Also `test_kanban_worker_restart_wait.py` and `test_kanban_worker_socket_recovery.py::test_a_draining_or_starting_owner_is_waited_on_up_to_its_restart_cap` (changed/added), and the upgrade handoff E2E `tests/e2e/core/upgrade/handoff/_scenario.py` (`_release_inside_the_drain`).
- 1211 `tests/cron/test_tick_gateway_not_ready.py`: the `test_desktop_tick_*` hold cases (update, fence over ready gateway, starting/draining, absent gateway may start), `test_run_canonical_job_refused_before_admission_is_not_fired`, `test_update_refusal_racing_past_the_gate_keeps_job_due_and_streak_zero`, `test_definite_gateway_fault_before_admission_is_still_a_failure`, `test_refusal_after_admission_still_increments_the_streak`, `test_guard_*` (cron tick, owner-less in-process scheduler, desktop ticker gated; gateway-hosted skips). Manual-fire-fails-loudly is untested here.
- 1213 `tests/gateway/test_wake_admitted_before_drain_authority.py::test_authority_route_runs_a_wake_receipted_before_drain_once_after_restart` (new `claim_in_flight` case via `hold_owner_wake_claim`; the test now joins the drain like a real restart).
- 1231 `tests/tools/test_process_recovery_sandbox_reattach.py`: `test_a_alive_sandbox_process_is_reattached_and_its_completion_delivered`, `test_a_tui_owned_sandbox_completion_goes_through_the_readopt_sink`, `test_b_unreachable_sandbox_delivers_watcher_lost_notice`, `test_b_tui_owned_unreachable_sandbox_notice_goes_through_the_readopt_sink`, `test_c_gateway_owned_host_process_that_exited_unwatched_wakes_its_session`, `test_c_gateway_readopted_host_process_that_exits_later_wakes_its_session`, `test_spawn_via_env_checkpoints_the_reattach_handle_and_temp_dir`, `test_reattach_docker_returns_an_exec_handle_only_for_a_running_container`, `test_reattached_container_that_vanishes_marks_the_job_lost_not_exited`, `test_another_writer_keeps_sandbox_entries_for_their_owners_recovery`, `test_gateway_recovery_leaves_desktop_sandbox_entries_and_settles_its_own`, `test_docker_level_exec_failure_is_never_read_as_the_job_exiting`. Docker is faked (`_fake_docker`, `LocalShellSandbox`); no real container is exercised.

## Applying

Apply the complete numbered series at the merge-base with `git am -3 --empty=keep`; patches are not independent. 1228, 1211, 1213 and 1231 touch disjoint files from each other, but each assumes the earlier drain/update-fence machinery (`restart_wait`, `update_paused`, `_admitted_before_drain`, gateway ownership) from earlier patches in the series. 1213 follows the drain-admission work it fixes. See [README.md](README.md).

## Gotchas

- A continuous drain ends a Kanban worker at restart cap + `OWNER_WAIT_S`; true owner silence is still a crash after `OWNER_WAIT_S`.
- Exit 69 must not be counted as a crash anywhere (systemic tally, protocol-violation streak); the cooldown is the only back-pressure.
- Gate by owner binding, not entry point, or a new ticker silently bypasses the hold. Refusals after admission still count as failures.
- Re-attach is `docker exec` only; a vanished container is "lost", never a fake exit code. Only the Docker backend records a handle; other sandboxes get the lost-watcher notice.
- Claimed rows are in-flight work: do not drain-gate them a second time.
- Patch 1228 is a merge-adapted entry. Its source commit was written on a branch that was later merged with conflict resolutions in the Kanban dispatch files, so the raw commit does not apply on top of the mainline that precedes it in this series. The published mail patch keeps the source commit's SHA header, subject, author and date, but its diff is the net change to the files the commit touched as they stand after that merge, plus `hermes_cli/kanban_owner_unavailable.py` and its test, which the merge resolution changed. Patches 1203-1233 are ordered by first-parent merge position, not by `git log` order of the source commits.
