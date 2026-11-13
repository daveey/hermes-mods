# Kanban sync replica alarm and awaited-card re-probe

Patches: **3677, 3684**.

## Behavior
- The stale-replica alarm no longer files a false card after the wall clock steps back: any change of the replica's `pulled_at` stamp counts as an advance. A missing or unreadable replica still counts as not advanced.
- A park awaiting another card now re-probes early when that card's done status arrives by a snapshot rebuild (Resync) or by `kanban sync reconcile`, not only by the round's feed pull; before, it slept up to its check interval.
- The `awaited_card_done_park_lag` warning reads the awaited cards' status from the replica itself, so it sees a status that arrived by any path, and warns once per park.

## How it works
- 3677 changes `_check_board` from `stamp > seen.stamp` to `stamp != seen.stamp`, because the stamp is wall time while the timer is monotonic.
- 3684 reads the old replica's card statuses before it is dropped (`acw.statuses`), diffs them against the rebuilt replica (`acw.rebuilt`), and carries pages applied before the Resync (`acw.changes_page`) on the rebuilt Pulled. `replica_at_head` forwards each chunk's `status_changed` to `expedite`. `report_lag` reads replica status at most once per 60 s per board and marks a warned park until it leaves scheduled. Expedite only moves an existing probe earlier; no new probing.

## Code
- `hermes_cli/kanban_sync_replica_age.py`
- `hermes_cli/kanban_awaited_card_wake.py`
- `hermes_cli/kanban_sync_backlog.py`
- `hermes_cli/kanban_sync_client.py`
- `tests/hermes_cli/test_kanban_sync_replica_age.py`
- `tests/hermes_cli/test_kanban_awaited_card_wake.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_sync_replica_age.py`: `test_a_wall_clock_step_backwards_is_still_an_advance`.
- `tests/hermes_cli/test_kanban_awaited_card_wake.py`: `test_review_done_arriving_by_a_snapshot_rebuild_wakes_the_owner_within_a_minute`, `test_review_done_arriving_by_sync_reconcile_wakes_the_owner_within_a_minute`, `test_lag_warning_reads_the_replica_for_a_status_no_expedite_was_handed`, `test_the_replica_read_is_throttled_and_skipped_without_a_replica`, `test_rebuilt_diffs_the_replica_and_keeps_the_pages_applied_before_the_resync`, `test_changes_page_attaches_the_pull_s_applied_changes_to_a_resync`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The early wake is a latency optimization; the check interval remains the fail-safe.
