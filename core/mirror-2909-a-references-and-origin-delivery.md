# Card references, creator subscriptions and restored origin custody

Patches: **2728, 2736, 2737, 2780**.

Related context: [kanban-phantom-ref-replica-2678.md](kanban-phantom-ref-replica-2678.md), [kanban-board-restore-subs-2726.md](kanban-board-restore-subs-2726.md).

## Behavior

Summary advisory checks accept short task-ID prefixes found in local boards, Board replicas or the hub replica, and ignore tokens embedded in paths, branches or identifiers. Full IDs still require exact existence; unknown prefixes/full IDs remain phantom. Children created by a worker from another gateway's creator copy regain the owning chat's delivery subscription. A locally unsubscribed creator stays unsubscribed. Restored courier cards can republish their original origin-binding marker rather than publishing an empty binding set.

## How it works

2728 extracts the prose scan to `kanban_prose_refs.py` (re-exported by `kanban_db`), adds the negative word/slash lookbehind, and consults `replica_prefixes` for tokens shorter than `t_` plus 16 hex digits. This is advisory prefix resolution, not relaxation of authenticated `created_cards` custody. 2736 copies delivery columns without copying notification cursors, and falls back to `_owner_delivery` when the creator's subscription is absent. 2737 restricts that fallback to `_foreign_copy`: a nonempty `created_on` different from this gateway; missing columns/own origin do not qualify. 2780 adds a synced `origin_intent_recorded` event containing the complete intent row in the create/adopt transaction. The sync apply restores the exact row with `INSERT OR IGNORE` only for an own `kanban_restored_cards`-marked card in its primary live file, with matching card and owner-gateway identity. Event GC preserves the carry kind.

## Code

- `hermes_cli/kanban_board_replica_refs.py`
- `hermes_cli/kanban_creator_chats.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_origin_creation.py`
- `hermes_cli/kanban_origin_intent_carry.py`
- `hermes_cli/kanban_prose_refs.py`
- `hermes_cli/kanban_session_activity.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_board_replica_refs.py`: `test_short_prefix_of_a_board_replica_card_is_not_flagged_but_an_orphan_prefix_is`, `test_id_found_nowhere_is_still_flagged_and_still_refused`.
- `tests/hermes_cli/test_kanban_false_signals.py`: `test_card_prefix_inside_branch_name_is_not_flagged`, `test_prose_prefix_of_a_real_card_is_not_flagged`, `test_nonexistent_full_length_id_is_still_flagged`, `test_short_token_prefixing_no_card_is_flagged`, `test_prefix_of_a_replica_only_card_is_not_flagged`.
- `tests/hermes_cli/test_kanban_origin_adoption.py` (fixture/assertion changes; no test definition name appears in this diff).
- `tests/hermes_cli/test_kanban_sync_restore_origin_intents.py`: `test_restored_courier_card_republishes_its_exact_marker_and_the_obligation_advances`, `test_restore_hook_only_rebuilds_an_own_restored_card_from_a_valid_payload`, `test_event_gc_keeps_the_carried_intent`.
- `tests/tools/test_kanban_creator_copy_owner_sub.py`: `test_child_of_a_copy_gets_its_owners_sub`, `test_owner_sub_in_this_file_is_still_copied_once`, `test_own_card_whose_chat_unsubscribed_gives_its_child_no_sub`.

Coverage is limited to the entry points and fixture controls above; live services, deployment and user-interface behavior are not established by this documentation task.

## Applying

Use the [README full replay procedure](README.md#applying). Apply these patches in published ordinal order with the intervening series, replay bridges and mandatory post-replay repair described there. They are incremental changes, not independently certified cherry-picks. This document does not claim that the full mirror replay or feature suites ran during the documentation pass.

## Gotchas

A prefix match is existence evidence only, not a unique-card resolution or creation authorization. Rebuilding origin intent is not the same as rebuilding subscriptions: both parts are needed for their respective delivery paths. Foreign copies, malformed carry rows and unmarked cards are skipped. Board event retention is 30 days, so an intent whose carry event aged out before loss still fails closed. Existing intent rows are never rewritten. Live cross-gateway delivery was not exercised by this documentation pass.

## Plugin status

Core/CI implementation; no plugin is added or required. A plugin cannot replace the shared database, admission, worker or workflow invariants documented here.
