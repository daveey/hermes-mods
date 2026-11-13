# Kanban sync: stale-replica alarm

Patches: **3649–3650**.

## Behavior
- When a synced board's replica stamp (`pulled_at`) does not advance for 1200 s of monotonic (awake) time, one incident card is filed; sleep does not count.
- Further reports comment at most hourly on the open card; a new card opens once it is done. The stamp advancing ends the incident (one INFO line); one INFO telemetry line per board per hour.
- Covers slow rounds, failing rounds, a dead loop, and a missing or unreadable replica. Detached or unsynced boards never file; an exception in the check never reaches the dispatcher tick.
- The card names host and gateway, board, replica stamp, awake minutes, last round outcome, the loop's recorded stop, pending outbox ops, the core sha, and local cards waiting on remote parents with the parent's Board status (local mirror when unreachable).
- The replica stamp is read through the lock guard (3650).

## How it works
- 3649 adds `kanban_sync_replica_age.py`, run from `kanban_sync_outbox_age.check` on the dispatcher tick, outside the sync loop. `after_round` feeds each round's outcome. `report()` now takes key/author/title/body.
- 3650 changes the stamp read to go through the lock guard.

## Code
- `hermes_cli/kanban_sync_replica_age.py`
- `hermes_cli/kanban_sync_outbox_age.py`
- `tests/hermes_cli/test_kanban_sync_replica_age.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_sync_replica_age.py`: `test_rounds_ending_behind_for_21_awake_minutes_file_exactly_one_card`, `test_rounds_failing_with_urlerror_file_one_card_naming_the_last_error`, `test_a_loop_that_runs_no_rounds_files_one_card`, `test_a_synced_board_without_a_readable_replica_files_one_card`, `test_an_unreadable_replica_counts_as_missing`, `test_sleep_never_counts`, `test_the_stamp_advancing_ends_the_incident_then_the_next_comments_or_opens_a_new_card`, `test_a_detached_board_never_files`, `test_an_unsynced_board_never_files`, `test_an_exception_in_the_check_never_breaks_the_dispatcher_tick`, `test_the_dispatcher_tick_runs_the_replica_check`, `test_the_card_names_host_board_stamp_minutes_outcome_outbox_and_status_command`, `test_the_card_lists_frozen_children_of_remote_parents_with_their_board_status`, `test_an_unreachable_board_lists_the_local_mirror_status_instead`, `test_replay_hub_slow_rounds_1112`, `test_replay_macbook_75k_backlog_after_sleep_1112`, `test_replay_macbook_schema_skew_refusal_1112`.
- 3650's lock-guard read is untested by a dedicated test.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Only boards in primary/shadow mode are watched.
