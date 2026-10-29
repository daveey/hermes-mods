# fix(cron): unreachable_retry still imports into a scheduler running the previous release [daveey]

Patch 0804, source `66c42a1aa552f5e6e98e0a3f853ff9887613c7f1`.

## Behavior

fix(cron): unreachable_retry still imports into a scheduler running the previous release [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `import logging`
- `+"""cron.unreachable_retry imports into a scheduler still running the previous release.`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The previous release's scheduler imports cron.unreachable_retry lazily (will_retry) after an
> update swapped the tree; 6f88751a7e made that module import is_recurring from cron.constants,
> which the old process already holds without it. The ImportError lost the run's bookkeeping and
> the one-shot job ran twice (release run 40717630046, e2e-upgrade handoff,
> test_cron_job_due_mid_update_runs_exactly_once). Fall back to a local is_recurring.
>
> Tests: tests/cron/test_unreachable_retry_mixed_version_import.py (red before).
>
> Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Code

- `cron/unreachable_retry.py`

## Tests

Source test files changed by this commit:

- `tests/cron/test_unreachable_retry_mixed_version_import.py`

New or changed test definitions present in the source diff:

- `test_imports_under_an_older_in_memory_cron_constants`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
