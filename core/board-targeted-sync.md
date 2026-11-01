# Board-targeted sync rounds

## Behavior

A feed change wakes synchronization for that board, not every known board. Newly discovered boards and boards with pending local outbox work are included on the next tick. Initial synchronization and fixed-deadline periodic recovery still visit every board. An event arriving during I/O remains pending for the following round.

Unchanged outbox checks reuse their answer using the same SQLite connection's `data_version`; a committed change is read on the next tick. Replacement of the database or a read error invalidates the cached answer. This is not a timer-only cache.

## How it works

`SyncWork` retains a locked dirty-board set and consumes it before I/O. `FeedWatcher` marks its board on connection/reconnection and received change frames. The sync loop unions dirty/new boards with locally pending outboxes and passes the selection into `sync_all_once`. Periodic recovery keeps a fixed deadline independent of frequent event wakes.

## Code

- `hermes_cli/kanban_sync_work.py`: selection and deadline state.
- `hermes_cli/kanban_sync_client.py`: feed callback, selected rounds and `_BoardWatch` answer invalidation.
- Patches 1493–1494; originating PR #500.

## Tests

Executed with the canonical core runner: 21 sync/metrics/worker-node test files, 310 passing tests. All 15 `scripts/check` gates passed. Exact head `a34db8c8724a8eb8da412e116db32808ec4d1682` passed all eight Python CI shards and [the required aggregate](https://github.com/daveey/hermes/actions/runs/41309874436/job/124861636002).

- `test_kanban_sync_targeted_rounds.py`: real loop wiring with controlled I/O verifies unchanged siblings are not pulled, next-tick feed and local changes, event-during-I/O retention, fixed periodic recovery and discovery. SQLite trace assertions cover unchanged no-SELECT ticks and next-commit visibility in rollback-journal and WAL modes, file replacement and error retries.
- `test_kanban_sync_client.py`: real WebSocket connection and change-frame callbacks.
- `test_machine_metrics.py`: existing metric-reporting assertion retained with a selected-board-compatible fake.
- Cache-disabled mutation fails both unchanged-query cases; restored code passes. The pre-fix integration test pulled all sibling boards and failed.

## Applying

Apply as part of the verified full mirror series; see the README's current replay recipe. These patches depend on earlier synchronization changes.

## Gotchas

No specific live CPU target is claimed by the tests or this mirror. Deployment and a quiet-process CPU measurement are separate acceptance gates. A feed reconnect deliberately refreshes its board. A periodic full round is recovery work, not a cache invalidation shortcut.

## Why core rather than a plugin?

Board selection and SQLite observer lifetime belong to the existing synchronization loop. A plugin cannot avoid already-issued sibling pulls without replacing that loop.
