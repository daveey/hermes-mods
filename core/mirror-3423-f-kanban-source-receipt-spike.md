# Kanban source receipt on the status-leaving wire op (spike R1)

Patches: **3294–3298**.

## Behavior
- A spike carries a `source_receipt` on the status-leaving sync op (a run ending) so the Board receives it with the transition: it rides the op, is not a synced column, is rolled back with the ending, and a refused ending leaves no pending receipt. A stale reclaim after a refused complete sends no receipt, and a malformed pending receipt is rejected.
- An unseen card's full-row push keeps the virtual field. An old Board answering `forbidden_field` for a virtual wire field has it dropped without recording an omission, and the original op is retired.
- Status: spike code (patch subjects say "spike"); 3294 adds the tests first (red on main), later patches implement.

## How it works
- 3294 adds the spike tests. 3295 adds a pending-receipt table and trigger pair staged at the first statement of the transaction. 3296 replaces that with a one-statement `with_source_receipt` and purges pending receipts in `_end_run`. 3297 allows `VIRTUAL_WIRE_FIELDS` through `_wire_fields` and keeps virtual fields on an unseen full row. 3298 makes the sync-compat layer drop a virtual field on `forbidden_field` without an omission.

## Code
- `hermes_cli/kanban_source_receipt.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_db_run.py`
- `hermes_cli/kanban_db_schedule.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_dependency_review.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_compat.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_source_receipt_spike.py`: `test_receipt_rides_status_leaving_op`, `test_unseen_card_full_row_keeps_source_receipt`, `test_receipt_not_a_synced_column`, `test_receipt_rolled_back_with_ending`, `test_refused_ending_leaves_no_pending_receipt`, `test_stale_reclaim_after_refused_complete_sends_no_receipt`, `test_push_wire_op_carries_source_receipt`, `test_old_board_forbidden_source_receipt_drops_without_omission`, `test_malformed_pending_receipt_rejected`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Spike-quality work; 3295's table-and-trigger design is superseded by 3296 within the same range.
