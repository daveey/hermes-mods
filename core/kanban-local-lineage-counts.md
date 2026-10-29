# Local Kanban cards count once per compression lineage

Patch: `0682, 0695`; core commit `a3a632954c`.

## Behavior

- A local card created in a root session and linked by a counted `create` or `shepherd` TUI subscription to its compressed successor counts once in their combined sidebar row, matching `session_tasks`.
- Active, blocked, parked, held and stalled counts deduplicate shared cards. An alias displayed alone still counts its obligation.
- Distinct cards, the same task id on different boards, and local versus remote cards remain distinct.
- A cached annotation snapshot keeps counts and identities from the same generation; invalidating a board refreshes both.

## How it works

The local board transaction retains per-session board-qualified card identities alongside count buckets and parked state. Those identities survive the board memo and activity cache. Annotation subtracts repeated identities across lineage aliases through a helper shared with remote-card deduplication. Held and stalled subsets use the same identity rule. Counts and identity maps leave the cache lock together, so a refresh cannot mix generations.

## Code

- `hermes_cli/kanban_session_activity.py`: local identity collection, cache/snapshot propagation and annotation.
- `hermes_cli/kanban_activity_identity.py`: shared lineage duplicate and subset calculations.
- `tests/hermes_cli/test_kanban_local_lineage_counts.py`: regression suite.
- `tests/hermes_cli/test_kanban_session_activity.py`: concurrency mocks follow the nine-map annotation snapshot contract; assertions are unchanged.

## Tests

Executed on core commit `a3a632954c` using:

```sh
scripts/run_tests.sh tests/hermes_cli/test_kanban_local_lineage_counts.py tests/hermes_cli/test_kanban_session_activity.py tests/hermes_cli/test_kanban_chat_links_parity.py tests/hermes_cli/test_kanban_sidebar_poll_reads.py tests/hermes_cli/test_kanban_session_activity_triage.py tests/hermes_cli/test_profiles_sidebar_scope.py --file-retries 0
```

Result: 126 passed, 0 failed across six files. The new suite has 14 cases:

- `test_shared_local_card_counts_once_per_lineage`: running/scheduled/blocked × create/shepherd, fresh and cached reads, list parity, and single-alias controls.
- `test_distinct_local_cards_are_not_collapsed`: two cards remain two.
- `test_local_identity_snapshot_tracks_board_changes`: consistent in-snapshot state and refresh after archival.
- `test_shared_held_card_is_a_single_active_subset` and `test_shared_stalled_card_is_a_single_parked_subset`: subset counts remain one.
- `test_mixed_local_and_remote_lineage_snapshot`: running/scheduled remote cards remain separate from the local card, including stalled counts.
- `test_annotation_keeps_count_and_identity_generation_together`: an intervening cache refresh cannot replace identities for already-read counts.
- `test_same_local_id_on_different_boards_remains_distinct`: board-qualified identity preserves both cards.

The initial reproduction failed eight cases before the fix; held/stalled and generation-interleave regressions were also reproduced before correction. Live Desktop rendering and process activation were not exercised here.

## Applying

Apply the ordered `core/patches` series, including `0682, 0695`, with `git am -3 --empty=keep`. On the original core parent, `0682, 0695` also applies alone. Keep earlier curated patches and their published numbering unchanged.

## Gotchas

This changes aggregation, not task ownership or subscriptions. A card remains associated with both aliases; only the combined lineage count is deduplicated. Local keys include the board, so task-id equality alone does not merge cards.

## Why not a plugin?

The count cache, board read transaction and annotation snapshot are core-owned. Retaining identity at that boundary avoids a second plugin projection drifting from the task strip.
