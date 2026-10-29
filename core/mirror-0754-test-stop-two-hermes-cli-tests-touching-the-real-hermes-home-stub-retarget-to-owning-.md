# test: stop two hermes_cli tests touching the real Hermes home (stub retarget_to_owning_install; stub node instead of symlinking PM's tool store)

Patch 0754, source `15b490715145ca98f103b0b6be1c136a34ab1356`.

## Behavior

test: stop two hermes_cli tests touching the real Hermes home (stub retarget_to_owning_install; stub node instead of symlinking PM's tool store). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_runtime_node_comes_from_pm_without_legacy_repair(tmp_path, monkeypatch)`
- `def test_command_reports_outcome_and_releases_real_lock(tmp_path, monkeypatch, f`

## Code

- `tests/hermes_cli/test_tui_npm_install.py`
- `tests/hermes_cli/test_update_products.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_tui_npm_install.py`
- `tests/hermes_cli/test_update_products.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
