# fix(kanban): use portable notice-holder liveness probe [daveey]

Patch 0799, source `2ebff86d104e453248c1ae1f7d19c8d8b78d0ee6`.

## Behavior

fix(kanban): use portable notice-holder liveness probe [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _holder_is_me(row) -> bool:`
- `+"""Notice holder liveness uses the portable probe and preserves PID-reuse protection."""`

## Code

- `hermes_cli/kanban_db_notify.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_notify_holder.py`

New or changed test definitions present in the source diff:

- `test_holder_liveness_does_not_signal_processes`
- `test_holder_liveness_with_real_alive_and_reaped_process`
- `test_invalid_holder_is_not_alive`
- `test_holder_uses_portable_liveness_and_start_fingerprint`
- `test_holder_probe_errors_preserve_conservative_ownership`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
