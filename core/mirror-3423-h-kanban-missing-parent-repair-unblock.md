# Kanban missing-parent repair card may unblock its child

Patches: **3418–3420**.

## Behavior
- The dispatcher blocks a child as `parent_missing` and files a repair card. That repair card may now unblock exactly its creator child through the `kanban_unblock` tool (or an operator call), only while the child is blocked with no human request and its newest relevant block event is `parent_missing` naming this repair card; the unblocked event is attributed to the repair card's agent run rather than a user.
- Other callers, other targets, blocks superseded by a hand block, and blocks naming a different repair card stay refused.
- A hold placed after the parent_missing block (a failure-breaker give-up, a later human request, an unblock) no longer reads as that block; the grant refuses it. The repair card must still be `running` with its current run (a reclaimed earlier run is refused), and the grant is rechecked inside the unblock transaction.
- A child that left `blocked` without an `unblocked` event (todo, ready, running, done, archived) gets no grant.
- A child that exists only as a replica copy on another gateway is forwarded as an operator call (tested via a forwarding case).
- The repair brief names the tool and forbids falling back to the CLI over ssh.

## How it works
- 3418 adds `missing_parent_repair_grant` to `kanban_requeue.py`, lets the `kanban_unblock` tool return `via=missing-parent-repair`, extends the operator-call home check in `kanban_operator_call.py`, and updates the tool schema and graph helper.
- 3419 adds `current_block_payload` (empty when an `unblocked` or `gave_up` event is newer than the newest `blocked`) used by both checks, and applies the owner-wake run check in the unblock transaction (`kanban_tools_self_heal.py`).
- 3420 adds a test pinning that the grant needs the child to still be blocked.

## Code
- `hermes_cli/kanban_requeue.py`
- `hermes_cli/kanban_operator_call.py`
- `hermes_cli/kanban_db_graph.py`
- `tools/kanban_tools_schemas.py`
- `tools/kanban_tools_self_heal.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- Across `tests/hermes_cli/test_kanban_operator_call_missing_parent.py` and `tests/tools/test_kanban_missing_parent_repair_unblock.py`: `test_repair_card_unblocks_its_child_through_an_operator_call`, `test_another_caller_still_cannot_release_the_parent_missing_block`, `test_repair_card_cannot_unblock_another_hub_card`, `test_block_superseded_by_a_hand_block_is_refused_at_home`, `test_parent_missing_block_naming_another_repair_card_is_refused`, `test_repair_worker_unblocks_its_child_attributed_to_the_repair_card`, `test_another_worker_cannot_unblock_a_parent_missing_child`, `test_repair_card_cannot_unblock_another_target`, `test_block_superseded_by_a_hand_block_stays_refused`, `test_parent_missing_block_naming_a_different_repair_card_stays_refused`, `test_grant_is_rechecked_inside_the_unblock_transaction`, `test_repair_brief_names_the_tool_and_forbids_ssh_cli`, `test_child_on_another_gateway_is_forwarded_as_an_operator_call`, `test_breaker_hold_after_a_release_is_refused_at_home`, `test_child_that_later_got_a_human_request_is_refused_at_home`, `test_breaker_hold_after_releasing_an_own_agent_block_is_refused_at_home`, `test_hold_placed_after_the_parent_missing_block_stays_refused`, `test_child_that_later_got_a_human_request_stays_refused`, `test_repair_run_that_no_longer_owns_the_card_is_refused`, `test_block_naming_this_repair_card_on_another_child_is_refused`, `test_card_the_detector_did_not_file_gets_no_grant`, `test_later_block_naming_the_repair_card_but_not_from_the_detector_is_refused`, `test_grant_needs_the_child_to_still_be_blocked`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Only a card created by the missing-parent detector can hold the grant.
