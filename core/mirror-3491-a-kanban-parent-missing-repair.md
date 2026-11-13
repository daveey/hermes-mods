# Kanban missing-parent self-clear and repair-grant hardening

Patches: **3474, 3479**.

## Behavior
- A child card blocked by the missing-parent detector (source `parent_missing`, no human request) whose parent links are all removed now returns to `todo` and is promoted by the same readiness pass, instead of staying blocked with nothing to wait on. Blocks from other sources with no parents stay blocked.
- When the detector block clears because the parent appeared, the open "Recover missing dependencies" repair card gets one comment naming the child and the blocked event. A done or archived repair card gets no comment; a later pass does nothing more.
- The missing-parent repair grant lets a repair worker unblock only its own child. If the repair card's creator is unknown or is another card, the worker gets the plain local refusal instead of being forwarded to the home end.
- A card re-held after a release (including via promotion from `blocked`) is not granted a second release; the real failure breaker lands the card in `triage`, which the status check refuses.
- The `kanban_unblock` tool's missing-parent branch writes the same audit row (`missing_parent_requeue` with by, repair card, target) as the self-heal and owner-wake grants (no listed test covers the audit row directly beyond the source test name in the commit message).

## How it works
- 3474 changes `clear_found_parents` in the kanban graph module: an empty parent set counts as resolved, and the clear comments on the repair card inside the readiness transaction.
- 3479 names the release events as a constant (`BLOCK_RELEASE_EVENTS`, now also listing `promoted` and `promoted_manual`) with the invariant that every write into `blocked` appends a `blocked` event. `_forward_missing_parent_unblock` forwards only when the creator equals the target. Docstrings now describe the `gave_up` arm as defence in depth.

## Code
- `hermes_cli/kanban_db_graph.py`
- `hermes_cli/kanban_requeue.py`
- `tools/kanban_tools_self_heal.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_parent_missing_self_clear.py`: `test_every_parent_unlinked_returns_the_detector_blocked_child_to_ready`, `test_a_hand_block_with_no_parents_stays_blocked`, `test_clearing_the_block_comments_on_the_open_repair_card`, `test_a_finished_repair_card_gets_no_comment`.
- `tests/hermes_cli/test_kanban_operator_call_missing_parent.py`: `test_worker_kanban_unblock_tool_forwards_to_the_home_end_to_end`.
- `tests/tools/test_kanban_missing_parent_repair_unblock.py`: `test_repair_card_without_this_child_as_creator_is_not_forwarded`, `test_real_breaker_trip_after_a_release_leaves_no_grant`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The new audit row for the `kanban_unblock` tool branch has no test name added in these patches that is visibly dedicated to it: treat as untested here.
