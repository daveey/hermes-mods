# fix(kanban): retire all stale board handles before WAL reopen [daveey]

Patch 0946, source `1e1ec687ef81e7a18d8df348346a49ab3f3f5c37`.

## Behavior

fix(kanban): retire all stale board handles before WAL reopen [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def worker_claim_is_live() -> bool:`
- `def _safe_copy_db(src: Path, dst: Path, *, timeout_seconds: float = 10.0) -> boo`
- `+"""Retire every known handle to a dead Kanban WAL generation before opening another.`
- `class _GuardMixin:`
- `def _apply(conn: Any, held_path: Optional[str] = None) -> bool:`
- `def guard(conn: sqlite3.Connection) -> bool:`
- `def forward_argv(task, board: Optional[str], worker_cmd: list) -> Optional[list]`
- `def replica_task_resolution(ids: list[str], now: float | None = None) -> tuple[s`
- `def _open_ro(path: Path, check_same_thread: bool = True) -> sqlite3.Connection:`
- `def cached_ro(path: Path) -> Iterator[sqlite3.Connection]:`
- `def register_worker_session(session_db, session_id: str) -> bool:`
- `import time`
- `def gc_task_worktrees(conn: sqlite3.Connection, *, older_than_days: int = 7,`
- `def _sweep_repo(repo_root: str) -> None:`
- `def read_header_bytes_preopen(path: Path | str, *, length: int = 100, force: boo`
- `def test_claim_ownership_matrix(clear_kanban_env, tmp_path, overrides):`
- `+"""Auxiliary board readers must survive raw I/O while they hold a WAL view."""`
- `def test_kanban_modules_open_boards_only_through_the_guard():`
- `def test_crash_and_budget_continuation_survives_reopen(worker, outcome):`

## Code

- `agent/kanban_turn_recovery.py`
- `hermes_cli/backup_sqlite.py`
- `hermes_cli/kanban_generation.py`
- `hermes_cli/kanban_lockguard.py`
- `hermes_cli/kanban_placement_forward.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_worker_recovery.py`
- `hermes_cli/kanban_worktree_gc.py`
- `hermes_cli/sqlite_safe_read.py`

## Tests

Source test files changed by this commit:

- `tests/agent/test_kanban_turn_recovery.py`
- `tests/hermes_cli/test_kanban_auxiliary_lockguard.py`
- `tests/hermes_cli/test_kanban_lockguard.py`
- `tests/hermes_cli/test_kanban_worker_recovery.py`

New or changed test definitions present in the source diff:

- `test_claim_check_uses_guarded_wal_reader`
- `test_claim_check_fails_closed_without_a_board`
- `test_gc_reader_survives_stray_close`
- `test_placement_reader_survives_stray_close`
- `test_recovery_retires_all_alias_caches_and_idle_board_handles`
- `test_recovery_does_not_rollback_a_live_transaction`
- `test_unregistered_stranded_peer_refuses_retries_and_ro_fallback`
- `test_foreign_thread_handle_refused_until_owner_closes`
- `test_borrowed_cache_is_not_closed_or_waited_on_during_recovery`
- `test_gc_write_handle_survives_stray_close`
- `test_imported_backup_guards_source_and_preserves_snapshot`
- `test_preopen_probe_does_not_drop_untracked_readers_locks`
- `test_registration_uses_guarded_wal_board`
- `test_open_run_is_not_its_own_recovery`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
