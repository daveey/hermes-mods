# Test-only deflakes and regression coverage, patches 1254–1277

Patches: 1256 1257 1260 1261 1263 1267 1268 1271 1275 1276 1277

- `patches/1256-test-agent-isolate-startup-review-cancellation-from-.patch`
- `patches/1257-test-wait-for-exec-d-argv-before-asserting-SSH-runti.patch`
- `patches/1260-test-kanban-goal-evidence-preview-truncated-at-white.patch`
- `patches/1261-test-tui-tolerate-worker-reaping-between-exit-probes.patch`
- `patches/1263-test-gateway-give-runtime-status-flush-a-generous-ti.patch`
- `patches/1267-tests-close-the-read-only-state.db-probe-connections.patch`
- `patches/1268-test-kanban-wait-for-admitted-dispatcher-launch-dave.patch`
- `patches/1271-test-cron-allocate-fire-streak-slots-from-a-stable-a.patch`
- `patches/1275-test-anchor-docker-handback-ordering-on-this-teardow.patch`
- `patches/1276-test-whatsapp-pin-_wait_pids_exit-in-kill-listeners-.patch`
- `patches/1277-test-update-tolerate-procfs-reaping-during-custody-c.patch`

All eleven patches change only tests or test fixtures. They do not modify production behavior.

## Behavior

- 1256: startup-review cancellation coverage no longer depends on short scheduler deadlines or globally replacing threads needed by plugin discovery.
- 1257: the SSH runtime ownership fixture waits for its child's exec'd nonce-bearing argv before checking ownership.
- 1260: goal-release coverage crosses whitespace at the evidence preview boundary with intact versus tampered run summaries.
- 1261: a test's worker-exit probe accepts reaping between observations but does not hide access denial.
- 1263: asynchronous runtime-status flush assertions get explicit 60-second hang bounds.
- 1267: read-only database probes explicitly close their SQLite connections.
- 1268: the real-dispatcher test fixture retries admission-deferred, still-unclaimed launches, with a new contended-admission case.
- 1271: cron test occurrences use a fixed creation-time anchor so elapsed seconds cannot accidentally reuse a slot.
- 1275: Docker handback ordering assertions anchor on this teardown's own walk/strip calls rather than the first process-wide stop/remove.
- 1276: the WhatsApp listener-kill test stubs the exit wait for its fake PID.
- 1277: the custody test's liveness helper treats a disappearing procfs stat file as an exited process.

## How it works

1256 scopes the captured `Thread` replacement to review spawn and sets the test cancellation timeout, event wait and joins to 30 seconds. 1257 polls argv on a monotonic 60-second deadline, checking the child remains alive.

1260 constructs 399 characters followed by space, tab or em-space and a remainder, completes through the goal gate with a stubbed judge, optionally changes the run summary, then observes the owner receipt.

1261 wraps both `is_running` and `status` in a `NoSuchProcess` handler. 1263 passes `timeout=60.0` at status flush sites without changing production defaults. 1267 uses `contextlib.closing`: a SQLite connection's own context manager only ends its transaction.

1268 retries only while the task is ready, has no current run and has zero run rows; an already-failed launch is not hidden. Its contended mode first holds the admission lock, then releases it and waits up to 60 seconds for claim/spawn.

1271 subtracts unique counter values from `created_at` rather than moving wall time. 1275 picks the nearest stop preceding the walk and first remove after the strip. 1276 patches `_wait_pids_exit` to return no remaining PIDs. 1277 catches only `FileNotFoundError` around the stat read.

## Code

Only test paths:

- `tests/agent/test_background_review.py`
- `tests/hermes_cli/test_windows_ssh_runtime_ownership.py`
- `tests/hermes_cli/test_kanban_goal_receipt.py`
- `tests/tui_gateway/test_slash_worker_owned.py`
- `tests/gateway/test_startup_failed_platform_reports_degraded.py`
- `tests/gateway/fixtures/local_recovery_probe.py`
- `tests/hermes_cli/test_runtime_maintenance_exclusion.py`
- `tests/gateway/fixtures/kanban_owner_probe.py`
- `tests/gateway/test_session_kanban.py`
- `tests/cron/test_gated_tick_clears_fire_streak.py`
- `tests/tools/test_docker_ownership_handback.py`
- `tests/gateway/test_whatsapp_connect.py`
- `tests/hermes_cli/test_update_custody_tree.py`

## Tests

These are the tests added or changed, not executions performed for this documentation update:

- 1256: `test_live_turn_cancels_review_during_startup_before_provider` retains startup cancellation coverage with scoped thread capture and larger bounds.
- 1257: `test_isolated_bootstrap_is_owned_without_accepting_foreign_source` gains argv readiness; it is marked POSIX despite the Windows-runtime module name.
- 1260: `test_goal_release_normalizes_truncated_preview_whitespace` covers three whitespace tails crossed with tampering; only intact evidence verifies. The judge is mocked, not a live model call.
- 1261: `test_worker_exit_probe_handles_reaping_between_observations` covers non-running, sleeping, zombie and vanished workers. `test_worker_exit_probe_does_not_mistake_access_denied_for_exit` preserves the exception. The legacy-owner death test uses the helper.
- 1263: existing degraded/draining/platform-heal cases keep their assertions and receive longer flush bounds; no new case.
- 1267: existing local-recovery and `test_restore_refuses_live_authority_without_changing_data` probes close their read connections; no new case.
- 1268: `test_real_dispatcher_lifecycle` gains POSIX `timeout_contended`, checking initial deferral, timed-out outcome, one failure, PID preservation and managed-process death. Its transport/model are fixture-owned, not a deployed gateway/model service.
- 1271: `test_real_success_resets_the_kind` crosses elapsed times 0, 1 and 2 seconds while retaining streak-reset and next-fire assertions.
- 1275: `test_root_container_teardown_hands_every_sandbox_mount_back_before_removal` retains stop/walk/chown/strip/remove ordering with local call anchors. This is call-recording coverage, not a live Docker run.
- 1276: the listener-kill case in `TestKillPortProcess` still checks one SIGTERM and now asserts the fake PID is passed to the exit-wait helper.
- 1277: Linux-only `test_alive_tolerates_a_process_reaped_before_its_stat_read` starts a real child, confirms liveness, kills/reaps it immediately before the stat read and asserts it is no longer alive.

None were re-run for this mirror documentation task; live provider, UI and service-manager paths beyond these fixtures are untested here.

## Applying

Apply the complete numbered series at its documented merge-base with `git am -3 --empty=keep`; see `README.md`. These tests depend on earlier production fixes and shared fixtures, so are not standalone feature implementations.

## Gotchas

- Longer deadlines are hang bounds, not sleeps or new production cancellation policies.
- SQLite `with connect()` does not close a connection; use `closing` when the fixture must release its handle.
- Readiness retries must stop once any claim/run exists, otherwise they could conceal a lost launch.
- The liveness changes here are test helpers; they do not change production process detection.
