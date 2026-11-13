# CI review comment: retry budget, re-refresh queueing, budget pins

Patches: **3200, 3201, 3231**.

## Behavior
- The batch review-comment sweep uses a wall-clock retry budget that leaves headroom under the job timeout; a large sweep during an artifact outage stops before the job timeout, while a healthy sweep is not cut short.
- At most one re-refresh is queued per head: a failed refresh that keeps a completed snapshot queues none, CI and Docker failures on one head queue one, and a new head or a successful refresh clears the mark. A marker skipped for a moved head queues nothing.
- The re-refresh's own failed footer points at the hourly sweep (3201).
- No artifact download starts once the budget cannot fit it; strict mode refuses an artifact the budget cannot fit after a good one (3231).

## How it works
- 3200 reworks `batch_review_comments.py` (attempt cost tied to download timeouts, budget checks, queued-mark logic), updates `live_comment.py` and the workflow file.
- 3201 changes the footer text for the re-refresh failure.
- 3231 only adds tests for the pre-download budget check.

## Code
- `scripts/ci/batch_review_comments.py`
- `scripts/ci/live_comment.py`
- `.github/workflows/ci-review-comment.yml`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/ci/test_batch_review_comments.py`: `test_attempt_cost_matches_the_download_timeouts`, `test_a_delayed_re_refresh_stops_retrying_at_the_wall_clock_budget`, `test_the_run_budget_leaves_headroom_under_the_job_timeout`, `test_a_large_sweep_in_an_artifact_outage_stops_before_the_job_timeout`, `test_a_healthy_sweep_is_not_cut_short`, `test_a_failed_refresh_that_keeps_a_completed_snapshot_queues_no_re_refresh`, `test_ci_and_docker_failures_on_one_head_queue_one_re_refresh`, `test_a_new_head_or_a_successful_refresh_clears_the_queued_mark`, `test_a_marker_skipped_for_a_moved_head_queues_nothing`, `test_no_artifact_download_starts_once_the_budget_cannot_fit_it`, `test_strict_refuses_an_artifact_the_budget_cannot_fit_after_a_good_one`.
- The workflow YAML change is untested.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
