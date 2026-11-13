# Kanban remote notices: legacy-poller skip and shared host index

Patches: **3446, 3447**.

## Behavior
- The legacy notice poller no longer collects remote notices for a chat the live gateway owns, ending the per-tick claim/release churn on the remote ledger. Other chats are delivered as before (by the gateway remote owner or the Desktop sweep).
- Remote notice indexes are published once per host as `<ledger dir>/remote-index.db` per replica state, so each backend's 5 s poller reads its chat's rows instead of rebuilding the index. A stale key, missing or corrupt db, or different ledger falls back to local compute (as before) and offers the result for publishing.
- Publishing never waits: non-blocking build lock, sqlite timeout 0, busy publishes skipped, no index I/O under the notices lock. A change-driven holder (leased as `kanban-remote-index`) rebuilds on replica change, but is optional.
- Readers parse only task payloads that changed.

## How it works
3446 reuses the `_kb_gateway_owns(session)` answer in `tui_gateway/session_notifications.py` to skip remote collection. 3447 adds `tui_gateway/kanban_remote_index.py` (meta, cards and links tables, per-seq delta publish), hooks `collect`/`_chat_index` in `kanban_remote_notices.py`, starts the holder from `collect` and the gateway remote owner poll, and adds payload reuse in `remote_index_projection.py`.

## Code
- `tui_gateway/session_notifications.py`
- `tui_gateway/kanban_remote_index.py`
- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/remote_index_projection.py`
- `gateway/kanban_remote_owner.py`
- `tests/gateway/test_kanban_tui_sibling_custody.py`
- `tests/tui_gateway/test_kanban_remote_index.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_kanban_tui_sibling_custody.py`: `test_gateway_owned_chat_skips_the_remote_collection_a_legacy_only_chat_keeps_it`.
- `tests/tui_gateway/test_kanban_remote_index.py`: `test_reader_hit_returns_the_same_notices_as_local_compute`, `test_stale_key_computes_locally_and_publishes`, `test_publish_writes_only_the_rows_that_changed`, `test_missing_or_corrupt_copy_computes_locally`, `test_no_index_io_or_wait_under_the_collect_lock`, `test_notice_reaches_the_chat_on_its_next_poll_with_the_holder_dead`, `test_holder_republishes_on_replica_change_only_while_leased`, `test_reader_parses_only_the_payloads_that_changed`, `test_replica_without_seq_has_no_key_and_never_publishes`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Edge in 3446: a tick that newly claims a non-gateway local row flips ownership after collection, so its remote notices wait for the next poll (5 s).
- The holder relies on the host loop lease; without it the first reader after a change computes and publishes.
