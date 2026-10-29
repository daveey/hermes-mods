# fix(kanban): pin refusal names the cross-gateway route that works from this gateway [daveey]

Patch 0940, source `2bb1d3298bd3527c63a21ca9d3fd36ad0659915f`.

## Behavior

fix(kanban): pin refusal names the cross-gateway route that works from this gateway [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `class MachinePinRefused(Exception):  # not ValueError: label-syntax handlers mus`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> placement=<machine> only reaches the gateway's routed target (on the hub there is
> no route, so placement='macbook' is refused); hermes-hub create-task <machine>
> works from any gateway (live: t_01a186cf60392092 claimed and completed on
> David-MBP 8 s after a hub create).

## Code

- `hermes_cli/kanban_worker_node.py`

## Tests

No test file changes in this source commit. Runtime behavior is untested by this mirror unless an executed check is named in [mirror-958-verification.md](mirror-958-verification.md); full-series application is a structural check, not a behavior test.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
