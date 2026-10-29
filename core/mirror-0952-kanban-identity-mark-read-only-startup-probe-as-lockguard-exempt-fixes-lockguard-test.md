# [daveey] kanban_identity: mark read-only startup probe as lockguard-exempt (fixes lockguard test)

Patch 0952, source `a83962fbccc3e80d8c87b76d1f0eaf31e844acad`.

## Behavior

[daveey] kanban_identity: mark read-only startup probe as lockguard-exempt (fixes lockguard test). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _connect(db: Path) -> sqlite3.Connection:`

## Code

- `hermes_cli/kanban_identity.py`

## Tests

No test file changes in this source commit. Runtime behavior is untested by this mirror unless an executed check is named in [mirror-958-verification.md](mirror-958-verification.md); full-series application is a structural check, not a behavior test.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
