# Regression fixture isolation and deterministic cleanup

Mirror entries: [2254](patches/2254-daveey-fix-tests-discovery-refusing-load-tests-in-upstream-conformance-packages.patch), [2262](patches/2262-test-update-recognize-dead-fetch-process-state-daveey-1084.patch), [2273](patches/2273-daveey-Join-metrics-HTTP-fixture-threads-before-interpreter-exit.patch), [2284](patches/2284-test-kanban-preserve-commit-status-landing-proof-offline-daveey.patch), [2285](patches/2285-fix-tests-close-kept-alive-metrics-client-and-join-HTTP-fixtures-daveey.patch), [2326](patches/2326-daveey-Reap-the-signal-test-pnpm-shim-sleeper-before-exit.patch), [2328](patches/2328-daveey-Isolate-baseline-suite-fixtures-from-host-metadata-and-readiness.patch).

## Behavior

- Conformance test packages refuse bare unittest discovery, preserving the test runner's isolation contract without changing plugin registration.
- Fetch-custody assertions recognize Linux X (dead) and Z (zombie) states; only a disappeared proc entry counts as an absent process. Permission and other observation errors still surface.
- HTTP fixtures close kept-alive clients and join owned server/handler threads; the signal-test pnpm shim owns and reaps its sleeper before exiting.
- Offline landing-proof coverage preserves commit-status evidence after remote evidence expires. Baseline fixtures isolate host metadata, readiness, browser cleanup and optional SDK availability.

## How it works

2254 adds discovery-refusing load_tests hooks, including after the fixture's register function. 2262 factors process-state observation into _fetch_exited, with explicit missing-file handling. 2273 and 2285 repair teardown ownership rather than masking unexpected HTTP errors. 2326 publishes readiness after starting the sleeper and waits for it in both signal traps. 2328 supplies deterministic local fixtures instead of consulting the runner's installed metadata/readiness. 2284 is a regression test, not a change to the landing algorithm.

## Code

This is test-only; changed source test paths are listed below.

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/plugins/conformance/__init__.py`
- `tests/plugins/conformance/fixtures/conformance_surface/__init__.py`
- `tests/hermes_cli/test_update_custody.py`
- `tests/hermes_cli/test_update_custody_observation.py` — `test_fetch_exit_observation`, `test_fetch_exit_observation_read_errors`.
- `tests/hermes_cli/test_kanban_sync_metrics_budget.py`
- `tests/hermes_cli/test_kanban_sync_metrics_teardown.py` — `test_fixture_joins_disconnected_handlers`, `test_fixture_subprocess_exits_cleanly`, `test_close_waits_for_owned_thread_completion`, `test_handler_only_suppresses_expected_disconnects`.
- `tests/hermes_cli/test_kanban_pr_merge_time.py` — `test_commit_status_landing_proof_survives_github_expiry`.
- `tests/agent/test_aux_progress_streaming.py`
- `tests/agent/test_bedrock_no_sdk_retry.py`
- `tests/agent/test_model_metadata_http.py`
- `tests-js/node-deps.test.mjs`
- `tests/gateway/test_readiness.py` — `test_collect_runtime_readiness_reports_healthy_local_runtime`.
- `tests/hermes_cli/test_banner.py`
- `tests/hermes_cli/test_banner_skills_width.py`
- `tests/hermes_cli/test_doctor_pm_store_probe.py`
- `tests/hermes_cli/test_pty_bridge.py`
- `tests/hermes_cli/test_web_host_terminal_persistence.py`
- `tests/tools/test_browser_launcher.py` — `test_a_throwaway_profile_left_by_a_process_that_died_mid_launch_is_reclaimed`.
- `tests/tools/test_fal_sdk.py` — `test_sdk_submit_preserves_wire_contract`, `test_keyed_errors_survive_without_duplicate_submission`, `test_missing_sdk_surface_is_actionable`, `test_optional_sdk_surface_refused_before_http` (additional cases in this file).
- `tests/test_unittest_safety.py` — `test_every_test_package_refuses_discovery`.

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

These are test-infrastructure changes, not evidence that a running gateway's threads, fetch processes or credentials were inspected. Use scripts/run_tests.sh in the source checkout, not unittest discovery. The many unrelated features quoted in 2262's commit message are not its diff: this entry changes only two custody test files.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
