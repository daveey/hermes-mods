# Kanban sync: repair window and batched provenance

Patches: **3060, 3103, 3107, 3110**.

## Behavior
- Writes by other processes share one full repair pass per 30-second window instead of one pass per write; captured writes are still pushed every round and the remote-parent mirror runs every round.
- Chat-link and backfill classification reads each home's session database once per pass instead of once per row, and backfill no longer reads provenance inside its write transaction; output is identical to the per-row implementation.
- Review-nit tidy: `pull()` no longer takes a `revisions=` parameter; `decide()` no longer takes `local_min_s`.

## How it works
- 3060 tidies own-write credit naming and removes the unused `revisions=` parameter (no behavior change).
- 3103 gates repair-revision gaps on `LOCAL_WRITE_REPAIR_MIN_S`.
- 3107 adds `headless_sessions()` and `prefetch_sources()` and classifies chat links in batches.
- 3110 drops `decide(local_min_s)` and documents that the deferral covers link repairs too.

## Code
- `hermes_cli/kanban_sync_repair_window.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_own_writes.py`
- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_creator_chats.py`
- `hermes_cli/kanban_session_provenance.py`
- `hermes_cli/kanban_db_notify.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_sync_own_writes.py`: `test_another_connections_write_during_the_round_still_repairs`.
- `tests/hermes_cli/test_kanban_sync_repair_window.py`: `test_a_local_write_after_its_window_repairs_at_once`, `test_a_burst_of_local_writes_runs_one_repair_pass_per_window`, `test_a_captured_local_write_is_pushed_by_a_deferred_round`.
- `tests/hermes_cli/test_kanban_sync_copy_links.py`: `test_a_lost_edge_is_restored_when_the_production_repair_window_ends`.
- `tests/hermes_cli/test_kanban_chat_links_bulk_provenance.py`: `test_local_links_opens_each_home_once_for_500_sessions`, `test_local_links_stays_bounded_when_the_pass_outlives_the_cache_ttl`, `test_owner_written_releases_opens_each_home_once_for_500_rows`, `test_local_links_output_is_byte_identical_to_the_per_row_implementation`, `test_owner_written_releases_output_is_byte_identical`, `test_reconcile_and_machine_links_match_per_row_verdicts`, `test_backfill_reads_no_provenance_inside_its_write_transaction`, `test_backfill_result_and_rows_match_the_per_row_implementation`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
A lost link edge can stay missing for up to the 30-second window on a deferred round.
