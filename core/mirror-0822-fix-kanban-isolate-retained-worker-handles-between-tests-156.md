# fix(kanban): isolate retained worker handles between tests [daveey] (#156)

Patch 0822, source `a0ebd73b9c91b6a69f133898179c059ba592b4ee`.

## Behavior

fix(kanban): isolate retained worker handles between tests [daveey] (#156). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `from __future__ import annotations`
- `def _spawn_exit(code: int) -> subprocess.Popen:`

## Code

- `tests/hermes_cli/conftest.py`
- `tests/hermes_cli/test_kanban_worker_exit_decode.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/conftest.py`
- `tests/hermes_cli/test_kanban_worker_exit_decode.py`

New or changed test definitions present in the source diff:

- `test_reaper_retains_running_worker_and_records_exit_once`
- `test_reaper_records_signaled_worker`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
