# Kanban: parent_missing block clears itself

Patches: **3442**.

## Behavior
- A todo child blocked by the missing-parent detector (transient block, source `parent_missing`) returns to `todo` once every parent is visible again, and is promoted to `ready` when all parents are done or archived. Previously the block was sticky and the child stayed blocked with nothing unsatisfied.
- The unblock event records `cleared_block`, `blocked_event_id`, `parents` and `repair_task_id`; the repair card is left to finish normally.
- Stays blocked: blocks from any other source, a later hand block over the detector's, a card with a human request, and a card with a parent still missing.

## How it works
3442 makes the readiness pass call `kanban_db_graph.clear_found_parents` on each blocked row first. It acts only when the newest lifecycle event is the detector's own block, there is no human request, and parent states show every parent again; it then runs the normal todo path (`parent_found`, promotion).

## Code
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_graph.py`
- `tests/hermes_cli/test_kanban_parent_missing_self_clear.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_parent_missing_self_clear.py`: `test_the_parent_row_appearing_done_returns_the_child_to_ready`, `test_an_open_parent_coming_back_returns_the_child_to_todo`, `test_the_remote_parent_mirror_saying_done_returns_the_child_to_ready`, `test_a_transient_block_from_another_source_stays_blocked`, `test_a_later_hand_block_over_a_parent_missing_block_stays_blocked`, `test_a_parent_missing_block_with_a_human_request_stays_blocked`, `test_a_parent_missing_block_with_one_parent_still_missing_stays_blocked`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- `kanban_db.py` is kept at its line cap, so the logic lives in `kanban_db_graph.py`.
