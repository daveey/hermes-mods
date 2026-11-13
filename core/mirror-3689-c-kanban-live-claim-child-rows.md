# Kanban sync: Board live_claim refusal of run and event rows

Patches: **3668, 3689**.

## Behavior
- When the Board refuses a non-holder's run handoff (`task_runs`) or lifecycle event (`task_events`) with `live_claim`, the refusal is recorded on the card as `sync_rejected`, naming the table and row uid.
- On a primary board the local row is restored to the Board's version; a row the Board never stored (a phantom synthesized run, a `completed` event) is deleted and tombstoned so reconcile never re-uploads it. A shadow board records the refusal and keeps its rows.
- An older client that handles only task rows sees extra final rejections; nothing applies and nothing stays pending.
- The reduced `compat` retry (queued after a `forbidden_field` rejection) of a refused run handoff is restored too, not just plain upserts.

## How it works
- 3668 adds `kanban_sync_live_claim.py`, hooked from the sync client and compat layer. `record_rejection` also resolves the task of a delta op that omits task_id. The returned row is applied with the run's sync_version cleared so the newer-local check does not skip it. The test fake Board mirrors the Board's row fence.
- 3689 makes `restore_child` treat `compat` ops like `upsert`; before, the refusal was reported handled but the local run kept the refused handoff.

## Code
- `hermes_cli/kanban_sync_live_claim.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_compat.py`
- `tests/hermes_cli/kanban_sync_fake_board.py`
- `tests/hermes_cli/test_kanban_live_claim_child_rows.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_live_claim_child_rows.py`: `test_refused_run_handoff_is_recorded_and_the_local_run_restored`, `test_refused_phantom_run_is_removed_and_never_reuploaded`, `test_shadow_board_records_the_refusal_and_leaves_its_rows`, `test_old_wire_shape_task_only_handling_sees_extra_final_rejections`, `test_refused_compat_retry_of_a_run_handoff_restores_the_local_run`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Requires a Board that enforces the live-lease rule on child rows; against an older Board no refusals arrive and nothing changes.
