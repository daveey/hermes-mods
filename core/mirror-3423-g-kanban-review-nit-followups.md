# Kanban review follow-ups: hold test name, GC logging, priority feed rows

Patches: **3306, 3315, 3316**.

## Behavior
- Scratch GC logs a warning for every detached trash tree it sweeps, so a tree removed without a removal record leaves a trace.
- The priority feed keeps cards created and finished inside the capture window in the inventory rows, so an `exceptions` entry naming such a card resolves the same as on a later page.
- 3306 only renames a test to the hold-after-latest-boundary rule.

## How it works
- 3306 renames the local observed-hold test.
- 3315 logs in `_sweep_detached_trash` and corrects a comment on trash infix ordering.
- 3316 keeps terminal arrivals in capture rows and documents fixed-head limits in the feed docstring.

## Code
- `hermes_cli/kanban_priority_feed.py`
- `hermes_cli/kanban_worktree_gc.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_observed_pr_hold_boundaries.py`: `test_local_observation_requires_a_hold_after_the_latest_boundary`.
- `tests/hermes_cli/test_kanban_scratch_gc_trash_sweep.py`: `test_each_swept_tree_is_logged`.
- `tests/hermes_cli/test_kanban_priority_fixed_head.py`: `test_card_created_and_finished_in_window_resolves_an_exception_like_a_later_page`, `test_terminal_arrival_joins_the_rows_in_id_order`, `test_busy_board_inventory_rows_are_every_card_at_the_final_head`, `test_priority_change_after_an_earlier_in_window_old_value_refuses_even_paged_late`, `test_only_in_window_priority_change_paged_late_is_exact`, `test_above_cap_card_paged_before_the_window_is_planned_but_an_above_cap_arrival_refuses`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
