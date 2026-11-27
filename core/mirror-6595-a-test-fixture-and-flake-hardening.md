# Test fixture and flake hardening with CI push diagnostics

Patches: 5956, 5957, 5973, 6005, 6008, 6009, 6017, 6020, 6021, 6044, 6093, 6140, 6160.

## Behavior

- Fixture-only fixes: build the Kanban board before the first web_server import; scope an injected config read failure to the MoA save handler; reopen contended native dispatcher handles in a lifecycle fixture; model a periodic hub sampler in an admission-echo test; wait for composer clear in a TUI attachment test; drain annotation refresh before test cache reader teardown; settle bootstrap WAL before a cursor-only cache check; wait for legacy discovery services to warm; assert prune-lifecycle RED and writer custody for checkpoint housekeeping.
- CI script changes: upstream gate pushes get a longer deadline than the generic git timeout and wrap a timeout with stage/ref/limit diagnostics; local push errors are kept ahead of remote hints. A late-exit stack capture for authenticated-session tests was added and then reverted in the same series, so its net effect is nil.

## How it works

Each entry edits tests, test fixtures, or scripts/ci / scripts/run_tests_launch helpers. The test-only entries change no runtime behavior; the one production-module edit (`hermes_cli/kanban_session_activity.py`) is the test-reset helper `_reset_cache_for_tests`, which now takes the annotation-refresh lock for the whole reset before closing cached readers (test-only entry point, untested outside the listed regression). The one script behavior change is the push deadline and error ordering in the upstream sync gate.

## Code

Paths touched by these entries (non-test):

- `hermes_cli/kanban_session_activity.py`
- `scripts/ci/upstream_sync.py`
- `scripts/run_tests_launch.py`

## Tests

Entry-path behavior above is source-reviewed but **untested in this review**. The files below identify source regression targets, not proof that this review exercised those entrypoints.

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `tests/gateway/fixtures/kanban_owner_probe.py`
- `tests/gateway/test_checkpoint_prune_housekeeping.py`
- `tests/gateway/test_command_discovery.py`
- `tests/gateway/test_session_kanban.py`
- `tests/hermes_cli/test_kanban_annotation_reset_custody.py`
- `tests/hermes_cli/test_kanban_hub_admission_grant_echo.py`
- `tests/hermes_cli/test_kanban_session_activity.py`
- `tests/hermes_cli/test_kanban_sync_links_replica_revision.py`
- `tests/hermes_cli/test_moa_preset_deletion.py`
- `tests/scripts/ci/test_upstream_sync.py`
- `tests/scripts/test_pytest_exit_diagnostics.py`
- `ui-tui/src/__tests__/slashAttachmentRenderer.test.tsx`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- The added-then-reverted diagnostic pair nets out; applying only one of the two would change test-runner behavior.
- Several fixes address timing races, so they reduce flake rates rather than prove absence of flakes.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: these are the host repository's own tests and CI helper scripts.
