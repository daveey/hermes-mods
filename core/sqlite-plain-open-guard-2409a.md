# SQLite plain-file open guard and forensic copy fence

Mirror entries: [2278](patches/2278-daveey-lint-forbid-plain-file-calls-on-SQLite-shm-wal-sidecars-check-only-sqlite-sidecars.patch), [2279](patches/2279-daveey-cover-database-and-journal-paths-in-SQLite-open-guard.patch), [2280](patches/2280-daveey-fence-forensic-copies-for-justified-SQLite-guard-exemption.patch).

## Behavior

- Add scripts/check --only sqlite-sidecars and lint wiring to reject unmarked production plain-file calls on recognizable SQLite paths.
- Extend detection from -shm/-wal to .db/-journal, including byte literals and tracked path aliases/attributes.
- Avoid raw sidecar copies in quick backup and fence forensic copies against live or concurrent connections.

## How it works

The AST checker follows path-bearing literals through assignments, loop targets, attributes and nested scopes, and recognizes selected open/read/write/copy call forms. A justified sqlite-sidecar-open: ok marker on the same or preceding line is a narrow exemption; the baseline is empty. Production reads should use sqlite_safe_read. Quick backup skips sidecars and routes databases through safe copying; detached transfer extraction documents its offline justification. Forensic copies now hold offline_file_access for the whole main-file/sidecar copy, not only an earlier preflight.

## Code

- `.github/workflows/lint.yml`
- `hermes_cli/kanban_db_recover.py`
- `hermes_cli/session_recovery.py`
- `scripts/check`
- `scripts/check_sqlite_sidecar_opens.py`
- `hermes_cli/backup.py`
- `hermes_cli/kanban_transfer.py`
- `hermes_state_repair.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/scripts/test_check_sqlite_sidecar_opens.py` — `test_sidecar_file_calls_are_flagged`, `test_a_variable_assigned_from_a_sidecar_path_is_followed`, `test_attributes_loop_variables_and_augmented_assignments_are_followed`, `test_names_are_tracked_per_function_without_flow_and_any_argument_counts` (additional cases in this file).
- `tests/scripts/test_code_health_cli.py`
- `tests/hermes_cli/test_backup_stability.py` — `test_quick_snapshot_never_raw_copies_board_sidecars`, `test_quick_snapshot_listing_ignores_partial_directories`.
- `tests/hermes_state/test_repair_copy_fence.py` — `test_copy_rejects_connection_opened_after_preflight`, `test_copy_holds_fence_against_concurrent_opener`.

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

Closing a plain descriptor can discard process-scoped SQLite locks even while another connection is writing. An exemption comment without a real offline fence is not safety. This is a syntactic lint, not complete dataflow analysis: paths with no identifying literal and arbitrary aliased open functions can escape detection. Tests and the safe-read helper are excluded from the production scan.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
