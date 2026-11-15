# Update checkpoint intent reread contracts

Mirror ordinals: 3956.

## Behavior

Adds tests requiring strict Boolean cron_cut and fresh checkpoint intent reads between restart polls. No production updater changes.

## How it works

Cards-only work reaches the checkpoint predicate without cron counting. The same runner and intent Path are reused while the file changes in both directions, preventing a cached first-read result from passing.

## Code

Test-only patch set; production implementation is unchanged. The regression files below are the changed code.

## Tests

- `tests/gateway/test_instant_update_cron_drain_latency.py`: True activates the short checkpoint wait; integer 1 and string true do not. Both checkpoint transitions alter the second-poll result, while the ordinary long wait still expires.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

Truthy values are not interchangeable with a literal Boolean in this persisted intent contract.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.
