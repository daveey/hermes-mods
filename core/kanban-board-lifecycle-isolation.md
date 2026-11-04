# Kanban board lifecycle isolation and busy budgets

Patches: **1703** (`08ad5d923b`, `fix(kanban): isolate session opens from board journal waits [daveey]`); **1704** (`f3e082fe58`, `fix(kanban): isolate session opens from board journal waits [daveey]`); **1705** (`43fc8d84e2`, `fix(kanban): serialize board lifecycles without global cache waits [daveey]`); **1706** (`0f2574c6dd`, `fix(kanban): bound reader lifecycle waits by caller budget [daveey]`); **1707** (`d040f572a9`, `fix(kanban): retry busy setup outside board lifecycle locks [daveey]`); **1742** (`ec2cfc7b93`, `[daveey] fix(kanban): content-key probe never spends a busy board's read budget`); **1754** (`edea0871e4`, `[daveey] fix(kanban): a zero-budget content-key probe never waits in SQLite on a warm cached reader`); **1755** (`3bff2cda23`, `[daveey] fix(kanban): evict a cached reader whose busy_timeout restore fails; share the 500 ms constant`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- Opening a session database no longer waits behind a Kanban board's journal or lifecycle lock (1703/1704).
- Board open/close lifecycles are serialized per board, without a process-global cache wait (1705), and reader lifecycle waits are bounded by the caller's own budget (1706).
- Busy setup is retried outside the board lifecycle locks, so one contended board cannot stall another board's open (1707).
- The annotation content-key probe opens with a zero busy budget and, on a warm cached reader, temporarily lowers the SQLite `busy_timeout` for its statements (1742/1754). A reader whose timeout restore fails is evicted instead of staying cached with a zero budget; the 500 ms constant is shared (1755).

## How it works

`kanban_dispatch_owner`, `kanban_generation`, `kanban_lockguard` and `kanban_reader_lifecycle` split the per-board lifecycle lock from the cache lookup; `cached_ro` in `kanban_session_activity` applies the probe's budget and restores the reader's timeout afterwards.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1703: (subject only)
- 1704: (subject only)
- 1705: (subject only)
- 1706: (subject only)
- 1707: (subject only)
- 1742: The annotation fingerprint and per-board memo each probe the board's content revision through cached_ro before the real read. Since reader opens retry for a 0.5 s busy budget (PR #475), a board held by a writer cost every poll three full budgets (~1.5 s) instead of one, and overlapping cold polls serialized past the 4 s bound in test_busy_board_open_does_not_block_session_poll. The probe already falls back to the file stat on any sqlite error, so it now opens with a zero budget and takes that fallback at once.
- 1754: PR #696 (ec2cfc7b931) made the content-key probe open with a zero busy budget, but a warm cached reader keeps its own 500 ms SQLite busy_timeout, so the probe body still waited the full 0.5 s on a busy board (measured: 0.50 s warm vs 0.00 s cold). cached_ro now applies a lower budget to the body's statements and restores the reader's timeout after.
- 1755: Review follow-up: a failed restore no longer leaves a zero-budget reader cached, the lifecycle opener uses _RO_BUSY_TIMEOUT_S, and a non-sqlite body error test replaces a warm-key test that also passed on the parent.

## Code

`hermes_cli/kanban_db_connect.py`, `hermes_cli/kanban_dispatch_owner.py`, `hermes_cli/kanban_generation.py`, `hermes_cli/kanban_lockguard.py`, `hermes_cli/kanban_reader_lifecycle.py`, `hermes_cli/kanban_session_activity.py`, `hermes_cli/sqlite_safe_read.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_kanban_auxiliary_lockguard.py`
- `tests/hermes_cli/test_kanban_content_key_busy_budget.py`: `test_busy_board_counts_pass_waits_once`, `test_cached_reader_budget_restored_after_non_sqlite_error`, `test_cached_reader_statement_budget_restored_after_no_wait_key`, `test_content_key_on_busy_board_falls_back_without_waiting`, `test_warm_key_read_on_busy_board_does_not_wait_in_sqlite`
- `tests/hermes_cli/test_kanban_lifecycle_serialization.py`: `test_board_lock_wait_honors_reader_budget`, `test_guarded_custom_factory_close_waits_outside_tracking_lock`, `test_same_board_open_close_churn`
- `tests/hermes_cli/test_kanban_lockguard.py`
- `tests/hermes_cli/test_kanban_open_session_isolation.py`: `test_busy_board_open_does_not_block_session_poll`, `test_identity_setup_failure_closes_tracked_connection`
- `tests/hermes_cli/test_kanban_reader_close_isolation.py`: `test_reader_close_does_not_wait_for_busy_native_opener`, `test_setup_retry_expires_and_restores_sql_budget`, `test_untracked_reader_close_while_native_open_waits_for_file_probe`
- `tests/hermes_cli/test_kanban_reader_lifecycle.py`: `test_cache_registry_is_available_during_board_io`, `test_recovery_refuses_busy_cache_registry`, `test_replaced_reader_is_not_closed_under_borrower`

Untested by their own patch (refactors, constants or CI routing): 1742.

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- A probe that hits a busy board falls back to the file stat, as before; the cost is one busy budget per poll, not three.

## Plugin?

No: these change core modules that plugins cannot hook.
