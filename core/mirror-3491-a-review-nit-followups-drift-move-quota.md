# Review follow-ups: code-skew drift cache, move-guard timeout cause, quota proof release

Patches: **3478, 3480–3481**.

## Behavior
- Code-skew watchdog: when the boot or disk commit no longer exists (git `merge-base --is-ancestor` exits 128 after a force-push and gc), the verdict is now a definite skew and is cached, instead of re-running git on every poll. An unreadable repo, a timeout, an OS error or missing git stays unresolved and uncached.
- Move guard: the cause text is pinned. A slow but successful multi-call check reads "unpushed commits in"; a git timeout in the unpushed check or the dirty check's hidden-index read reads "unchecked worktree ... (git timed out"; unbudgeted callers still fail safe toward True. The module docstring notes that a worker-first repin whose origin is an active, unheld registry machine with those labels skips the unpushed guard.
- Quota recovery: a failed release of a proof reservation in the gate is logged at DEBUG with traceback and never raises; the docstring now covers every release path.

## How it works
- 3478 makes `range_restart_verdict` ask `git rev-parse --verify --quiet --end-of-options <sha>^{commit}` on a 128; exit 1 yields a definite False. It also corrects the `note_import_error` docstring (git subprocesses with 15 s timeouts) and makes the `locate_command` patch in a test strict.
- 3480 adds tests (and a one-line source/doc change in the move module) for the timeout cause text under the `budget=` API.
- 3481 replaces the silent suppress in `_release_proof` with a DEBUG log and documents why the in-loop release before the CAS in `recover()` is kept.

## Code
- `gateway/code_skew.py`
- `hermes_cli/restart_ignore_paths.py`
- `hermes_cli/kanban_move.py`
- `hermes_cli/kanban_quota_recovery.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_restart_ignore_paths.py`: `test_a_missing_commit_is_a_definite_verdict_cached_as_skew`, `test_a_non_commit_object_is_a_definite_verdict`, `test_a_git_that_cannot_read_the_repo_stays_uncached`.
- `tests/hermes_cli/test_kanban_move_guard_timeout_cause.py`: `test_a_slow_multi_call_check_that_never_times_out_keeps_its_unpushed_cause`, `test_a_git_timeout_in_the_unpushed_check_reads_unchecked_however_fast`, `test_a_git_timeout_in_the_dirty_checks_hidden_index_read_reads_unchecked`, `test_the_checks_report_timeouts_only_to_a_budgeted_caller`.
- `tests/hermes_cli/test_kanban_quota_recovery.py`: `test_failed_gate_release_logs_debug_and_never_raises`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A repo that git cannot read is deliberately not cached, so the watchdog keeps retrying it.
- The recover() double release (in-loop and via the done-callback) is intentional; the DELETE is idempotent.
