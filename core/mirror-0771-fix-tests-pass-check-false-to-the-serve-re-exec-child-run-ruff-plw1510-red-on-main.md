# fix(tests): pass check=False to the serve re-exec child run (ruff PLW1510 red on main) [daveey]

Patch 0771, source `f0845d0b7977c1d3864f979074d8e99cdc2681dd`.

## Behavior

fix(tests): pass check=False to the serve re-exec child run (ruff PLW1510 red on main) [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _run_serve(tmp_path, *, update_running: bool, new: str = NEW):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Code

- `tests/hermes_cli/test_serve_update_wait_reexec.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_serve_update_wait_reexec.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
