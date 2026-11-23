# Gateway busy count: reconcile the persisted count on heartbeat and deferred-worker release

Mirror snapshot 5923; ordinal 5874.

Entries:

- **5874**: Reconcile persisted gateway busy count on heartbeat and deferred release

## Behavior

- Idle probes read `active_agents` from the gateway runtime status file. That count was published at turn boundaries only, so a deferred executor worker finishing later left the file busy while the loop was idle, and the thread heartbeat refreshed `updated_at` without refreshing the count.
- Now the count is republished right after a deferred worker is released (result, exception or cancellation), and every housekeeping heartbeat requests one coalesced recount on the owner event loop.
- The thread-side status stamp stays independent of the loop, so a wedged loop cannot stop freshness publication; it stamps, then requests at most one pending recount. If the loop is closed during shutdown the request is dropped and retried on a later tick; with no runner or loop the heartbeat still stamps.

## How it works

- `gateway/run_runtime_heartbeat.py` (new): `runtime_heartbeat(runner, loop)` returns the tick: write the quiet runtime status, then `loop.call_soon_threadsafe(refresh)` guarded by a `threading.Event` so a wedged loop holds at most one callback; `refresh` calls `runner._persist_active_agents()`. It never inspects authority state on the housekeeping thread.
- `gateway/run_deferred_workers.py` (new): `discard_deferred_worker` pops the worker, consumes its exception so asyncio does not warn, then calls `_persist_active_agents()` after release. The old inline callback in `gateway/run_shutdown.py` now delegates to it.
- `gateway/run.py` wires the heartbeat entry ("Runtime heartbeat") to `runtime_heartbeat(runner, loop)`.

## Code

Shipped non-test paths:

- `gateway/run.py`
- `gateway/run_deferred_workers.py` (new)
- `gateway/run_runtime_heartbeat.py` (new)
- `gateway/run_shutdown.py`

## Tests

Source-side test shipped by this entry; not executed by this documentation pass, so no pass result is claimed here:

- `tests/gateway/test_live_busy_count.py` (deferred completion republishes the live count for result/exception/cancel; heartbeat converges on the loop, not the housekeeping thread; a wedged loop keeps the thread stamp and coalesces; heartbeat without loop or runner still stamps; closed loop and failed refresh re-arm)

## Applying

These entries are part of the ordered snapshot 5923. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying) (`git am -3 --empty=keep` over the full ordered list, plus any named repair overlay). They are not independent patches.

## Gotchas

- Convergence is bounded by the heartbeat interval; the file can still lag between ticks.
- The refresh must stay on the owner loop; calling it from the heartbeat thread would sample live state off-loop.
- The source notes that live verification on a deployed gateway was still pending when the change landed.

## Plugin

Core change; no plugin or hook is involved.
