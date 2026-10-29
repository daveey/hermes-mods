# fix(kanban): dispatch output names cards this machine cannot lease and deferred claims [daveey]

Patch 0868, source `b232537d0a5512be508e735fc0aafef575ef21f6`.

## Behavior

fix(kanban): dispatch output names cards this machine cannot lease and deferred claims [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _cmd_dispatch(args: argparse.Namespace) -> int:`
- `def test_dry_run_never_lists_a_card_this_machine_cannot_lease(gws):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Follow-up to c44335f774: `hermes kanban dispatch [--dry-run] [--json]` now prints skipped_ineligible and
> claim_deferred (task id + Board reason), so a machine can read back that a card whose runs_on excludes it is
> skipped, not spawned (the MacBook readback for the 10-28 review-lane stall).
>
> Test: test_cli_dispatch_dry_run_json_names_ineligible_cards.

## Code

- `hermes_cli/kanban_ops.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_runs_on_dispatch.py`

New or changed test definitions present in the source diff:

- `test_cli_dispatch_dry_run_json_names_ineligible_cards`
- `test_health_probe_does_not_count_ineligible_cards`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
