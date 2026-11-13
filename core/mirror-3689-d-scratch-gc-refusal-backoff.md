# Kanban scratch GC: per-tree refusal backoff and pass budget

Patches: **3671**.

## Behavior
- Scratch trees whose archive is refused are remembered per board and retried with a backoff (2 h doubling to 24 h, forgotten after 14 days) instead of paying the refusal cost every pass. A changed tree (path, completion time, root inode/mtime) is retried at once; a successful archive clears the entry; an unreadable state file is treated as empty.
- A pass budget stops new archives (aged loop) and custody Board reads once spent; the remainder is logged as deferred for the next pass.
- `hermes kanban gc --scratch-budget-seconds N` sets the budget (default 1200, 0 = no limit), leaving headroom below a wrapper's 1800 s kill.
- All ownership, custody, live-job and snapshot guards still run for every tree that is attempted.

## How it works
3671 adds `kanban_scratch_backoff.py` (host-local `scratch-gc/refusals-<board>.json` and a budget object), threads both through `gc_task_scratch` (aged trees oldest first) and `gc_custody`, and adds the CLI flag in the parser and ops.

## Code
- `hermes_cli/kanban_scratch_backoff.py`
- `hermes_cli/kanban_scratch_custody.py`
- `hermes_cli/kanban_worktree_gc.py`
- `hermes_cli/kanban_ops.py`
- `hermes_cli/kanban_parser.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_scratch_gc_backoff.py`: `test_refused_tree_is_not_rearchived_until_backoff_passes`, `test_changed_tree_is_retried_at_once_and_success_clears_the_entry`, `test_backoff_doubles_to_a_cap`, `test_unreadable_state_is_empty_and_never_blocks`, `test_budget_defers_the_rest_oldest_first_and_pass_completes`, `test_cli_budget_flag_and_default`, `test_default_budget_leaves_headroom_below_the_wrapper_kill`.
- `tests/hermes_cli/test_kanban_scratch_custody_backoff.py`: `test_custody_refusal_backs_off_then_retries_a_changed_tree`, `test_spent_budget_skips_custody_before_any_board_read`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The budget cannot interrupt a tree already being archived, so the longest in-flight archive must fit in the headroom.
- Backoff state is host-local.
