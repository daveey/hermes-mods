# Idle watcher change-key gates

Patch **1663** (`b71f0fef09f83ff0d7f92ea341ccbc8e7b062230`); fixture follow-up **1664** is described in [test stability](test-stability-1659-1681.md).

## Behavior

Unchanged skill, pairing and bot-relay outbox directories avoid repeated directory listings, while nested installs, member edits and symlink-target type changes remain observable. Session signatures use published SQLite commit observations rather than relying only on DB/WAL timestamps. Remote-notice sweeps reuse replica-derived rows while continuing time, liveness and claim-recovery decisions every pass.

## How it works

`directory_snapshot()` caches membership, not child contents, under device/inode/mtime/ctime/size. Its locked LRU holds at most 4096 directories. Recent/racy timestamps are not cached; symlink target types are rechecked. Callers still stat members and recurse into skill directories.

`_session_db_content_sig()` uses `kanban_replica_observer.fingerprint()` and brackets reads with the published-commit key. `_sweep_snapshot()` likewise caches only a stable read, keyed by observer stamp and gateway; one memo bounds retained sweep data. A commit racing the read cannot certify the old snapshot. `_deliver_link()` checks delivered identity and grace before constructing the payload, but an unchanged replica does not suppress a newly unloaded chat or expired grace interval.

## Code

- `utils_directory_snapshot.py`: membership cache.
- `tools/skills_tool.py`: skill signature walk.
- `tui_gateway/change_watcher.py`: session, pairing and outbox signatures.
- `tui_gateway/kanban_remote_notices.py`: stable sweep snapshot and delivery ordering.

## Tests

`tests/tools/test_skill_signature_idle.py`:
`test_unchanged_tree_no_listing_nested_install_is_seen`, `test_symlink_target_type_change_is_seen`, `test_racy_timestamp_cannot_hide_membership_change`.

`tests/tui_gateway/test_idle_watcher_gates.py`:
`test_idle_directory_not_relisted_but_member_edit_seen` (pairing/outbox),
`test_sweep_idle_does_not_read_replica_and_unloaded_chat_is_retried` (delete/WAL journals),
`test_sweep_commit_during_read_is_not_cached`,
`test_session_commit_after_wal_frames_is_seen`,
`test_sweep_grace_expires_without_replica_change`.

These patch-contained tests were not rerun in the mirror. They exercise local filesystem/SQLite fixtures and watcher helpers, not a deployed Desktop session or a fleet CPU benchmark.

## Applying

Use the [ordered mirror replay](README.md), including 1663 then its 1664 fixture fix. Requires the existing replica observer and projection revision machinery; no config switch or plugin is added.

## Gotchas

Directory membership is not file content. Never replace the callers' per-member stats with the cached listing. Do not memoize grace expiration, session liveness or dead-claim settlement alongside replica data. WAL file timestamps can precede publication of the committed frames; the observer key is the synchronization boundary.
