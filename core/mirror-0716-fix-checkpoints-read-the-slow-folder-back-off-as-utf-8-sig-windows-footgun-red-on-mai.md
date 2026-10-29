# fix(checkpoints): read the slow-folder back-off as utf-8-sig (Windows footgun red on main) [daveey]

Patch 0716, source `95568533b461bf9e1db4aa13b136d5033018cae8`.

## Behavior

fix(checkpoints): read the slow-folder back-off as utf-8-sig (Windows footgun red on main) [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `class TestStagingBudget:`
- `def _slow_file(store: Path) -> Path:`

## Code

- `tools/checkpoint_staging.py`

## Tests

Source test files changed by this commit:

- `tests/tools/test_checkpoint_manager.py`

New or changed test definitions present in the source diff:

- `test_slow_dir_backoff_file_with_bom_is_read`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
