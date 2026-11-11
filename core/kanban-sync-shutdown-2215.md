# Stop replica pagination at shutdown boundaries

Patch **2211**.

## Behavior

Kanban replica feed pulls and snapshot resyncs stop requesting pages when the gateway stops or yields its sync lease. A page fetched during shutdown is not newly applied after the stop check. Cooperative shutdown is recorded as a normal stopped state rather than a crashed sync loop; unexpected exceptions still propagate as crashes.

## How it works

`kanban_sync_stop.stop_scope` binds a keep-running predicate with a `ContextVar` to one sync-loop context. `pull` and `resync` call `checkpoint()` immediately before and after each remote page fetch. A false predicate raises `SyncStopped` at those transaction boundaries. Calls outside a stop scope retain their prior behavior.

`run_loop` handles that exception with a stopped diagnostic, records and re-raises other failures, and always calls timing cleanup. This allows a cancelled gateway watcher to finish its executor work without draining an entire paginated bootstrap.

## Code

- `hermes_cli/kanban_sync_stop.py`: scoped predicate, checkpoints and loop lifecycle.
- `hermes_cli/kanban_sync_client.py`: checks in feed/snapshot pagination and `run_forever` wrapper.

## Tests

Supplied source coverage, **not executed by this documentation/mirror task**:

- `tests/hermes_cli/test_kanban_sync_shutdown.py::test_gateway_stop_interrupts_active_replica_pages`, parameterized for feed and snapshot: drives the gateway watcher, stops it during a page fetch, joins the asyncio default executor, and asserts only one page was requested and none applied after stopping.
- The test uses a controlled Hub, not live network service calls. Separate normal-stop diagnostic and unexpected-crash branches are not directly asserted by this added suite.

## Applying

Use only the full ordered replay recipe in [README.md](README.md), including bridges and verification. This is not an independent apply set.

## Gotchas

Cancellation is cooperative, not an interrupt of an in-flight HTTP request or transaction. A blocked page fetch must return before the post-fetch check runs; network timeout behavior is unchanged. The predicate is scoped rather than a process-global cancellation flag.

## plugin?

No. These are core replica-loop and transaction-boundary checks; no plugin is required.
