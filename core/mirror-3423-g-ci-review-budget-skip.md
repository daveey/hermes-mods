# CI review comments: refresh budget and budget-skip marking

Patches: **3310, 3323**.

## Behavior
- A manual CI-review dispatch asking for a long refresh delay no longer spends the whole run budget before its first download attempt: the delay cap drops from 600 s to 480 s and the headroom test requires two full attempts.
- A targeted run whose budget gate skips a PR marks it "refresh failed on <head>"; the sweep still writes nothing.
- The budget-skip log line is worded per mode, and a CI-triggered targeted run skipped by the gate queues one re-refresh.

## How it works
- 3310 changes `MAX_REFRESH_DELAY_S`, the workflow, and `run_batch` in `scripts/ci/batch_review_comments.py`.
- 3323 adds the CI-triggered marker test and the per-mode log wording.

## Code
- `.github/workflows/ci-review-comment.yml`
- `scripts/ci/batch_review_comments.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/ci/test_batch_review_comments.py`: `test_attempt_cost_matches_the_download_timeouts`, `test_a_dispatch_at_the_capped_delay_still_refreshes_the_pr`, `test_a_targeted_run_skipped_by_the_budget_gate_marks_the_comment_failed`, `test_the_sweep_skipped_by_the_budget_gate_writes_no_marker`, `test_a_ci_triggered_run_skipped_by_the_budget_gate_queues_one_re_refresh`, `test_budget_skip_log_line_is_worded_per_mode`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
