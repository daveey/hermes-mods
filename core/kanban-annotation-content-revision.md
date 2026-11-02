# Kanban annotation content revisions

Patches **1666** (`ab8d9bb45db393ff59efe03523acb6efa76f03f1`) and **1667** (`c92bb2cf53e0b31a76f76494899357ba460305c8`).

## Behavior

Session-list Kanban annotation caches invalidate on content they actually read, rather than on every board-file bookkeeping write. Relevant task/link/run/event/comment/subscription changes remain visible on the next read. Claim, lease, heartbeat and sync-only updates do not force a board reread. Equal annotations reuse their published/frozen objects.

Patch 1667 supplies `drop_triggers(conn)` for schema edits that remove watched columns; readers use stat fallback until the next writer initialization reinstalls the triggers.

## How it works

Writer initialization installs a one-row `kanban_activity_revision` counter and INSERT/DELETE/conditional UPDATE triggers. Local boards watch `tasks`, `task_links`, `task_runs`, `task_events`, `task_comments`, `kanban_notify_subs` and `task_chat_links`; sync shadows watch only tasks/chat links. Update predicates exclude enumerated bookkeeping columns. Trigger increments share the writer's transaction.

`read_key()` reads the counter and checks coverage in one snapshot. `content_key()` combines revision, schema version and file identity, falling back to DB/WAL stat when coverage is incomplete or unreadable. Repair/reinstallation increments the counter so an unobserved gap cannot reuse an old key. The counter is read even when file stats are unchanged.

Board memos and replica-link reads use that key; ledger-owner indexes reuse unchanged card objects. Equal recomputation retains previous parts, and `_read_annotation_parts()` reuses an equal frozen result. The hub-replica signature implementation moves into its own module and continues separating relevant task/gateway content from unrelated replica traffic.

## Code

`hermes_cli/kanban_activity_revision.py` (new counter/trigger/key helpers), `kanban_db_connect.py` (installation), `kanban_session_activity.py`, `kanban_session_activity_hub.py` (extracted hub signature), `kanban_chat_links.py` (replica and owner memos). Patch 1667 also updates legacy-schema fixtures in `test_kanban_comment_origin.py` and `test_kanban_notify.py`.

## Tests

In `tests/hermes_cli/test_kanban_activity_revision.py`:

- `test_bookkeeping_writes_do_not_move_the_fingerprint_or_reread_the_board`
- `test_a_task_write_moves_the_fingerprint_and_is_read_at_once`
- `test_every_table_the_counts_read_moves_the_key`
- `test_claim_and_heartbeat_columns_are_bookkeeping`
- `test_a_missing_trigger_falls_back_to_the_file_stat`
- `test_reinstall_after_a_gap_moves_the_key`
- `test_hub_bookkeeping_does_not_move_the_fingerprint`
- `test_shadow_key_ignores_pull_stamps_and_unread_tables`
- `test_replica_rows_are_reread_only_when_their_content_moves`
- `test_ledger_owners_rebuild_only_for_a_new_projection`
- `test_an_unchanged_annotation_reuses_its_frozen_parts`
- 1667: `test_drop_column_after_drop_triggers_reinstalls_for_the_new_columns`.

These source tests were not rerun during this mirror. The patch message's performance replay is a source-reported measurement, not a reproduced benchmark here. No live Desktop or remote fleet acceptance is claimed.

## Applying

Apply 1666 then 1667 in the [ordered mirror series](README.md). Existing writers install/repair the revision on open. This is core SQLite/display-cache integration, not a plugin.

## Gotchas

SQLite refuses to drop a column named by a trigger: call `drop_triggers()` before the schema change, not afterward. It removes triggers, not the counter. Missing revision support must retain the fallback rather than trusting stale rows. A new annotation read column must not remain classified as bookkeeping. Memoized rows and owner maps are read-only to callers.
