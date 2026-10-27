# Kanban sync seams (client side, flag off by default)

Patch **0401** (`5cd9bd0161`). It is phase 1 of [kanban-central-service.md](kanban-central-service.md), where the patch
is called "proposed 0193". It adds the core seams a sync plugin needs. It ships no network code, and with
`kanban.sync.enabled: false` (the default) nothing changes.

## Behavior

- **Config gate.** `kanban.sync.enabled` in `config.yaml` is read through `load_config` the first time a process
  opens a board file. When it is false, no sync table or trigger is created, and capture triggers left by an earlier
  enable are dropped. When it is true, the board gets `kanban_sync_state` (mode `shadow`), `kanban_sync_outbox`,
  `kanban_sync_tombstones`, a `uid` on comments, events, runs and attachments, and the lease mirror columns on
  `tasks`. Rows written before the enable get uids at that point.
- **Outbox capture.** `AFTER INSERT/UPDATE/DELETE` triggers copy synced columns into the outbox in the writer's own
  transaction. `run_id` and `current_run_id` travel as run uids. Local-only columns (claim lock and expiry, PIDs,
  heartbeats, workspace path, session and project ids, the lease mirror) never produce an op, so heartbeats write
  nothing. Triggers are generated from `PRAGMA table_info` minus the declared local set, so a column a migration adds
  is captured by default. `undeclared_columns()` names it, and a test fails until it is declared synced or local.
- **Apply path.** `apply_remote_changes(conn, changes, new_cursor=...)` applies one feed page in one transaction,
  with capture suppressed so there is no echo. It does these things:
  - An op of ours that comes back (its `client_op_id` is still in the outbox) only clears that outbox row.
  - Run uids map to local run ids, in any order: an unknown uid gets a placeholder run row.
  - Wire values never overwrite local-only columns.
  - A replayed page applies nothing a second time.
  - The cursor advances in the same commit as the rows.
  - After the commit, lifecycle hooks fire once with `remote=True` and `origin_gateway`.
  - A page that makes a card `ready` or `review` wakes the embedded dispatcher within about a second.
- **Leases** (boards in mode `primary` only). `claim_task` and `claim_review_task` ask the injected lease client
  (`set_lease_client`) for a lease before the claim CAS. With no grant (denied, offline or no client), the claim is
  deferred: it logs `claim_deferred: ...` and the card stays `ready`. A grant is mirrored onto the task, its run uid
  becomes the run row's uid, and the local claim expiry is capped at `lease_expires - 60 s`. A grant whose CAS then
  loses is released after the commit. `heartbeat_claim` renews at most once per `ttl/3` and keeps the claim within
  the lease deadline. If renewal keeps failing, or the feed reports the lease held elsewhere, the dispatcher's
  stale-claim path stops the live worker and the run ends `lease_lost`. An operator `reclaim_task` releases the
  lease.
- **Deletes and ids.** On a synced board, deleting a task writes one `delete` op and one tombstone. Links, comments
  and events removed with it write nothing. Later pulls of a tombstoned task, or of its rows, are dropped. New cards
  get `t_` plus 16 hex digits (a 48-bit ms timestamp and 16 random bits); unsynced boards keep 8-hex ids.

## How it works

`hermes_cli/kanban_db_sync.py` holds all the logic. Each seam is one call:

- `kanban_db_connect` first-connect init calls `install_or_remove`.
- `claim_task` and `claim_review_task` call `claim_gate`. It is used as the outer context manager around the
  claim's `write_txn`, so a release happens after the commit.
- `_claim_and_open_run` calls `stage_claim`.
- `heartbeat_claim` calls `heartbeat_expiry`.
- `release_stale_claims` calls `lease_lost`.
- `reclaim_task` calls `release_lease`.
- `create_task` and decompose call `new_task_id`.
- The gateway dispatcher sleeps through `_sleep_until_tick_or_sync_wake`.

`write_txn` itself is unchanged: the triggers already run inside the caller's transaction. To offset the size
ratchet, the pure worker-context helpers moved to `kanban_db_context.py`.

## Code

`hermes_cli/kanban_db_sync.py`, `hermes_cli/kanban_db_context.py`, seams in `kanban_db.py`, `kanban_db_connect.py`,
`kanban_db_graph.py`, `gateway/kanban_watchers.py`, and the default in `config_defaults.py`.

## Tests

- `tests/hermes_cli/test_kanban_sync_capture.py`: the config key through the loader, flag off, every synced column
  captured on update and insert, local-only columns never captured, heartbeat writes nothing, the drift guard,
  the tombstone on delete, wide ids, uid backfill.
- `tests/hermes_cli/test_kanban_sync_apply.py`: no echo, own op clears its outbox row, run uid translation, local
  columns protected, hooks fire once after the commit, cursor atomic with the rows, dispatcher wake, remote delete.
- `tests/hermes_cli/test_kanban_sync_leases.py`: grant, deny and offline against a stub service; no client;
  review claims; shadow mode never asks; a lost CAS releases its lease; the renewal rate limit; a worker that
  cannot renew is stopped before `lease_expires` (a real child process); the control case; lease moved elsewhere;
  operator reclaim.
- `tests/gateway/test_kanban_dispatcher_sync_wake.py`: the wake cuts the sleep short; stop and cancel still work.
- The upstream kanban suite (`-k kanban`) gives the same failing test ids as clean main, both with the flag off and
  with it on (shadow mode, no server).

## Not built (later phases)

- The sync worker: outbox drain, change feed, enroll, import and detach.
- HLC stamping and the per-field merge of locally pending fields.
- Server-side `recompute_ready` (gateways still promote locally).
- `available` cards.
- Blob fetch.
- The `remote` check in workspace plugins.
- `board_id` and `gateway_id` are local placeholders until enrollment.

## Gotchas

- The 60 s lease margin equals the default dispatcher tick, so on a 60 s tick a worker can be stopped up to the
  margin later than its deadline, which is just before `lease_expires`. Keep `dispatch_interval_seconds` below the
  margin on synced boards.
- With the flag on and no plugin draining it, the outbox grows. Turn the flag on only together with the sync plugin.
- A lease client must raise `LeaseUnavailable` (or let `OSError` through) for transport failures. Any other
  exception propagates out of the claim.

## Plugin?

No. Capture has to happen inside every writer's transaction, including CLI subprocesses and direct-SQL editors, so
it must be triggers installed by core init. The claim, heartbeat and stale-claim paths are core functions. The
network side will be a plugin.
