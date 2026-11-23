# Checkpoint pruning off the housekeeping thread

Source patch ordinals: 5765.

## Behavior

Daily checkpoint pruning no longer occupies the gateway housekeeping thread. The tick requests a coalesced pass on a dedicated daemon worker, allowing unrelated heartbeat and delivery chores to continue.

## How it works

`CheckpointPruneWorker` starts lazily using `process_ownership.start_writer`, copies the requester's context, and runs each pass through the existing `run_chore` admission/lease boundary. Requests during a pass coalesce into at most one follow-up. `close` prevents future passes and wakes an idle worker; an already-admitted prune is not interrupted. The original `checkpoints.auto_prune` and daily marker still decide whether maintenance is due.

## Code

- `gateway/run_checkpoint_prune.py`
- `gateway/run.py`
- `gateway/run_runtime_init.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/gateway/test_checkpoint_prune_housekeeping.py` covers a blocked prune with continuing heartbeat/delivery drain, single-flight coalescing, housekeeping lease custody, failure survival, daemon-thread identity, close/stop behavior and copied profile/home context.

## Applying

Apply worker and housekeeping wiring together. Existing checkpoint retention settings remain the control surface; no new pruning policy is introduced.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

Closing the worker is not cancellation of a running `git gc`. Runtime ownership must continue accounting for a surviving worker. Offloading removes housekeeping blockage, not the pruning CPU or I/O cost.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
