# fix(kanban): [daveey] dispatch CLI shows at_capacity instead of a silent 'Spawned: 0' (#140)

Patch 0726, source `9920ee6bca5d54a764d811299c5434a40c8276d7`.

## Behavior

fix(kanban): [daveey] dispatch CLI shows at_capacity instead of a silent 'Spawned: 0' (#140). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _cmd_dispatch(args: argparse.Namespace) -> int:`
- `def test_cli_daemon_does_not_warn_stuck_when_cap_is_full(kanban_home, monkeypatc`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> * fix(kanban): [daveey] dispatch CLI names a full concurrency cap (at_capacity) instead of a bare 'Spawned: 0'
>
> * fix(update): [daveey] read .git gitdir marker with utf-8-sig (Windows footguns gate, red on main since 2aa3397018)

## Code

- `hermes_cli/kanban_ops.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_host_cap.py`

New or changed test definitions present in the source diff:

- `test_cli_dispatch_text_names_full_host_cap`
- `test_cli_dispatch_json_names_full_host_cap`
- `test_cli_dispatch_text_omits_capacity_line_under_cap`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
