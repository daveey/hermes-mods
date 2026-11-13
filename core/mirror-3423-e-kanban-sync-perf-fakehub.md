# Kanban sync: SQLite divergence prefilter, off-thread links pass, FakeHub lease release

Patches: **3208, 3214, 3233**.

## Behavior
- Divergence repair compares settled cards inside SQLite, so settled cards never reach Python; the prefilter queues the same fields as the full scan, matches run refs, and is unavailable for in-memory files (falls back to full scan). A row that leaves the replica mid-pass is skipped.
- The chat-link pass runs off the sync round thread: rounds keep running while a links pass is slow, and a failed pass does not stop later passes.
- The test FakeHub's lease release always emits the run-outcome event, honors the requested outcome, merges diagnostic/extra into run metadata, rejects unknown outcomes, and does not model `spawn_failed`.

## How it works
- 3214 adds a prefilter in `kanban_sync_divergence.py`, a new `kanban_sync_links_pass.py` that runs the pass on a worker thread, and wiring in `kanban_sync_client.py`.
- 3208 and 3233 extend the test fake board `tests/hermes_cli/kanban_sync_fake_board.py`.

## Code
- `hermes_cli/kanban_sync_divergence.py`
- `hermes_cli/kanban_sync_links_pass.py`
- `hermes_cli/kanban_sync_client.py`
- `tests/hermes_cli/kanban_sync_fake_board.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_sync_divergence_prefilter.py` and `tests/hermes_cli/test_kanban_sync_links_thread.py` (names added across both): `test_divergence_repair_skips_a_row_that_left_the_replica_mid_pass`, `test_settled_cards_never_reach_python`, `test_prefilter_and_full_scan_queue_the_same_fields`, `test_prefilter_skips_unsettled_and_matches_run_refs`, `test_prefilter_unavailable_for_in_memory_files`, `test_rounds_keep_running_while_a_links_pass_is_slow`, `test_a_failed_links_pass_does_not_stop_later_passes`.
- `tests/hermes_cli/test_kanban_sync_rejections.py`: `test_fake_lease_release_emits_run_outcome_event`, `test_fake_lease_release_merges_diagnostic_into_run_metadata`, `test_fake_lease_release_rejects_unknown_outcome`, `test_fake_lease_release_spawn_failed_is_not_modelled`.
- `tests/hermes_cli/test_dispatcher_tick_read_budget.py` and `tests/hermes_cli/test_kanban_sync_steady_links.py` were adjusted.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
