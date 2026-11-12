# Profile stop uses the process protocol for retained managed handles

Patches: **2482**.

## Behavior

When a gateway profile stops, a managed-worker handle that is only retained through session adoption (already removed from the managed-worker registry, generation not yet settled) is now stopped with the worker's own `control({'type': 'stop'})` message, once. Before, it fell into the in-process path and received `request_hard_interrupt(agent, ...)`, which a process handle does not accept. Ordinary in-process agents keep the hard interrupt; a handle already closed or already stopped is not signalled again.

## How it works

`gateway/run_runtime.py` factors the stop into `_stop_managed_handle(worker, reason)`: skip if `worker.closed` or `worker.stop` is set, record `worker.stop_cause = reason`, send the stop control, and tolerate a full control queue (the latch set by `control()` lets supervision escalate). `stop_authority_turns` uses it for registered workers and, in the in-process branch, for adopted agents that are `ManagedWorker` instances. A still-spawning worker continues to use the existing latch (`_stop_spawning_managed`).

## Code

`gateway/run_runtime.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/gateway/test_profile_stop_managed_handle.py` (one stop for a live handle, no repeat for a settling handle, an old adoption does not stop the old worker, ordinary agent keeps hard interrupt, queue backpressure still latches stop, profile stop joins and clears the adoption). Only the profile-stop path is covered.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Stopping is by identity of the handle class (`ManagedWorker`), not by attribute probing, so other handle types still take the in-process interrupt.
- The stop cause is recorded only on a worker that was not already stopped, so a user's earlier Stop keeps its own cause.

## Plugin

Core change; no plugin required.
