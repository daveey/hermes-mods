# Kanban sync, lock discovery and event index robustness

Patches: **3002, 3012, 3014, 3015, 3038**.

## Behavior
- Replica-lag acknowledgement parsing accepts already-parsed field dicts as well as JSON strings.
- The sync thread's own repair-revision steps (feed pull, parent refresh, push acknowledgements, placement reevaluation) are credited, so the full repair pass is skipped when only its own writes explain the revision gap; repair rounds are counted.
- Event-kind history lookups are indexed, and an existing database gains the index on reopen.
- A root gateway lease's 2 s claim retry is bounded to a 30 s fast window per contention episode, then falls back to the normal retry interval.
- The singleton sidecar owner is verified through fdinfo before consulting the global lock table, so a lock table that omits a held lock still reports the holder.

## How it works
- 3002 changes `acked_task_ids` in `kanban_replica_lag.py` to accept dict or JSON fields.
- 3012 adds `kanban_sync_own_writes.py`, recording the thread's committed steps per board file inside `credited()`, used by the repair window and sync client.
- 3014 adds an index for event-kind lookups at connect time.
- 3015 adds `ROOT_CLAIM_FAST_RETRY_WINDOW_SECONDS` in `gateway/kanban_singleton.py` and calls the sampler's `wait_fresh` directly.
- 3038 verifies a live sidecar pid with `_owns_lock` before falling back to `/proc/locks`.

## Code
- `hermes_cli/kanban_replica_lag.py`
- `hermes_cli/kanban_sync_own_writes.py`
- `hermes_cli/kanban_sync_repair_window.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_connect.py`
- `gateway/kanban_singleton.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_pickup_replica_lag.py`: `test_acked_task_ids_accepts_parsed_and_json_fields`.
- `tests/hermes_cli/test_kanban_sync_feed_apply_credit.py`: `test_a_push_ack_after_the_inputs_read_is_credited`, `test_an_unrecorded_apply_gets_no_credit`.
- `tests/hermes_cli/test_kanban_sync_own_writes.py`: `test_only_this_threads_committed_steps_on_this_connection_are_recorded`, `test_a_block_that_ends_inside_a_transaction_keeps_its_steps_until_it_commits`, `test_a_board_without_the_revision_table_records_nothing`, `test_a_reservation_write_by_the_sync_thread_is_credited`, `test_another_threads_write_during_the_round_still_repairs`, `test_round_outcomes_are_counted_and_logged_per_board`, `test_decide_reasons`.
- `tests/hermes_cli/test_kanban_event_read_budget.py`: `test_creator_search_does_not_scan_heartbeat_history`, `test_latest_move_uses_kind_and_id_order`, `test_existing_integer_schema_gets_index_on_reopen`.
- `tests/gateway/test_kanban_singleton_lease.py`: `test_root_claim_fast_retry_backs_off_against_a_holder_that_never_yields`.
- `tests/hermes_cli/test_kanban_host_lock_visibility.py`: `test_presence_reports_host_dispatcher_held_by_another_home`, `test_lock_holder_unaffected_by_unheld_sidecar`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
The fdinfo verification test is skipped where `/proc/self/fdinfo` is absent (Linux only).
