# fix(kanban): drop a finished run's identity at process start [daveey]

Patch 0949, source `28ebf9034b82bc38504b323ea7aa8433606938d9`.

## Behavior

fix(kanban): drop a finished run's identity at process start [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `+"""Drop a finished Kanban run's identity before plugins load. [daveey]`
- `def _default_to_chat(args) -> None:`
- `+"""A finished run's HERMES_KANBAN_TASK / HERMES_KANBAN_RUN_ID is not a worker. [daveey]`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A terminal app that outlived its worker keeps HERMES_KANBAN_TASK and
> HERMES_KANBAN_RUN_ID. Keep them only when that run still holds a live
> claim; an unreadable board is left alone.

## Code

- `hermes_cli/kanban_identity.py`
- `hermes_cli/main.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_stale_kanban_identity.py`

New or changed test definitions present in the source diff:

- `test_finished_run_is_dropped_and_not_a_worker`
- `test_live_run_is_kept_and_is_a_worker`
- `test_unreadable_board_keeps_the_identity`
- `test_run_for_a_different_task_is_dropped`
- `test_bare_task_without_a_run_id_is_dropped`
- `test_run_that_does_not_hold_a_claim_is_dropped`
- `test_noop_when_unset`
- `test_main_drops_identity_before_plugins_load`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
