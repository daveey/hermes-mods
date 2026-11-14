# Loop accounting survives interrupted passes and thread churn

Patches: 3822.

## Behavior

Accounting closes a lease pass that never reached its beat as an error before opening the next pass, keeps counting after forked nested contexts, and avoids carrying exited-thread CPU into a reused native ID. Configuration failure must not end application boot.

## How it works

`HostLoopLease.held` closes an already-open span with `error=True` before starting another. Accounting clamps context depth to zero after fork resets. A thread-local exit marker appends the ended TID to a deque; under the accounting lock, folds move that thread's accumulated CPU into exited totals before recording later work, preventing native-ID reuse contamination.

`_setting` explicitly catches `ConfigBackendUnavailable` as well as ordinary exceptions because the former is a SystemExit-derived configuration failure. `start` obtains the interval before taking the shared lock, so slow configuration reads cannot block every accounting pass exit. Fork cleanup drops thread-local state and clears the exit queue.

## Code

- `hermes_cli/host_loop_lease.py`
- `hermes_cli/loop_accounting.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/hermes_cli/test_loop_accounting.py` (patch 3822).

- **3822:** Tests require two lease runs/one error after a missing beat, nonnegative fork depth with later outer CPU counted, no reused-TID inheritance while exited CPU is retained, config-unavailable defaults/start survival and interval lookup outside the accounting lock.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

Native-thread CPU observation is platform-dependent; live unaccounted CPU can be unavailable without procfs. Thread exit queues are bookkeeping, not a promise of immediate snapshots at every instruction. Catching the named configuration exit must not turn unrelated SystemExit conditions into silent success.

## Plugin?

No. These are core process/thread accounting and lease lifecycle boundaries.
