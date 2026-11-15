# Claim release compares lock and worker independently

Mirror ordinals: 3979.

## Behavior

Strengthens tests that a feed-triggered release cannot clear a newer claim. Production code is unchanged.

## How it works

The previous combined race is parameterized so the worker PID alone, claim lock alone or both can change between read and release. This includes a same-dispatcher respawn that retains the lock.

## Code

Test-only patch set; production implementation is unchanged. The regression files below are the changed code.

## Tests

- `tests/hermes_cli/test_kanban_release_claim.py`: release_on_feed returns False in every race and the complete persisted claim tuple remains byte-for-value unchanged.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

Matching only dispatcher/lock identity is insufficient when its worker has respawned; matching only PID is insufficient after lock replacement.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
