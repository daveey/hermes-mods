# Worker attachment bytes through verified Board storage

Patches: **2446–2448**.

## Behavior

Attachment rows already synced between Kanban stores, but the machine-local `stored_path` was dropped on the way, so files a worker produced at completion (reports, ship proofs) stayed stranded on the worker. Now the bytes travel too. Each sync round, a store uploads attachments it alone holds to the Board service, and downloads attachments whose bytes it lacks, so the home gateway can read a worker's completion artifact after the worker's scratch directory is gone and after the sync cursor has moved on. A worker no longer evicts a task while it holds the only copy of an attachment that private storage has not acknowledged. Each transfer has an absolute time budget (headers and body included), is size-capped, never follows redirects with gateway credentials, and failures do not roll back row sync.

## How it works

`kanban_sync_attachments.transfer_pending(conn, hub, board, limit=2)` runs after the pull in `_pulled_replica` (replica store) and after push in `_sync_board_locked` (live store). It selects `task_attachments` rows that have no retry receipt, whose `blob_key` changed, or whose retry time has passed, at most two per store per round. The rows are the durable work queue; the local `kanban_attachment_retry` table (uid, blob_key, attempts, retry_at, error) only schedules attempts (30 s after a failure, 600 s after success, when verified rows get rehashed). `_transfer`:

- no `blob_key` and a local `stored_path`: read the file (must lie under the board's attachments root and match the row `size`), upload via `Hub.put_attachment`, and accept only a receipt whose `blob_key` is `sha256:<digest>` with matching `sha256` and `size`. Only the service assigns `blob_key`.
- `blob_key` present: verify the local file against the digest; if missing or damaged, download with `Hub.get_attachment_blob`, check size and digest, and publish atomically (temp file, fsync, replace, directory fsync) under `<task_id>/<uid>/<safe name>`.
- `_record_local` writes `stored_path`/`blob_key` with `kanban_sync_state.applying` set so the update is not echoed back as a sync event, and only if the row still matches (a row deleted or rebound during network I/O is not resurrected).

`kanban_sync_attachment_http` does the PUT `/v2/boards/<board>/attachments/<uid>/blobs/<digest>` and GET `.../blob` calls with the enrolled bearer key, no redirects, response capped at `KANBAN_ATTACHMENT_MAX_BYTES` (uploads 4096 bytes for the receipt), HTTP errors reduced to `HubError(code, "attachment transfer refused")`, truncated responses to `OSError`. Patch 2447 adds `kanban_sync_attachment_deadline.Transfer`: the request runs on a daemon thread; on expiry every registered socket is shut down and `TimeoutError` is raised, and a connection finishing after expiry is cut before sending. Patch 2448 registers the raw socket before proxy CONNECT reads and the SSL socket before the TLS handshake so those phases are cancellable too. `kanban_worker_node.evict` skips a task having an attachment with a `stored_path` but empty `blob_key`.

Related: [kanban-drop-attachments.md](kanban-drop-attachments.md) covers attachment handling elsewhere in the Kanban stack.

## Code

`hermes_cli/kanban_sync_attachment_http.py`, `hermes_cli/kanban_sync_attachment_deadline.py`, `hermes_cli/kanban_sync_attachments.py`, `hermes_cli/kanban_sync_client.py`, `hermes_cli/kanban_worker_node.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_sync_attachments.py` (sync-round upload and download between a worker and home store using a fake Board service, completion-time attachment, offline recovery, eviction hold, corrupt bytes, bad receipts, deleted-row race, outside-storage source, local repair) and `tests/hermes_cli/test_kanban_sync_attachment_http.py` (real local HTTP server: round trip, redirect refusal, truncated and oversized bodies, trickling headers/body, late connection, proxy CONNECT and TLS-handshake deadlines). The real Board service blob endpoints are not exercised (a test double models them); the sync paths are covered only via `client.sync_board`/`client.pull`/`client.push`.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The Board service must provide the blob endpoints; transports without `put_attachment` simply sync metadata (`transfer_pending` returns 0).
- Metadata can arrive before bytes; until the custodian uploads, the other store sees an empty `stored_path`.
- DNS resolution cannot be interrupted, so the deadline can only abandon that phase, not cancel it.
- Retry state stores only the exception class name, never response bodies or credentials.

## Plugin

Core change; no plugin required.
