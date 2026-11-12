# Hourly CI review comments and flaky-poll deadline

Patches: **2351, 2381–2385**.

## Behavior

`ci-review-comment.yml` now runs hourly and on manual dispatch: one batch snapshots every open same-repository PR using its newest exact-head CI `pull_request` run and posts one comment each, replacing per-run pollers. It runs trusted default-branch scripts only, never executes PR code and skips forks. Incomplete, cancelled or artifact-error snapshots never overwrite a comment and the ship-footer contract is preserved. Separately `flaky_kanban.py` saves state after every polled run and stops starting runs after a budget (420 s), reporting `FAILED poll_budget deferred=<n>` so the next poll resumes.

## How it works

`batch_review_comments.py` selects eligible PRs and runs, re-reads the PR just before writing (head race), isolates per-PR errors and exits nonzero on list failure. `live_comment.py` validates collection-time conclusions and decodes artifacts portably. `flaky_kanban.main` checkpoints per run and defers unstarted runs behind a deadline.

## Code

`.github/workflows/ci-review-comment.yml`, `.github/workflows/README.md`, `scripts/ci/batch_review_comments.py`, `live_comment.py`, `flaky_kanban.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/ci/test_batch_review_comments.py` (including workflow shape) and `tests/ci/test_flaky_poll_budget.py`. The live GitHub workflow itself was not run.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Comments can lag by up to an hour. A deferred-run poll deliberately fails the cron so it stays red until caught up.

## Plugin

CI tooling; no Hermes plugin.
