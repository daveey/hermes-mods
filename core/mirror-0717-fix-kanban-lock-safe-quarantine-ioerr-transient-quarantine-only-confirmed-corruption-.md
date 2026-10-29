# fix(kanban): lock-safe quarantine, IOERR transient, quarantine only confirmed corruption (#125)

Patch 0717, source `f9b92b497fdeb37a713753e83a73e845136b8855`.

## Behavior

fix(kanban): lock-safe quarantine, IOERR transient, quarantine only confirmed corruption (#125). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `from __future__ import annotations`
- `def _validate_sqlite_header(path: Path) -> None:`
- `def _backup_corrupt_db(path: Path) -> Optional[Path]:`
- `def _run_integrity_check(conn: sqlite3.Connection) -> list[str]:`
- `def _probe_for_corruption(resolved: Path) -> tuple[Optional[list[str]], Optional`
- `def _guard_existing_db_is_healthy(path: Path) -> None:`
- `def repair_db(db_path: Optional[Path] = None, *, board: Optional[str] = None) ->`
- `def stranded_sidecars(db_path: Any) -> list[str]:`
- `tracks live connections so raw reads happen only when none exist.`
- `def offline_file_access(path: Path | str, *, what: str = "read"):`
- `+"""Regression tests: a healthy kanban.db is never quarantined because of a stale`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> * fix(kanban): lock-safe quarantine reads; IOERR transient; quarantine only confirmed corruption
>
> A raw open()/close() of kanban.db plus -wal/-shm inside a process that holds
> connections (the quarantine fingerprint+copy2, the header probe) cancels the
> process's POSIX locks, including those of untracked read-only connections the
> registry cannot see. A sibling's last close then takes EXCLUSIVE and unlinks
> -wal/-shm, stranding every connection on a deleted generation: integrity_check
> reports phantom index errors / SQLITE_CORRUPT / SQLITE_IOERR on a healthy file,
> which then qualified for quarantine (50 MB .corrupt copies, each copy repeating
> the lock loss).
>
> - sqlite_safe_read: pread-from-cached-never-closed-fd helpers (header, sha256,
>   copy); quarantine and header validation use them, no in-process close() of the
>   live file or sidecars.
> - SQLITE_IOERR*/BUSY/LOCKED/CANTOPEN/FULL/NOMEM/PROTOCOL are transient: retried,
>   then raised raw, never quarantined (also when raised as DatabaseError).
> - Quarantine/REINDEX/repair_db require an independent confirmation: a process
>   holding deleted WAL/SHM raises KanbanDbUnreliableViewError; otherwise a fresh
>   read-only process must reproduce the failure.
> - Tests reproduce the strand with a live untracked connection and a sibling close.
>
> * fix(kanban): pin utf-8 on independent integrity child; update stale-run heartbeat test for claim-seeded heartbeat
>
> * fix(kanban): quarantine never pins a deleted -wal/-shm generation in-process
>
> Merging main's OFD lockguard exposed it: the lock-safe reader cached fds on
> -wal/-shm forever, so once SQLite unlinked that generation the process held
> 'kanban.db-wal (deleted)' fds. kanban_lockguard.stranded_sidecars and the
> /proc watchdogs (coach stranded_sqlite_watch, sqlite-live-guard
> stranded_restart, which restarts units) read that as a stranded process.
>
> - sidecars are copied by a child process (its close cannot touch our locks)
> - cached reader fds whose inode is unlinked are swept (closing a dead inode
>   cancels nothing that matters); retired fds too
> - kanban_lockguard.stranded_sidecars skips lock-safe reader fds, like kbc
> - regression test fails on the previous head
>
> ---------
>
> Co-authored-by: David Bloomin <daveey@users.noreply.github.com>

## Code

- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_lockguard.py`
- `hermes_cli/sqlite_safe_read.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_db_lock_safety.py`

New or changed test definitions present in the source diff:

- `test_ioerr_extended_codes_are_transient`
- `test_transient_ioerr_retries_then_succeeds_and_never_quarantines`
- `test_persistent_ioerr_propagates_raw_without_quarantine`
- `test_ioerr_raised_as_databaseerror_is_not_quarantined`
- `test_backup_does_not_cancel_locks_or_strand_the_process`
- `test_reader_fds_never_pin_or_fake_a_deleted_generation`
- `test_raw_open_close_is_the_hazard_this_guards_against`
- `test_header_probe_is_lock_safe`
- `test_lock_safe_helpers_copy_and_hash_match`
- `test_stale_view_failure_does_not_quarantine_healthy_board`
- `test_stranded_process_is_never_quarantined`
- `test_genuine_corruption_is_still_quarantined`
- `test_genuine_index_corruption_still_auto_repairs`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
