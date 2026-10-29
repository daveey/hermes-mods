# ci(e2e-upgrade): split suites into <=4-file shards and run one worker per slot CPU [daveey]

Patch 0955, source `e8d99e7a39712e546a211f635407a3b829ed33fb`.

## Behavior

ci(e2e-upgrade): split suites into <=4-file shards and run one worker per slot CPU [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `jobs:`
- `def test_run_tests_forwards_the_strict_switch():`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The git suite (9 files) ran as one shard at HERMES_TEST_WORKERS=6 on a 4-CPU bwrap slot; under load its
> own deadlines fired (git fetch 300 s watchdog, 30 s paused-gateway restart, the lock driver's update
> timeout) and three files went FLAKY, failing release run 40821597683. The plan now splits any suite
> over 4 files into <suite>-<i>of<n> shards (every n-th file), and the run step sizes workers to nproc
> (cap 6) instead of a fixed 6.
>
> Tests: test_e2e_upgrade_shards_cover_every_file_once_and_fit_one_slot replays the plan and every
> shard: each <=4 files, every upgrade test file exactly once (red before: core ran 7 files).
>
> Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Code

- `.github/workflows/tests.yml`

## Tests

Source test files changed by this commit:

- `tests/ci/test_update_ci_routing.py`

New or changed test definitions present in the source diff:

- `test_e2e_upgrade_shards_cover_every_file_once_and_fit_one_slot`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
