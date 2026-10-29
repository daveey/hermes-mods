# test(delegation): wait generously for the fan-out completion event instead of a 5s deadline

Patch 0884, source `09251f94f2540d02206182733ca7c46cd749b886`.

## Behavior

test(delegation): wait generously for the fan-out completion event instead of a 5s deadline. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_multi_task_call_is_one_completion_unless_independent_completions(monkey`

## Code

- `tests/tools/test_async_delegation.py`

## Tests

Source test files changed by this commit:

- `tests/tools/test_async_delegation.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
