# Skip unchanged partition-repair scans in the sync round

Patches: **2464, 2465**.

## Behavior

Each Kanban sync round on a cursor-eligible board used to re-run the partition link, chat-link and lost-placeholder scans even when an event-only feed had changed nothing they read. Now those scans are skipped when the live and replica content, file identity and sync scope are unchanged since the last fully successful pass. Scan ordering and the remote-parent refresh are preserved: `refresh_remote_parents` still runs every round in primary mode. Any relevant change (tasks, links, chat links, outbox, tombstones, omissions, repair triggers, schema, file rewrite or replacement, a new connection handle, a replica rebuild) re-arms the scans on the next tick. Cursor movement between successful passes is not content and does not re-arm them.

## How it works

`kanban_sync_partition_content.prepare()` builds a stamp from both connections: handle identity, main-file `st_dev/st_ino/st_ctime_ns/st_mtime_ns/st_size`, the `kanban_repair_revision` row and schema version, the sync mode and the repair inputs. It returns a frozen `Decision(key, stamp, skip)`; `skip` is true only if the same stamp was previously published for that board path and the replica was not rebuilt. An unknown or unreadable state (open transaction, in-memory db, bad cursor or mode, missing revision, short inputs, OS/SQLite error) yields no stamp, so the original scans run. `_sync_board_locked` in `kanban_sync_client` runs the scans only when `not decision.skip`, then calls `commit()`, which publishes the PRE-scan stamp only if both cursors are unchanged since `prepare` and both sides remain eligible, so a pass during which a cursor moved, or an own or concurrent write occurred, is never cached. The memo is an LRU of 32 boards cleared by `reset_board_watch`. If any scan raises, nothing is published. Patch 2465 only hardens the tests (deterministic mtime for the same-inode rewrite case, plus fallback-guard cases). Related: [kanban-sync-repair-revision.md](kanban-sync-repair-revision.md).

## Code

`hermes_cli/kanban_sync_partition_content.py`, `hermes_cli/kanban_sync_client.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_sync_partition_content.py`, `tests/hermes_cli/test_kanban_sync_repair_content.py`. They exercise the `prepare`/`commit` memo directly and the sync round (`_sync_board_locked`) for event-only skips, re-arming mutations, scan exceptions and replica rebuilds, using fake service boards. No CLI, tool or REST entry path is claimed. The commit message reports measured timings from a replay, which are not reproduced here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

The skip is deliberately conservative: unknown metadata always runs the scans, and the memo is per process. File-identity fences depend on filesystem timestamps; patch 2465 sets an explicit mtime in the rewrite test because ctime updates can coalesce within a tick. The commit message states the measurement is not whole-gateway or Mac acceptance.

## Plugin

Core change; no plugin required.
