# CI lanes and test deflakes, 1018-1037

Patches:

- `patches/1018-fix-tests-deflake-hosted-room-gateway-restart-test-5.patch`
- `patches/1019-ci-flaky_kanban-Deflake-card-is-done-when-the-PR-is-.patch`
- `patches/1022-test-metrics-synchronize-schema-writer-contention-wi.patch`
- `patches/1027-test-update-publish-stand-in-backend-port-atomically.patch`
- `patches/1030-test-tools-exit-cleanup-test-waits-for-the-foregroun.patch`
- `patches/1031-test-state-control-lease-expiry-clock-in-writer-revi.patch`
- `patches/1037-test-synchronize-relay-report-updates-with-a-control.patch`

Deflake fixes (test-only) plus one CI-card wording change from the 1018-1037 range.

## Behavior

- 1018: hosted-room gateway restart test waits longer than 5 s stop / 3 s wait, which missed under CPU throttling.
- 1019: `scripts/ci/flaky_kanban.py` card body now says "Done when the PR is merged to main and the test passes on main's next CI run" and that opening a PR is not done (stay on the card with `kanban_block`/`kanban_continue`, merge once required checks are green, confirm on main's next run, never `kanban_complete` while the PR is open).
- 1022: metrics schema writer contention is synchronised without a sleep.
- 1027: the update stale-dashboard review test publishes its stand-in backend port atomically.
- 1030: the exit-cleanup test waits for the foreground command to be registered (`_live_foreground`) before cleaning up; the child is visible in /proc before `execute()` publishes it, so on a starved runner cleanup found nothing to kill (reproduced by delaying publication 2 s).
- 1031: the writer-revival regression controls the lease expiry clock.
- 1037: relay report updates are synchronised with a controlled cap clock.

## How it works

Only test code changes, except 1019 which changes the text the flaky-test card generator writes.

## Code

`tests/gateway/test_hosted_room_gateway_lifecycle.py`, `scripts/ci/flaky_kanban.py`, `tests/ci/test_flaky_kanban.py`, `tests/hermes_cli/test_relay_shared_metrics.py`, `tests/hermes_cli/test_update_stale_dashboard_review.py`, `tests/tools/test_local_interrupt_cleanup.py`, `tests/hermes_state/test_session_turn_lease.py`, `tests/tui_gateway/test_bot_relay_methods.py`.

## Tests

These patches are the tests. 1019 adds `test_card_body_requires_merge_before_done` (asserts the done-when sentence and the do-not-complete line in the generated body). Tests were added or changed by these commits; they were not re-run for this mirror.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

None.
