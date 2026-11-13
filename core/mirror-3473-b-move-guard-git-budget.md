# Move guard: one git deadline per tree scan

Patches: **3448**.

## Behavior
- The move guard's worktree state check draws every git call from a single deadline, so the state budget is a real bound (hung hidden-edit scans, slow hardening probes, and the no-remote trunk path no longer overrun it).
- When the budget is spent the tree reads `unchecked worktree <p>` (fail safe, still state) instead of `unpushed commits` or `dirty worktree`; a git status timeout reads `unchecked ... (git timed out)`. An unreachable remote within budget still reads `unpushed`.
- `state_parts()` recognises `unchecked worktree <p> (<cause>)`, so a budget-spent park pin records check/tree.
- A park naming another card's tree with unpushed commits does not pin; the owner's own park does.

## How it works
3448 adds `hermes_cli/git_budget.py` (`GitBudget`: timeout drawn from one deadline, raises `GitUnchecked` when spent). The dirty, unpushed, local-trunk, hardening-env, archive-env and hidden-index-edit helpers take an optional `budget` keyword; `tree_state` and `pushed_on_remote` thread one budget through. Without a budget other callers are unchanged.

## Code
- `hermes_cli/git_budget.py`
- `hermes_cli/_subprocess_compat.py`
- `hermes_cli/kanban_move_pool.py`
- `hermes_cli/worktree_archive.py`
- `hermes_cli/worktree_ops.py`
- `hermes_cli/worktree_retention.py`
- `tests/hermes_cli/test_kanban_move_guard_budget.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_move_guard_budget.py`: `test_a_hung_hidden_edit_scan_spends_the_budget_and_reads_unchecked_not_dirty`, `test_slow_hardening_probes_draw_from_the_deadline`, `test_the_no_remote_trunk_path_draws_from_the_deadline_and_a_spent_refresh_reads_unchecked`, `test_a_git_status_timeout_reads_unchecked_not_dirty`, `test_unbudgeted_unpushed_check_still_fails_safe_on_a_timeout`, `test_an_unreachable_remote_within_budget_still_reads_unpushed`, `test_state_parts_names_every_check`, `test_a_budget_spent_park_pin_records_the_unchecked_check_and_tree`, `test_a_park_ignores_unpushed_state_in_another_cards_text_named_tree`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Replaces the earlier per-helper budget handling in `tree_state` from a preceding move-guard patch; apply after it.
