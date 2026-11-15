# Restart receipts and child-policy test isolation

Mirror batch 6; ordinals 4012, 4013.

## Behavior

Test-only changes independently fence submitter restart receipts and keep exec-based survival tests away from real account policy.

## How it works

Each receipt test changes one task/run/spawn fence while keeping the others valid. Survival child environment explicitly carries the supported temporary policy-root override because in-process monkeypatches do not cross exec; child startup verifies its resolution and prints stderr on failed readiness.

## Code

Source paths touched by this feature group (including regression fixtures):

- `tests/hermes_cli/test_kanban_outer_owner_restart_receipt.py`
- `tests/hermes_cli/test_kanban_restart_survival.py`

Mail entries in the ordered series:

- 4012: [`5f8dd018a45209efec3374457783d3a49b4fe34e`](patches/4012-daveey-Test-submitter-restart-receipt-fences-independently.patch)
- 4013: [`902711fb17becbd7116593ba8e8dbb47c6bdee2a`](patches/4013-daveey-isolate-restart-survival-child-account-policy.patch)

## Tests

- `tests/hermes_cli/test_kanban_outer_owner_restart_receipt.py`: changed task claim, missing current run, closed run, non-running task and missing spawned witness each return false and leave no worker-result receipt.
- `tests/hermes_cli/test_kanban_restart_survival.py`: the child asserts the isolated policy root before real claim/spawn flow; existing real-entrypoint teardown/deadline assertions remain intact.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

No runtime restart behavior or timeout is changed. A setup refusal is not evidence that restart survival itself failed.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.
