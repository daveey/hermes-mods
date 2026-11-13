# Kanban move refuses to leave a host holding unpushed work

Patches: **3035**.

## Behavior
- Moving a card off this host is refused with `unpushed_state_here` when the card's trees here (workspace, owner-marked linked worktrees, worktrees named in the body, newest comment or newest checkpoint) are dirty or hold commits on no remote.
- `force` overrides, and the moved event records `left_unpushed_state`.
- Shared main checkouts merely mentioned in the body are skipped; a queued move whose matches include this host is not refused; a running card's own move is refused while its run goes on.

## How it works
3035 adds `card_trees_state` in `kanban_move_pool.py` and a check in `kanban_move.check()` that runs when no match is this host; a test fixture module adds a card-trees helper.

## Code
- `hermes_cli/kanban_move.py`
- `hermes_cli/kanban_move_pool.py`
- `tests/_fixtures/card_trees.py`
- `tests/conftest.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_move_unpushed.py`: `test_an_owner_marked_tree_with_an_unpushed_commit_keeps_the_card_here`, `test_a_dirty_owner_marked_tree_keeps_the_card_here`, `test_a_clean_tree_whose_commits_are_pushed_moves`, `test_a_tree_named_only_in_the_newest_comment_keeps_the_card_here`, `test_a_shared_main_checkout_the_body_mentions_is_not_the_cards_work`, `test_force_leaves_the_state_and_the_event_records_it`, `test_a_queued_move_whose_matches_include_this_host_is_not_refused`, `test_a_running_cards_own_move_is_refused_and_its_run_goes_on`, `test_the_cli_prints_the_refusal_code`.
- `tests/hermes_cli/test_kanban_pickup_stall_repin.py`: `test_the_alarms_run_leaves_a_card_whose_unpushed_tree_is_on_this_host`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
The CLI entry path is not itself named by a listed test.
