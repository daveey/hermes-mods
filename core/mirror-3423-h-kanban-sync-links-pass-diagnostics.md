# Kanban sync links pass: lag, hold and failure diagnostics

Patches: **3361, 3381, 3407, 3412, 3414**.

## Behavior
- A nothing-due chat-wait sweep of a board with no recorded lag warning skips the lag hold (pinned by test).
- When the links pass is wedged holding its lock, the held-lock refusal warns again once the same hold has lasted `REWARN_S` (600 s) since its last warning, naming how long the pass has held the lock (at most once per interval); the next hold warns again.
- A board's links-pass failure warning carries `failed after <s>s`, and a board skipped as unchanged after a slow inputs read logs `<board> chat links unchanged, skipped in <s>s`.
- The test fake board's `spawn_failed` release is fenced and validated before its refusal, matching the real release ordering.

## How it works
- 3361 adds a test only, in the ready-wait checkpoint tests.
- 3381 adds a `_clock` seam and repeat warning in `kanban_sync_links_pass.py`, and drops an unreachable `hold[0] is self` guard.
- 3407 corrects a comment (the hold is also `None` in the acquire window, where a refusal stays quiet) and removes `raising=False` from the `_clock` patch so a rename fails loudly.
- 3412 times failed boards, moves the skip decision into `_links_unchanged`, and moves the pure `_ledger_slices` and `_links_signature` into `kanban_sync_links_inputs.py` (re-imported under old names).
- 3414 moves the `spawn_failed` `NotImplementedError` in the fake service after the lease fence and input checks on both release routes, and documents that the fake does not model `failure_limit`/`block_kind` validation.

## Code
- `hermes_cli/kanban_sync_links_pass.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_links_inputs.py`
- `tests/hermes_cli/kanban_sync_fake_board.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_ready_wait_checkpoint.py`: `test_nothing_due_sweep_of_unwarned_board_skips_lag_hold`.
- `tests/hermes_cli/test_kanban_sync_links_thread.py`: `test_a_new_hold_warns_again_after_the_first_one_ends`, `test_a_hold_past_the_threshold_warns_again_naming_its_duration` (3407 only tightens the second).
- `tests/hermes_cli/test_kanban_sync_steady_links.py`: `test_a_failed_board_logs_how_long_it_ran`, `test_a_slow_unchanged_skip_names_its_board`.
- `tests/hermes_cli/test_kanban_sync_rejections.py`: `test_fake_release_spawn_failed_is_fenced_first`, `test_fake_release_spawn_failed_validates_inputs_first`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A refusal during the brief acquire window (before the hold is recorded) stays quiet by design.
