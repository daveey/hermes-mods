# Kanban board recovery: rebuild a table-damaged `kanban.db` in place

When a Kanban board's SQLite file has damaged **table** pages, stock Hermes fails closed and the board stays down
until someone rebuilds it. These patches add a supported, automatic rebuild. It runs out of process, takes a
shared lock, salvages every readable row, installs the result under SQLite's own locking, and reports what was lost.

Patch series (mirrors of the curated commits; the numbers are apply order):

| Patch | Commit | Adds |
|---|---|---|
| 0633 | `c0ca2b2922` | initial recovery module, CLI, dispatcher hook |
| 0634 | `96fa4223ab` | tombstone-aware fill; reconcile never re-pushes deleted rows |
| 0635 | `ff003baaa4` | contention and index-damage recovery |
| 0636 | `bf6093e0c0` | `<db>.recovery.lock` shared with the live-guard swap tool |
| 0637 | `949f16241e` | stale-index handling, `contended`, `stranded_holders`, `installed_unverified`, child timeout |
| 0639 | `1bbcb03fa9` | lint fixes and UTF-8 handling |
| 0640 | `1bc374222c` | lint fixes |
| 0648 | `86dba5f9a3` | holder record (`<db>.recover.pid` JSON) |
| 0649 | `baed59f27c` | process-start identity in the holder record |
| 0652 | `a934e39df2` | shared safe pid-existence probe and explicit proc-stat encoding |
| 0659 | `adff0a11d6` | document automatic-recovery and recovery-assignee configuration keys in the user guide |

## Behavior

- **Index-only damage** is still repaired by the connect-time guard (`kanban_db_connect`) with `REINDEX`. Recovery
  reports it as `index_only` and leaves it to the guard.
- **Table damage** (anything `integrity_check` reports that is not a repairable index) starts recovery:
  - Automatically: the gateway's kanban dispatcher, when it pauses a damaged board, calls
    `spawn_recovery(db)`, which runs `python -m hermes_cli.kanban_db_recover --db <path> --json` as a detached child.
    One automatic attempt per board per 30 minutes. Opt out with `kanban.auto_recover_corruption: false`.
  - By hand: `hermes kanban recover [--dry-run] [--no-fill] [--json]` (also works on boards `init` refuses).
- Outcomes (`status`) and what each writes:

| status | Meaning | Installed? |
|---|---|---|
| `recovered` | rebuilt and installed, post-install `integrity_check` passed | yes |
| `installed_unverified` | installed, but the post-install check failed | yes (report + follow-up card say so) |
| `healthy` | a clean process reads the board fine (the caller was probably stranded on deleted `-wal`/`-shm`) | no |
| `index_only` | `REINDEX` is enough | no |
| `busy` | another recovery or a manual swap holds the lock; reason names pid, session and card | no |
| `contended` | the board was written during all 3 rebuild passes | no, retried later |
| `stranded_holders` | some process still holds fds on **deleted** `-wal`/`-shm` of this board; pids are listed in `stranded_pids` | no |
| `missing` | no file / empty file | no |
| `dry_run` | `--dry-run`: reports the loss, writes nothing in the live board | no |
| `failed` | error or timeout; nothing was written to the live board | no |

- Exit code is 0 for `recovered` / `healthy` / `dry_run`, 1 otherwise.
- **Reports**: every outcome (not just success) writes `<db>.recovery-<ts>.json`; the CLI and logs give the
  markdown-style summary lines (`- <table>: N read, N filled from backup, N known lost (N possibly)`).
  `recovered` and `installed_unverified` also file a card on the recovered board (assignee from
  `kanban.recovery_card_assignee`, default `default`) with the loss report and next steps.
- **Sync**: for `recovered` boards attached to the sync service, `kanban_sync_client.reconcile` runs. Its push of
  "rows the service lacks" skips rows the service deleted on purpose (tombstones) and rows deleted locally
  (local tombstone or a pending outbox op), so recovery never resurrects deletions.
- The damaged file is kept as `<db>.recover-<ts>.damaged.bak` (3 newest kept).

## How it works

1. **Out of process.** A raw read in a process that holds a connection cancels that process's POSIX locks, so the
   work happens in a fresh interpreter that opened nothing. That child also re-classifies the file from clean
   state; a caller stranded on deleted sidecars sees "corruption" that is not on disk.
2. **One recovery at a time.** Non-blocking `flock` on `<db>.recovery.lock`, the same file the live-guard's
   `swap_db.py` locks. The lock file is never written. The holder (`pid`, `proc_start`, `started_at`,
   `session_id`, `task_id`) is JSON in `<db>.recover.pid`, removed on release. A holder is only reported while its
   pid is alive **and** its `/proc/<pid>/stat` start time still matches (a reused pid is ignored). The spawned
   child gets the caller's card through `HERMES_RECOVERY_HOLDER_TASK`, since `HERMES_KANBAN_TASK*` is stripped from
   its env.
3. **Snapshot** through the SQLite backup API (consistent with the WAL) via a long-lived "watch" connection. If
   SQLite refuses to read, fall back to a raw file + WAL copy after closing this process's connections. A
   `PRAGMA data_version` is read before the copy.
4. **Salvage every table** from the snapshot: forward and reverse by rowid, plus forward and reverse through each
   full (non-partial) index with `INDEXED BY`. A covering scan of every index yields `{rowid: key}` for rows the
   index still lists. Listed rowids no scan produced get a point lookup. A **clean miss** (no error) means a stale
   index entry for a deleted row: it is dropped and counted in `stale_index_entries`, never resurrected.
5. **Fill** only the *unreadable index-listed* rowids from the newest healthy `*.bak` / `*.pre-recover-*` copy
   (`quick_check` ok, has `tasks`, newest of 12 checked). A backup row qualifies only if every index that lists the
   rowid agrees on the key columns, and its uid/id/link is not tombstoned. `kanban_sync_outbox`,
   `kanban_sync_state` and `sqlite_sequence` are never filled. Rows the backup has that no index lists are not
   added (they may have been deleted since).
6. **Rebuild** into a fresh WAL file with the same page size, `user_version` and `application_id`, then indexes,
   triggers and views, `sqlite_sequence` set to the max of old/backup/current/highest-listed rowid, then
   `integrity_check` and a schema-object comparison.
7. **Loss accounting** per table: `recovered`, `filled_from_backup`, `known_lost` (listed by **every** index:
   lower bound), `possibly_lost` (listed by **any** index: upper bound), `lost_keys` (first 200),
   `unreadable_paths`, `stale_index_entries`.
8. **Guards before install.** If `data_version` changed on the watch connection, rebuild again, at most 3 passes,
   else `contended`. Then `stranded_holders()` scans `/proc/*/fd` for fds on `<db>-wal (deleted)` /
   `<db>-shm (deleted)`; any hit means `stranded_holders`. Their next checkpoint would overwrite the install.
9. **Install in place** with `sqlite3.backup()` into the live file (one write transaction, so a kill leaves the old
   content), `wal_checkpoint(PASSIVE)`, then a fresh-connection `integrity_check`. The inode, WAL and other
   processes' connections survive and see the new content. **The db is never renamed or unlinked.**
10. **Timeout.** The child sets `SIGALRM` (`--timeout`, default 570 s) whose handler raises `TimeoutError`, so the
    normal cleanup runs (work dir removed, report written). The parent also gives up after 600 s.

## Code

All in core (`hermes-agent`), no new dependencies:

- `hermes_cli/kanban_db_recover.py`: new module. `recover()`, `spawn_recovery()`, `recovery_lock()`,
  `recovery_lock_holder_info()`, `classify()`, `stranded_holders()`, `_salvage_table()`, `_build()`,
  `_fill_from_backup()`, `_install()`, `_file_card()`, `_reconcile_sync()`, `main()`.
- `hermes_cli/kanban_ops.py`: `_cmd_recover`; `hermes_cli/kanban.py`: `recover` subcommand routing (runs before
  `init`); the "not index-only" hint in the repair command points at it.
- `gateway/kanban_watchers_dispatcher.py`: `_start_auto_recovery` hook when a damaged board is paused.
- `hermes_cli/kanban_sync_client.py`: `reconcile` skips tombstoned and locally deleted rows when pushing "missing
  from service".
- Related, outside this series: the sqlite-live-guard mod's `swap_db.py` takes the same `<db>.recovery.lock`, and
  `hermes_cli/kanban_db_connect.py` holds the `REINDEX` guard.

Config keys (read with defaults; not in `DEFAULT_CONFIG`): `kanban.auto_recover_corruption` (default `true`),
`kanban.recovery_card_assignee` (default `default`).

## Tests

Upstream regression coverage in `tests/hermes_cli/test_kanban_db_recover.py` (28 passed on the applied public range; see [mirror-651-verification.md](mirror-651-verification.md)):

- Recovery: `test_damaged_board_fails_closed_then_recovers_in_place_with_a_loss_report`,
  `test_rows_the_damage_took_are_filled_from_the_newest_healthy_backup`,
  `test_fill_never_resurrects_rows_deleted_after_the_backup`,
  `test_stale_index_entries_of_deleted_rows_are_not_filled_back_in`,
  `test_a_board_a_clean_process_reads_fine_is_left_untouched`, `test_dry_run_reports_the_loss_and_writes_nothing`.
- Concurrency / install safety: `test_a_write_during_the_rebuild_is_not_overwritten`,
  `test_writes_during_every_pass_install_nothing`, `test_stranded_holders_finds_processes_on_deleted_sidecars`,
  `test_a_process_on_deleted_sidecars_blocks_the_install`, `test_installed_unverified_still_writes_the_report`,
  `test_the_child_timeout_cleans_up_its_work_dir`.
- Lock and holder record: `test_a_second_recovery_of_the_same_board_is_refused_busy`,
  `test_a_manual_swap_holding_the_shared_lock_makes_recovery_busy`,
  `test_a_second_cli_recover_exits_nonzero_and_names_the_holder_pid_session_and_card`,
  `test_holder_info_is_recorded_while_held_and_cleared_on_release`,
  `test_holder_info_ignores_a_record_whose_process_is_gone`,
  `test_spawned_recovery_records_the_callers_card_as_holder`, `test_holder_info_ignores_a_reused_pid`,
  `test_holder_record_carries_this_process_start_time`.
- Entry points: `test_dispatcher_starts_recovery_when_it_pauses_a_damaged_board` (parametrized on enabled),
  `test_auto_recover_follows_config`, `test_spawn_recovery_is_rate_limited_per_board`,
  `test_cli_recover_rebuilds_a_board_init_refuses`, `test_cli_recover_reports_busy_while_another_recovery_runs`.
- Housekeeping: `test_damaged_snapshots_are_rotated`, `test_quarantine_rotation_keeps_manual_and_recovery_copies`.

`tests/hermes_cli/test_kanban_sync_reconcile.py` adds six cases covering lost-row/outbox restore, local-edit preservation, service tombstones, pending local deletes, no-op reconcile and the CLI service entry point; all six passed on the applied public range. The raw-copy fallback in `_snapshot` and the Windows lock branch have no test in the recovery test file.

```bash
scripts/run_tests.sh tests/hermes_cli/test_kanban_db_recover.py
```

## Applying

Apply on the fork after its earlier Kanban dependencies, in order (not directly on current upstream; see the [verification record](mirror-651-verification.md)):

```bash
git am -3 --empty=keep ~/code/hermes-mods/core/patches/0633-*.patch   # then 0634 0635 0636 0637 0639 0640 0648 0649
```

- Apply these in order; later ones modify the module the first creates. 0638, 0641-0647 and 0650-0651 are unrelated. 0652 replaces the holder liveness check with `gateway.status._pid_exists` and uses explicit UTF-8-sig for proc-stat reads; all 28 recovery tests passed again after it.
- The live-guard `swap_db.py` must take the same `<db>.recovery.lock` for the manual-swap exclusion to hold.
- Restart the gateway/dispatcher so `_start_auto_recovery` is loaded. No desktop rebuild.
- Nothing to enable: automatic recovery is on by default.

## Gotchas

- **Residual overwrite window.** The `data_version` check and the backup's write-lock acquisition are not atomic.
  A commit landing between them is overwritten by the install. This is explicit and unfixable with the backup API;
  it is why contended boards install nothing, and why the stranded-holder check exists.
- **Never `mv` a rebuilt file over a live `kanban.db`.** That strands every open WAL connection and corrupts the
  next copy; it is the failure this feature replaces. Install is always in place.
- **Stranded holders block the install.** Restart the listed pids (they keep an unlinked WAL), then re-run
  `hermes kanban recover`. Automatic retries are 30 minutes apart.
- **Loss numbers are bounds.** Rows no index lists and no read path reaches are invisible; `unreadable_paths > 0`
  means the count may be low. `known_lost` is a lower bound, `possibly_lost` an upper bound.
- **Fill needs a healthy backup** (`*.bak` / `*.pre-recover-*`, the newest of 12 candidates that passes). Without
  one, lost rows stay lost. `*.damaged.bak` snapshots are never used as a source.
- **Dry-run still takes the lock and makes a snapshot**; only the live file is untouched.
- **`HERMES_KANBAN_TASK*` is stripped** from the child; use `HERMES_RECOVERY_HOLDER_TASK` if you need the holder
  card shown.
- **Delegated child contexts** may not run recovery (fenced by `kanban_path_is_fenced`).
- **`/proc` only**: stranded-holder detection and the process-start check are Linux-only; elsewhere they are
  skipped (no stranded check, pid-liveness only).
- `installed_unverified` means the live board may be bad: inspect the report and the damaged snapshot before
  trusting it.

## Could this be a plugin instead?

No. The module itself is standalone, but it needs core hooks: the dispatcher's pause path that calls
`_start_auto_recovery`, the `hermes kanban recover` subcommand dispatched before `init`, the connect guard's
classification helpers, and the sync client's reconcile tombstone check. A plugin could ship the module and CLI
entry but cannot intercept the dispatcher pause or change reconcile. Keep it as core patches.
