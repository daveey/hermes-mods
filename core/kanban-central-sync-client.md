# Kanban central sync client (phase 2) and its live-soak fixes

Patches **0472**, **0474**, **0476**, **0478**, **0481**, **0509**, **0510**, **0513**, **0514**, **0515**, **0516**, **0547**,
**1622**
(sources `6053646f27`, `726d5b5525`, `9dc26ab68c`, `a7caf2ddb7`, `41a47a3c32`, `0fabd3eb47`, `07a0dd02b8`,
`da1a3a8443`, `20ca50ce3f`, `e1db6f8e77`, `193659fa7e`, `5c1a2bd67e`, `794ec3a2fc`). Builds on [kanban-central-service.md](kanban-central-service.md) and
[kanban-sync-seams.md](kanban-sync-seams.md); the two earlier client fixes are in [kanban-sync-client-fixes.md](kanban-sync-client-fixes.md).

- `patches/1622-fix-kanban-sync-reuse-one-kept-alive-Board-service-c.patch`

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
- **1622 (kept-alive Board connection):** `Hub` requests reuse one HTTP/1.1 connection per thread instead of a fresh
  `urlopen` (TCP connect + TLS handshake + platform trust evaluation) per request. Before it, a gateway's sync executor
  thread did several handshakes a second while cards ran; the patch's live replay measured ~10 ms thread CPU per
  request with `urlopen` against ~0.7 ms on a reused connection (a measurement, not a test).
  - The connection is dropped when the server has closed it, after 30 s idle (`Hub.IDLE_REUSE_S`) or on a
    `Connection: close` response.
  - A stale reused socket gets one retry on a fresh connection when the send fails (any method: the request never
    reached the server) or when a GET's answer is lost. A POST whose answer is lost is never resent; it fails as a
    transport error and the sync round retries.
  - Errors keep `urlopen`'s contract: 410 → `Resync`, other non-2xx → `HubError`, transport and protocol failures
    (including a garbage status line, which `http.client` raised as `BadStatusLine` before) → `urllib.error.URLError`.
  - A GET redirect falls back to `urlopen` (which follows it); a POST redirect raises `HubError` without resending.
  - An environment proxy (or a non-http(s) URL, untested) keeps the old `urlopen` path.
  - A connection inherited across `fork` is left to the parent; threads never share a connection.

## How it works

`hermes_cli/kanban_sync_client.py` (pull/push/import/diff/feed watcher), `hermes_cli/kanban_db_sync.py` (config, lease
client, capture triggers), `hermes_cli/kanban_sync_cli.py`, `hermes_cli/kanban_chat_links.py`,
`hermes_cli/kanban_session_activity.py` (replica read path), `gateway/kanban_watchers.py`.

**1622:** `Hub._call` splits the URL and routes to `_call_kept_alive`, or to `_call_urlopen` for a proxy or a
non-http(s) scheme. `_connection` keeps `conn`, `pid` and last-use time on a `threading.local`, and builds an
`http.client.HTTPSConnection` with one lazily created, shared `ssl.create_default_context()` (truststore-injected when
installed) or an `HTTPConnection`. The URL's path prefix is kept in front of each request path. `_peer_closed` checks
an idle socket with `select.poll` (not `select.select`, which raises for fds ≥ 1024 on a busy gateway; Windows falls
back to `select`). `Hub.close()` closes the calling thread's connection. Hub uses its own `threading`/`monotonic`
handles so tests that replace the module's `threading`/`time` don't affect it.

## Code

The files above plus `hermes_cli/kanban.py`, `hermes_cli/kanban_parser.py`, `hermes_cli/kanban_db.py`.
**1622** changes only `hermes_cli/kanban_sync_client.py` (`_peer_closed`, `Hub`) and adds
`tests/hermes_cli/test_kanban_sync_hub_keepalive.py`.

## Tests

`tests/hermes_cli/test_kanban_sync_client.py` (translate/import/diff, outbox replay, rejected ops, cursor and snapshot
rebuild, full-row sends, transient backoff, capture gap, feed watcher, no-PyYAML config, worker lease client),
`tests/hermes_cli/kanban_sync_fake_board.py` (fake service) and `tests/hermes_cli/test_kanban_chat_links.py`
(reconcile, replica answers, links diff, CLI). `tests/hermes_cli/test_kanban_sync_apply.py`
(`test_millisecond_timestamps_from_the_wire_are_stored_in_seconds`, `test_rows_already_in_milliseconds_are_repaired_once_on_open`)
covers 0547. A live Board service is not exercised by these tests.

**1622:** `tests/hermes_cli/test_kanban_sync_hub_keepalive.py`, against a real local HTTP/1.1 server that counts
accepted connections and requests: `test_sequential_requests_share_one_connection`, `test_url_path_prefix_is_kept`,
`test_server_idle_close_reconnects_transparently`, `test_stale_socket_get_is_retried_once`,
`test_post_whose_answer_is_lost_is_not_resent`, `test_error_statuses_keep_the_urlopen_contract_and_the_connection`,
`test_protocol_garbage_is_an_oserror`, `test_connection_close_response_is_not_reused`,
`test_idle_connection_past_reuse_window_is_replaced`, `test_threads_never_share_a_connection`,
`test_environment_proxy_keeps_urlopen`, `test_failed_send_on_reused_socket_is_retried_on_a_fresh_one`,
`test_connection_inherited_across_fork_is_left_alone`, `test_peer_closed_works_above_select_fd_ceiling` (skips when
`RLIMIT_NOFILE` ≤ 1100), `test_redirects_follow_for_get_and_never_resend_a_post`.

These were not rerun by the mirror pass.

Untested (1622): the HTTPS path (`HTTPSConnection`, the shared SSL context, truststore), since every test uses plain
http; the Windows `select.select` branch of `_peer_closed`; a real `fork` (the test fakes the pid); `Hub.close()`;
the non-http(s)-scheme fallback to `urlopen`; the CPU-per-request figures.

## Applying

`git am -3` the twelve patches in number order after `0471`, then `1622` (touches only `Hub` in
`hermes_cli/kanban_sync_client.py`; no config).

## Gotchas

- A Durable Object keeps old code until restart: any service deploy that adds a table races the clients, which is why
  rejections of that kind must stay pending.
- Never `install_or_remove` triggers outside a transaction.
- The service's clocks (leases, HLCs, feed, tombstones) are ms; core's row timestamps are seconds. Never copy one into
  the other: `lease_expires` stays ms, `task_runs.started_at` stays seconds.
- **1622:** never let a POST retry after its answer was lost: ops and lease grants may already be applied. A `Hub`
  used from several threads holds one socket per thread; `close()` only closes the caller's, the others close on
  their next reuse check or when the thread exits.

## Plugin?

No: the sync client hooks core's kanban DB layer and gateway startup.
