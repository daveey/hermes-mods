# test(kanban): a local archive closes as closed_uncompleted, not unknown [daveey]

Patch 0797, source `9b6863cd14567fe1b0b78dd1cca77954557c2427`.

## Behavior

test(kanban): a local archive closes as closed_uncompleted, not unknown [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_local_unverified_terminal_or_binding_never_releases(world, board, monke`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> 44f3ca1e39 made an owner-observed archive close the origin obligation as
> closed_uncompleted: not unfinished, and never released or completed. The
> local projection test from b47371bdf8 still expected archive to project
> unknown, so main was red. A live local archive is not verified completion
> evidence; it is a current owner close. Binding faults and a raw done status
> still project unknown, and the ledger released flag stays 0.

## Code

- `tests/hermes_cli/test_kanban_local_origin_projection.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_local_origin_projection.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
