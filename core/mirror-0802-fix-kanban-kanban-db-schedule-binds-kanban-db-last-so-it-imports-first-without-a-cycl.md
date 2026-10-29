# fix(kanban): kanban_db_schedule binds kanban_db last, so it imports first without a cycle [daveey]

Patch 0802, source `db733d2c56819b8700954c81bd24824bdf695787`.

## Behavior

fix(kanban): kanban_db_schedule binds kanban_db last, so it imports first without a cycle [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `from __future__ import annotations`
- `def schedule_task(`
- `+"""Every kanban_db_* split module imports first, in a fresh interpreter.`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> kanban_db_schedule imported kanban_db at the top while kanban_db imports schedule_task back from
> it, so importing the split module first raised a circular ImportError (release run 40717630046,
> e2e-upgrade core, test_every_shipped_module_imports_from_a_clean_first_party_graph). The other
> split modules already bind _kb at the bottom; this one now does too.
>
> Tests: tests/hermes_cli/test_kanban_db_split_imports.py imports every kanban_db_* module first in a
> fresh interpreter (red on kanban_db_schedule before this change; runs on every platform, unlike the
> Linux-only fresh-process e2e that caught it).
>
> Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Code

- `hermes_cli/kanban_db_schedule.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_db_split_imports.py`

New or changed test definitions present in the source diff:

- `test_split_module_imports_first`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
