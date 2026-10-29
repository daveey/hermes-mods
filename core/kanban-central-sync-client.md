# Kanban central sync client (phase 2) and its live-soak fixes

Patches **0472**, **0474**, **0476**, **0478**, **0481**, **0509**, **0510**, **0513**, **0514**, **0515**, **0516**, **0547**
(sources `6053646f27`, `726d5b5525`, `9dc26ab68c`, `a7caf2ddb7`, `41a47a3c32`, `0fabd3eb47`, `07a0dd02b8`,
`da1a3a8443`, `20ca50ce3f`, `e1db6f8e77`, `193659fa7e`, `5c1a2bd67e`). Builds on [kanban-central-service.md](kanban-central-service.md) and
[kanban-sync-seams.md](kanban-sync-seams.md); the two earlier client fixes are in [kanban-sync-client-fixes.md](kanban-sync-client-fixes.md).

## Behavior

- Off unless `kanban.sync.*` or a hub enrollment says otherwise. With sync on, each gateway pulls the Board's changes
  first (cursor applied in the same transaction; a 410 rebuilds from a paged snapshot), then pushes the capture-trigger
  outbox as HLC-stamped row ops (FIFO batches of 200). Shadow mode fills a shadow copy and never touches the live board.
  `hermes kanban sync` has import and diff commands.
- Fields with an unanswered local edit are not overwritten by a pull; a rejected op is marked and a primary board
  restores the service's row.
- A card the service never acknowledged (`sync_version` NULL) is sent as its full row on edit (0474) and ahead of any
  child row in the batch (0516); child rows (runs, comments, events, attachments, chat links) always go whole (0515).
- Transient rejections (unknown table, storage error, depends_on_rejected, not_found for a card the gateway still has)
  back off by time (2 s doubling to 5 min, 20 attempts) instead of retrying every round (0514, 0515).
- `sync diff` counts an acknowledged-but-unpulled row as in flight and pulls the replica first (0481).
- A worker process builds its own hub lease client from the enrollment (0478); `kanban.sync` is read with the repo's
  YAML loader, so ruamel-only venvs work (0476).
- Process start no longer opens a capture gap: trigger drop/create and the uid backfill run in one write transaction,
  and `install_triggers` is a no-op when the installed set already matches (0509).
- The sync loop holds the Board's WebSocket feed per synced board and runs a round on a change frame or within 0.5 s
  of a local outbox write, else at the poll period; without `websockets` it polls (0510).
- Chat links become a synced table `task_chat_links` reconciled every 60 s per board, so any gateway's replica can
  answer a chat's open cards on other gateways; missing links are released, never deleted (0513).
- Core's timestamp columns stay in Unix seconds whatever the feed sends (0547): `_local_fields` turns a millisecond
  value (> 1e11) in `SECONDS_COLUMNS` (tasks created/started/completed, runs started/ended, events/comments/
  attachments/chat links created) into seconds, and the first open of each synced file rewrites rows already in ms
  (`kanban_sync_state.seconds_repaired`, uncaptured). Before it, the Board's lease grants wrote `task_runs.started_at`
  and the `claimed` event in ms; the pull copied them into every live board and replica, the whole-row child push
  sent them back, and readers doing `now - started_at` broke (a day computed as the year 58792). The Board writes
  seconds since hermes-hub#30 and normalizes the same columns at its write boundary.

## How it works

`hermes_cli/kanban_sync_client.py` (pull/push/import/diff/feed watcher), `hermes_cli/kanban_db_sync.py` (config, lease
client, capture triggers), `hermes_cli/kanban_sync_cli.py`, `hermes_cli/kanban_chat_links.py`,
`hermes_cli/kanban_session_activity.py` (replica read path), `gateway/kanban_watchers.py`.

## Code

The files above plus `hermes_cli/kanban.py`, `hermes_cli/kanban_parser.py`, `hermes_cli/kanban_db.py`.

## Tests

`tests/hermes_cli/test_kanban_sync_client.py` (translate/import/diff, outbox replay, rejected ops, cursor and snapshot
rebuild, full-row sends, transient backoff, capture gap, feed watcher, no-PyYAML config, worker lease client),
`tests/hermes_cli/kanban_sync_fake_board.py` (fake service) and `tests/hermes_cli/test_kanban_chat_links.py`
(reconcile, replica answers, links diff, CLI). `tests/hermes_cli/test_kanban_sync_apply.py`
(`test_millisecond_timestamps_from_the_wire_are_stored_in_seconds`, `test_rows_already_in_milliseconds_are_repaired_once_on_open`)
covers 0547. A live Board service is not exercised by these tests.

## Applying

`git am -3` the twelve patches in number order after `0471`.

## Gotchas

- A Durable Object keeps old code until restart: any service deploy that adds a table races the clients, which is why
  rejections of that kind must stay pending.
- Never `install_or_remove` triggers outside a transaction.
- The service's clocks (leases, HLCs, feed, tombstones) are ms; core's row timestamps are seconds. Never copy one into
  the other: `lease_expires` stays ms, `task_runs.started_at` stays seconds.

## Plugin?

No: the sync client hooks core's kanban DB layer and gateway startup.
