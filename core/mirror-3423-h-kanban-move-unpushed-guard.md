# Kanban move unpushed-work guard follow-ups

Patches: **3362–3364, 3404**.

## Behavior
- The guard that stops a card from leaving this machine while it has unpushed or dirty work now also scans the move's own checkpoint text (written after the check ran).
- A worktree named only in text but owned by a different card's marker is skipped, so a reviewer card naming an implementer's tree no longer keeps itself pinned.
- Total git time spent inspecting trees is bounded; past the budget the tree reads as "unchecked" and still counts as state (fail safe).
- A worker-first card's queue re-pin to the worker queue is refused when this machine holds unpushed state.
- When the budget is spent or a git call times out, the cause text says "unchecked worktree" rather than "unpushed commits"; the card is still kept (only the wording changes).
- A queue re-pin whose matches still include this machine is not "leaving" and is unguarded (documented as intentional); an unreadable registry counts as leaving.

## How it works
- 3362 makes `check()`/`_card_text` scan the move's checkpoint, adds an owner-marker test for text-named trees, adds a `STATE_BUDGET_S` budget with shrinking per-call git timeouts, and applies the unpushed guard to `move_queued`'s repin.
- 3363 updates the queue-repin follow-up tests to stub the Board registry that pool eligibility now reads.
- 3364 lets the pool-queue unpushed test stub accept the `timeout` keyword added by the deadline-bounded git calls.
- 3404 changes `_BudgetSpent` from a `ValueError` subclass to a plain `Exception` so it is not swallowed inside `pushed_on_remote`, reports "unchecked" when a dirty/unpushed check returned True only because git timed out, and shares one repin leaving rule.

## Code
- `hermes_cli/kanban_move.py`
- `hermes_cli/kanban_move_pool.py`
- `hermes_cli/kanban_worker_repin.py`
- `hermes_cli/worktree_ops.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_move_unpushed_followups.py`: `test_a_tree_named_only_in_the_queued_moves_checkpoint_keeps_the_card_here`, `test_a_tree_named_only_in_the_running_moves_checkpoint_keeps_the_card_here`, `test_a_text_named_tree_owned_by_another_card_does_not_keep_this_card`, `test_a_text_named_tree_owned_by_the_card_itself_still_counts`, `test_git_time_for_many_slow_trees_is_bounded_and_fails_safe`, `test_a_fast_scan_within_the_budget_reads_clean`, `test_a_started_worker_first_cards_queue_repin_is_refused_on_unpushed_state`, `test_a_clean_worker_first_cards_queue_repin_reaches_the_board`.
- `tests/hermes_cli/test_kanban_move_guard_review_nits.py`: `test_a_budget_spent_inside_pushed_on_remote_reads_unchecked`, `test_a_remote_timeout_that_spends_the_budget_reads_unchecked`, `test_a_git_timeout_in_the_dirty_check_reads_unchecked_not_dirty`, `test_seen_state_within_the_budget_keeps_its_cause`, `test_a_queue_repin_whose_matches_include_this_machine_is_unguarded`, `test_a_queue_repin_that_leaves_this_machine_stays_guarded`, `test_an_unreadable_registry_counts_the_queue_repin_as_leaving`, `test_a_park_naming_a_tree_pins_only_when_the_tree_is_this_cards`.
- 3364 only adjusts an existing stub in `tests/hermes_cli/test_kanban_pool_queue.py`; it adds no new test names.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Every degraded case (timeout, spent budget) still counts as state; only the reported cause differs.
