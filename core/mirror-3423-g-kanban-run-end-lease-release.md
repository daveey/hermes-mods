# Kanban: a run the holder ends frees its Board lease while the outbox is stalled

Patches: **3324, 3352**.

## Behavior
- When the push outbox is stalled (a due pending row older than 120 s), a run the holder ends normally sends a direct lease release, so the Board's stale-lease reaper does not book it as a crash.
- Failures are logged and never fail the transition; a rolled-back end keeps the lease; crash-type ends send no neutral release; a draining outbox leaves the release to the row push.

## How it works
- 3324 adds `kanban_run_end_lease.note`, called inside the transaction by schedule, block, review handoff, timed/escalation parks, complete, request_changes, continue, archive and descendant reopen; `write_txn` sends the release after COMMIT.
- 3352 table-drives the test across all holder run-end transitions and documents the stalled window and `_PENDING` keying.

## Code
- `hermes_cli/kanban_continuation.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_archive.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_run_end_lease.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_run_end_lease.py`: `test_schedule_releases_the_run_lease_while_the_outbox_is_stalled`, `test_block_complete_review_and_continue_release_the_run_lease`, `test_a_failed_release_leaves_the_park_intact`, `test_a_draining_outbox_leaves_the_release_to_the_row_push`, `test_a_crash_end_does_not_send_a_neutral_release`, `test_a_rolled_back_end_keeps_the_lease`, `test_every_holder_run_end_releases_the_run_lease`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Best effort only: if the release fails the lease expires server-side as before.
