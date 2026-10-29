# fix(tests): stop subagent handoff flake from racing a 0.4s sleep [daveey]

Patch 0828, source `4d7c25e63dd1763f8694c869dc74586ebf0a119c`.

## Behavior

fix(tests): stop subagent handoff flake from racing a 0.4s sleep [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `named on the child's result as orphaned before teardown kills it.`
- `def test_handed_off_process_completion_reaches_parent_and_leftover_is_reported(c`
- `def test_handoff_refuses_exited_foreign_or_non_child_callers(clean_queue):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The handed-off child was `sleep 0.4`. On a CPU-throttled runner that sleep
> exits on wall clock while the test is still spawning the sibling, so
> transfer_ownership returns None and the tool result is an error dict —
> KeyError: 'status' on the first CI attempt, pass on retry. Hold the child
> on a sentinel until after handoff, then wait on the completion event
> (returns as soon as the reader publishes; does not consume the parent drain).

## Code

- `tests/tools/test_subagent_process_handoff.py`

## Tests

Source test files changed by this commit:

- `tests/tools/test_subagent_process_handoff.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
