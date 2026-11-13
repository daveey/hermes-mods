# Respawn guard resumes after a historical parent's exact-head approval

Patches: **3507**.

## Behavior
- A parked owner holding an open captured PR is resumed once (instead of held) when every awaited parent finished after the park with an explicit exact-head verdict, and every holding PR they do not cover was approved by name by a checkpointed historical parent at a full head equal to the PR's current head, confirmed by a fresh read.
- It stays held when the head moved, only a cached head matched, the head read fails, a verdict or full head is missing, a second captured PR is uncovered, the checkpoint is legacy (no awaited list), or the historical run was reopened, unclaimed or incomplete. Each approval covers only the PR it names.

## How it works
3507 adds `hermes_cli/kanban_dependency_review_historical.py`, called from `kanban_dependency_review.py` only after the existing `resumes_review` rule refuses. A PR named by an awaited parent but not captured by the checkpoint is ordering proof only, never coverage. Existing refusals (run binding, one continuation consumed by the next claim, crash/reclaim) are kept.

## Code
- `hermes_cli/kanban_dependency_review.py`
- `hermes_cli/kanban_dependency_review_historical.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_dependency_review_historical_cover.py`: `test_incident_resumes_exactly_once`, `test_incident_without_prerequisite_parent_resumes`, `test_head_moved_after_historical_approval_stays_held`, `test_head_moved_after_a_cached_match_stays_held`, `test_gh_failure_stays_held`, `test_awaited_parent_without_explicit_exact_head_verdict_stays_held`, `test_awaited_change_request_on_sibling_is_ordering_proof`, `test_historical_parent_must_approve_that_pr_at_a_full_head`, `test_historical_parent_reopened_after_park_does_not_cover`, `test_uncovered_second_captured_pr_stays_held`, `test_historical_parent_needs_verdict_and_head`, `test_unclaimed_historical_completion_does_not_cover`, `test_historical_run_that_did_not_complete_does_not_cover`, `test_unclaimed_awaited_completion_is_not_ordering_proof`, `test_legacy_checkpoint_without_awaited_list_stays_held`, `test_sibling_approval_of_unrelated_pr_still_approves_nothing`, `test_unparked_crash_after_review_is_not_a_handoff`, `test_each_approval_covers_only_the_pr_it_names`, `test_historical_approval_requires_unchanged_history`, `test_historical_approval_requires_a_full_sha`, `test_covers_needs_an_awaited_parent`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
The guard needs a live GitHub head read; a read failure holds the owner.
