# Isolated seeds for fake Board release matrices

Mirror ordinals: 4005.

## Behavior

Reduces repeated schema setup in fake-only release tests without dropping their cases or sharing mutable Board state.

## How it works

A module fixture performs one real create/push into FakeService, then retains a deep-copied Board template. Every case receives another deep copy and _leased installs it into a fresh fake service. Integration fixtures continue to use independent databases and real sync paths.

## Code

Test-only patch set; production implementation is unchanged. The regression files below are the changed code.

## Tests

- `tests/hermes_cli/test_kanban_sync_rejections.py`: New lease-run on/off isolation controls assert the template has no leases/runs and remains unchanged after copied lease grant/release, title mutation and change-list clearing. Existing release outcome, diagnostic sizing, nullish coercion, policy and fencing assertions are retained against copied seeds.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

Only fake release matrices use the seed. Sharing a connection, mutable template or service between cases would invalidate the isolation guarantee; this patch does not change the production Board protocol.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
