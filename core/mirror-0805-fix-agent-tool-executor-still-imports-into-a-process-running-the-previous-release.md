# fix(agent): tool_executor still imports into a process running the previous release [daveey]

Patch 0805, source `32c097c75e8d4370b4071227f92259bb7f75cee1`.

## Behavior

fix(agent): tool_executor still imports into a process running the previous release [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `from agent.display import (`
- `+"""agent.tool_executor imports into a process that cached the previous release's agent.interrupt_control.`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A long-lived process mid-update (dashboard in e2e-upgrade handoff, release run 40717630046)
> lazily loads HEAD's agent.tool_executor while its cached agent.interrupt_control predates
> interrupt_skip_wording (post-v2026.9.24). Fall back locally, like cron.unreachable_retry
> (66c42a1aa5). Test: tests/agent/test_tool_executor_mixed_version_import.py (red before).

## Code

- `agent/tool_executor.py`

## Tests

Source test files changed by this commit:

- `tests/agent/test_tool_executor_mixed_version_import.py`

New or changed test definitions present in the source diff:

- `test_tool_executor_imports_against_previous_release_interrupt_control`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
