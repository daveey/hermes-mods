# Kanban PR-owner holds, handoffs and review release

Patches: **3266–3267, 3277–3278**.

## Behavior
- `hermes kanban resume` on the hub accepts a runner-only `active_pr` hold (a replicated `respawn_guarded` event) after any latest lifecycle transition, not just a fixed list; any later transition still invalidates an earlier observation; a worker's hold recorded after a reassign is accepted.
- A PR owner is released (not left READY with no worker) when it handed off in the same second as its newest PR comment: the tie counts only when every PR comment at that second has exactly one commented event written by the run that parked, ordered before the park, and that run is the assignee's latest executed run. A later comment, an earlier run's comment, an unprovable order, an unreadable event, a crash without park, or a non-assignee run still guard.
- A card parked awaiting an exact-head review resumes once when the review approved at head; a non-approval still guards.
- Review release skips held cards (sticky stop, blocked/triage card at the failure breaker) before any GitHub read or snapshot. A real worker-hold-fed home lets home resume and worker claim proceed.

## How it works
- 3277 loosens `observed()` in `kanban_observed_pr_hold`. 3278 adds `kanban_pr_same_run_park` and `kanban_awaited_review` and wires them into the PR-state dispatch check. 3266 makes `prepare()` apply `recompute_ready`'s skips first via a shared `kanban_block_routing.recovery_held`, and uses `fetchone` in `released()`. 3267 adds a comment that review-release stamp authority depends on `task_events.origin_gateway`.

## Code
- `hermes_cli/kanban_observed_pr_hold.py`
- `hermes_cli/kanban_db_dispatch_pr_state.py`
- `hermes_cli/kanban_pr_same_run_park.py`
- `hermes_cli/kanban_awaited_review.py`
- `hermes_cli/kanban_review_release.py`
- `hermes_cli/kanban_block_routing.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_sync.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_observed_pr_hold_boundaries.py`: `test_a_hold_seen_after_any_latest_transition_counts`.
- `tests/hermes_cli/test_kanban_operator_resume_observed.py`: `test_observation_after_any_latest_transition_allows_home_resume`, `test_any_later_transition_invalidates_an_earlier_observation`.
- `tests/hermes_cli/test_kanban_operator_resume_worker_copy.py`: `test_home_resume_accepts_a_worker_hold_recorded_after_a_reassign`.
- `tests/hermes_cli/test_kanban_active_pr_awaited_review.py`: `test_same_second_report_then_park_by_the_same_run_resumes_once`, `test_same_second_pr_comment_after_the_park_still_guards`, `test_same_second_pr_comment_from_an_earlier_run_still_guards`, `test_same_second_tie_with_unprovable_order_still_guards`, `test_unreadable_commented_event_keeps_the_tie_guarded`, `test_older_pr_comment_of_an_earlier_run_does_not_block_the_same_second_handoff`, `test_park_in_an_earlier_second_than_the_new_pr_comment_still_guards`, `test_same_second_report_then_crash_without_park_still_guards`, `test_same_second_park_of_a_non_assignee_run_still_guards`, `test_awaited_review_approved_at_head_resumes_once`, `test_awaited_review_fixture_holds_without_the_review`, `test_awaited_review_that_is_not_an_approval_at_head_still_guards`.
- `tests/hermes_cli/test_kanban_operator_resume_observed_feed.py`: `test_real_worker_hold_fed_home_lets_home_resume_and_worker_claim`.
- `tests/hermes_cli/test_kanban_review_release.py`: `test_held_card_gets_no_external_evaluation`.
- 3267 is a comment only; `tests/hermes_cli/test_kanban_dependency_pr_handoff.py` and `test_kanban_pr_continuation.py` were adjusted by 3278 (no new test names read).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A same-second tie is released only when provable; otherwise the guard keeps holding, so an operator may still need to release such owners.
