# Kanban pool placement: park pin, repin fallback, local-marker queueing

Patches: **3181, 3213, 3228**.

## Behavior
- Parking a card no longer pins it to the hub just because its text names a dirty shared checkout; a false pin is lifted at the next park. A dirty linked worktree named in the text still pins, and the event names the check.
- A single-branch clone whose HEAD is on another remote branch is treated as pushed, so park and move keep it on the pool. A HEAD that no remote has still holds, and an unreachable remote still pins.
- Worker-first self-check: when the board refuses its repin at the same revision, the card falls back to a reserve until the revision moves; an explicit move repin is not downgraded to a reserve.
- A card with a local-marker label is queued for the pool only when one live machine holds its whole label set; otherwise a pool worker keeps it.

## How it works
- 3181 changes `kanban_park_pin.py` and `kanban_move_pool.py` to ignore shared (non-worktree) checkouts named in text and to treat untracked remote branches correctly.
- 3213 adds a reserve fallback in `kanban_worker_placement.py` keyed on the board revision.
- 3228 adjusts `kanban_placement.py` and `kanban_worker_placement.py` to require a single machine covering all marker labels.

## Code
- `hermes_cli/kanban_park_pin.py`
- `hermes_cli/kanban_move_pool.py`
- `hermes_cli/kanban_worker_placement.py`
- `hermes_cli/kanban_placement.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_pool_queue.py`: `test_park_ignores_a_dirty_shared_core_checkout_the_card_text_names`, `test_a_false_pin_on_a_shared_checkout_is_lifted_at_the_next_park`, `test_a_dirty_linked_worktree_the_text_names_still_pins_and_the_event_names_the_check`, `test_a_single_branch_clone_whose_head_is_on_another_remote_branch_is_pushed`, `test_park_and_move_keep_a_pushed_single_branch_clone_on_the_pool`, `test_a_head_no_remote_holds_and_an_unreachable_remote_still_pin`, `test_pool_worker_keeps_a_mac_marker_card_when_no_live_machine_holds_every_label`.
- `tests/hermes_cli/test_kanban_pool_deny_runs_on.py`: `test_a_refused_self_check_repin_falls_back_to_the_reserve_until_the_revision_moves`, `test_a_refused_explicit_move_repin_is_not_downgraded_to_a_reserve`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
