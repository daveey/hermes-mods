# Kanban missing-parent detection judged only from a fresh replica

Patches: **3353, 3354–3358**.

## Behavior
- Missing-parent blocks and unclaimed-ready notices are judged only from a replica copy pulled to the feed head within 600 s on a synced board; a stale replica yields "unknown" rather than a block, repair card or notice.
- A missing-parent streak is scoped to its parents and ends at an unblock or a link to a missing parent; a link to a present parent does not restart it.
- A detached board (mode off) judges missing parents itself.
- A stalled replica's deferral is logged once at INFO per stale stamp and count, and a fresh pull vouches only once the mirror is refreshed from it.

## How it works
- 3353 adds `kanban_replica_freshness` and gates `note_missing_parents`, chat-wait and parent-unclaimed on it.
- 3354 and 3355 refine the streak rules and apply the gate only to primary/shadow boards.
- 3356 adds `report_deferred` and calls `note_missing_parents` directly from `recompute_ready`.
- 3357 records `mirror_pulled_at` via `mark_mirrored` once the mirror reflects the pull.
- 3358 keys the log dedupe on (stamp, count).

## Code
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_graph.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_parent_unclaimed.py`
- `hermes_cli/kanban_replica_freshness.py`
- `hermes_cli/kanban_sync_client.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_cross_gateway_parents.py`: `test_a_stale_replica_never_starts_or_expires_a_missing_parent_streak`, `test_a_fresh_replica_that_lacks_the_parent_still_blocks_and_files_one_repair`, `test_a_detached_board_keeps_its_mirror_table_but_still_blocks_a_missing_parent`, `test_a_stalled_replica_is_reported_once_per_stale_stamp_at_info`, `test_a_fresh_pull_does_not_vouch_for_a_mirror_not_yet_refreshed_from_it`, `test_the_deferral_line_is_said_again_when_more_children_wait_on_the_same_stamp`.
- `tests/hermes_cli/test_kanban_replica_freshness.py`: `test_chat_wait_sends_nothing_from_a_stale_copy_then_reports_after_catch_up`, `test_chat_wait_from_a_fresh_copy_is_unchanged`, `test_parent_unclaimed_sends_nothing_from_a_stale_copy`, `test_an_unsynced_board_is_its_own_authority`.
- `tests/hermes_cli/test_kanban_missing_parent_detector.py`: `test_a_parent_linked_after_an_unblock_starts_its_own_streak`, `test_a_newly_missing_parent_waits_its_own_window`, `test_a_streak_naming_more_parents_still_covers_the_ones_left`, `test_a_link_to_a_present_parent_does_not_restart_a_missing_ones_window`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A stale replica delays legitimate blocks until it catches up.
