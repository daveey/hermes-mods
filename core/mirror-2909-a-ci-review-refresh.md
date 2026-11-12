# Exact-head CI review comment refresh

Patches: **2727**.

Related context: [ci-review-batch-and-flaky-budget-2409b.md](ci-review-batch-and-flaky-budget-2409b.md).

## Behavior

A completed CI run on a ship worktree branch refreshes only the open same-repository PR at that exact head, rather than waiting for the hourly review-comment sweep. The hourly schedule and manual dispatch remain.

## How it works

The workflow adds `workflow_run` for completed `CI` runs on `wt/**`, restricted to same-repository `pull_request` runs with success/failure conclusions. It checks out trusted default-branch code. Only the validated full 40-hex head SHA passes into `TARGET_HEAD_SHA`; `batch_review_comments.py --head-sha` filters the batch and a nonmatching head is a no-op. Per-head concurrency groups avoid cancelling unrelated pending head refreshes.

## Code

- `.github/workflows/README.md`
- `.github/workflows/ci-review-comment.yml`
- `scripts/ci/batch_review_comments.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/ci/test_batch_review_comments.py`: `test_ci_completion_on_a_ship_head_triggers_a_targeted_refresh`, `test_targeted_run_refreshes_only_the_pr_at_that_head`, `test_targeted_run_without_an_open_pr_at_that_head_writes_nothing`, `test_main_targets_the_completed_runs_head_from_the_workflow_env`, `test_main_refuses_a_target_that_is_not_a_full_sha`.

Coverage is limited to the entry points and fixture controls above; live services, deployment and user-interface behavior are not established by this documentation task.

## Applying

Use the [README full replay procedure](README.md#applying). Apply these patches in published ordinal order with the intervening series, replay bridges and mandatory post-replay repair described there. They are incremental changes, not independently certified cherry-picks. This document does not claim that the full mirror replay or feature suites ran during the documentation pass.

## Gotchas

A targeted refresh does not execute PR code or broaden eligibility to forks. It does not guarantee that every sibling workflow has finished; the source change relies on the sampled Docker runs finishing before CI. The old document's hour-long lag still applies to cases outside the new trigger. Live GitHub Actions execution and actionlint were not run by this mirror documentation pass.

## Plugin status

Core/CI implementation; no plugin is added or required. A plugin cannot replace the shared database, admission, worker or workflow invariants documented here.
