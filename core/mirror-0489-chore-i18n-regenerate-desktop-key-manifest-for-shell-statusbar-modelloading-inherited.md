# chore(i18n): regenerate desktop key manifest for shell.statusbar.modelLoading (inherited from main 3232b40b24)

Patch 0489, source `f845189101e7c878bc1b8e8a7d130b743421af94`.

## Behavior

chore(i18n): regenerate desktop key manifest for shell.statusbar.modelLoading (inherited from main 3232b40b24). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `"shell.statusbar.hideTerminal",`

## Code

- `locales/_keys.desktop.json`

## Tests

No test file changes in this source commit. Runtime behavior is untested by this mirror unless an executed check is named in [mirror-958-verification.md](mirror-958-verification.md); full-series application is a structural check, not a behavior test.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
