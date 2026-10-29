# fix(tests): [daveey] hostile-commit e2e frees each per-test install so the git shard fits its CI slot

Patch 0724, source `0f838d6b6583fe8e7bdf9f90c696819958864c5b`.

## Behavior

fix(tests): [daveey] hostile-commit e2e frees each per-test install so the git shard fits its CI slot. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `real git failure, and then judges the next real launch from the files the update`
- `def provider():`

## Code

- `tests/e2e/core/upgrade/git/test_hostile_commit.py`

## Tests

Source test files changed by this commit:

- `tests/e2e/core/upgrade/git/test_hostile_commit.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
