# Profile-isolated boot recovery contracts

Mirror ordinals: 3954.

## Behavior

Adds regression coverage for pending-owner route binding and complete, profile-isolated stale-route pruning; no production code changes.

## How it works

Native recovery is fed queued, unknown and idle logical owners together. Pruning uses real SessionDB handles, ended sessions on both sides of the SQL chunk boundary, and identical session IDs with opposite end states in two profiles in both insertion orders.

## Code

Test-only patch set; production implementation is unchanged. The regression files below are the changed code.

## Tests

- `tests/gateway/test_boot_recovery_off_loop.py`: Asserts only pending owners request adapters and bind routes; both chunks lose ended entries but keep live/missing routes; each profile retains its own live shared ID regardless of insertion order.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

The profile resolver seam supplies owned database handles; tests exercise actual batched queries, not live gateway startup.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
