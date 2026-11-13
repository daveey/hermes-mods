# Test hardening: review requeue deflake and single cancelled completion

Patches: **3653, 3658**.

## Behavior
- Test-only. No runtime behavior changes.
- 3653 removes a CI flake in the background-review wind-down tests by joining the review thread before asserting its requeue log line.
- 3658 pins that a stopped queue head's cancelled completion is published exactly once, so a double settle is caught.

## How it works
- 3653 captures the spawned review thread, joins it (30 s bound) and sets caplog level for the whole test; the sibling drop test gets the same join.
- 3658 attaches an observer on the live session event stream and asserts exactly one cancelled completion for the head (Stop during the requeue write; failed cancel committed then raised, before and after the hold) and none when the cancel fails before commit.

## Code
- No source files; tests only.

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_background_review_update_pause.py`: `test_gateway_review_winds_down_releases_its_reservation_and_is_requeued`, `test_review_out_of_requeues_is_dropped_with_a_log_line`.
- `tests/gateway/test_update_requeue_stop_race.py`: `test_stop_latched_during_the_requeue_write_cancels_the_head_and_clears...` and `test_a_failed_cancel_of_the_stopped_head_runs_it_once_after_the_hold_o...` (names truncated in the patch hunk headers).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
