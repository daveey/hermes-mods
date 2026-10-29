# fix(tests): pm launch tests ignore a supervisor's child marker [daveey]

Patch 0719, source `76ff5aa9fa5ea188dcd3126cffd6cd4fe07c3a47`.

## Behavior

fix(tests): pm launch tests ignore a supervisor's child marker [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def source_launch(tmp_path, monkeypatch, isolated_python):`

## Code

- `tests/pm/test_source_update_launch.py`

## Tests

Source test files changed by this commit:

- `tests/pm/test_source_update_launch.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
