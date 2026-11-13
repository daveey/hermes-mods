# Kanban park and release guards: run-end lease and awaited-review head

Patches: **3380, 3421**.

## Behavior
- Run-end lease release is now tested for a real `needs_input` block (outcome `blocked`) and for a run end undone by a savepoint rollback (no release).
- An awaited-review release only trusts a PR head read at most 5 s old (`RELEASE_HEAD_MAX_AGE_SECONDS`), so a force-push after approval cannot match the approved sha from a stale cached read; a failed re-read holds the card. Mismatches may still come from the cache.
- Awaited-review checks use one `gh` read (`state,comments,headRefOid`) shared between the PR state cache and the head lookup.
- "Parked run" has a single definition (`latest_parked_run`: latest executed run, the assignee's, outcome scheduled/continued, `ended_at` set); wrapped or gateway/list wait-check forms are parsed strictly so they name no awaited card.

## How it works
- 3380 is test-only, covering the `_end_or_synthesize_run` release and the `ended_at` guard in `kanban_run_end_lease._release`.
- 3421 adds the re-read to `kanban_awaited_review.py`, records heads in `kanban_db_dispatch_pr_state._heads`, and centralizes the park definition in `kanban_pr_same_run_park.py`.

## Code
- `hermes_cli/kanban_awaited_review.py`
- `hermes_cli/kanban_db_dispatch_pr_state.py`
- `hermes_cli/kanban_pr_same_run_park.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_run_end_lease.py`: `test_a_human_block_releases_the_run_lease`, `test_a_savepoint_rolled_back_end_keeps_the_lease`.
- Across `tests/hermes_cli/test_kanban_active_pr_awaited_head.py`, `test_kanban_active_pr_awaited_review.py` and `test_kanban_park_invariants.py`: `test_force_push_after_a_cached_head_read_keeps_the_hold`, `test_approval_at_the_current_head_still_releases_through_real_gh`, `test_a_cold_release_costs_one_read`, `test_a_failed_release_reread_keeps_the_hold`, `test_a_cached_head_that_mismatches_refuses_without_a_new_read`, `test_pr_head_shares_pr_states_read`, `test_pr_state_reuses_pr_heads_read`, `test_same_second_tie_of_a_run_that_did_not_end_parked_still_guards`, `test_same_second_tie_needs_the_park_event_its_outcome_names`, `test_latest_parked_run_is_the_one_definition`, `test_wrapped_wait_check_names_no_awaited_card`, `test_gateway_request_and_list_awaiting_name_no_awaited_card`, `test_wrapped_probe_park_keeps_the_hold_after_an_approval`, `test_gateway_request_awaiting_park_keeps_the_hold_after_an_approval`, `test_rewritten_park_fixture_releases_with_the_bare_card_wait`, `test_a_wrapped_probe_parked_for_real_is_named_by_its_recorded_awaiting`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A release now costs at most one fresh `gh` read when the cache is older than 5 s.
