# CI review bot: retry failed review-status downloads

Patches: **2931**.

## Behavior
- A targeted review-comment refresh retries a failed review-status artifact download and then posts the completed snapshot.
- If it keeps failing, the comment is marked as a failed refresh and exactly one delayed re-refresh is queued; the re-refresh never queues another.
- A failed refresh keeps a completed snapshot of that head, or writes a failed note when none exists. The sweep shares the retry but writes no marker and queues nothing. A failed comment write is not marked as a failed refresh.

## How it works
2931 adds the retry and marker logic to `scripts/ci/batch_review_comments.py` and `live_comment.py`, and lets the `ci-review-comment` workflow queue one targeted re-refresh after a capped delay.


## Code
- `.github/workflows/ci-review-comment.yml`
- `scripts/ci/batch_review_comments.py`
- `scripts/ci/live_comment.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/ci/test_batch_review_comments.py`: `test_a_failed_comment_write_is_not_marked_as_a_failed_refresh`, `test_a_failed_refresh_keeps_a_completed_snapshot_of_that_head`, `test_a_failed_refresh_with_no_comment_for_that_head_writes_a_failed_note`, `test_a_targeted_refresh_retries_a_failed_download_and_posts_the_completed_snapshot`, `test_a_targeted_refresh_that_keeps_failing_marks_the_comment_and_queues_exactly_one_re_refresh`, `test_main_runs_a_dispatched_re_refresh_after_its_capped_delay`, `test_the_re_refresh_itself_never_queues_another`, `test_the_sweep_shares_the_retry_but_writes_no_marker_and_queues_nothing`, `test_workflow_can_queue_one_targeted_re_refresh`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
The workflow-level queueing is covered only by a test that the workflow can queue one targeted re-refresh; real GitHub runs are untested here.
