# test(tui): assert refused-turn workers finish without errors [daveey]

Patch 0834, source `eae9120eebaa060c10702b0a17a83a544326e54e`.

## Behavior

test(tui): assert refused-turn workers finish without errors [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_refused_input_commits_failed_mailbox_receipt(tmp_path):`

## Code

- `tests/tui_gateway/test_bot_live_owner_delivery.py`

## Tests

Source test files changed by this commit:

- `tests/tui_gateway/test_bot_live_owner_delivery.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
