# Test isolation and synchronization followups

Patches 0631 (`b930129eb9`), 0638 (`b98c132d9d`) and the downloader portion of 0640 (`1bc374222c`). Recovery lint changes in 0639–0640 are documented in [kanban-board-recovery.md](kanban-board-recovery.md).

## Behavior

These are test-only changes: profile alias discovery uses a sandbox wrapper directory rather than real home symlinks; the git-consumer fallback test reaches its injected package-manager failure past the POSIX target-gap check; downloader integrity counters synchronize with worker progress instead of relying on unsynchronized counter observations.

## How it works

The shared fixture patches the profile alias wrapper-directory seam without globally redirecting HOME. The git test removes the earlier target-gap refusal from its intended fallback path. Downloader tests coordinate counter observations with the background work.

## Code

`tests/conftest.py`, `tests/tools/test_git_consumer_environment.py`, `tests/pm/test_downloader_integrity.py`; 0640 also adjusts `tests/test_conftest_kanban_env_scrub.py` and recovery lint.

## Tests

Regression files: `tests/tui_gateway/test_profiles_list_canonical_session.py`, `tests/tui_gateway/test_profiles_list_worker_session.py` (profile discovery), `tests/tools/test_git_consumer_environment.py`, `tests/pm/test_downloader_integrity.py` and `tests/test_conftest_kanban_env_scrub.py`. The exact execution paths and results are in the mirror verification record; no user-facing behavior is added by these fixtures.

Patch **0651** (`90a63800fb`) additionally scrubs inherited systemd unit markers per test: `INVOCATION_ID`, `NOTIFY_SOCKET`, `WATCHDOG_USEC`, `WATCHDOG_PID`, `JOURNAL_STREAM` and `MANAGERPID`. This keeps ordinary spawn tests in-process and prevents a test from notifying a real unit. Tests that exercise systemd opt in explicitly. `tests/test_hermetic_env_systemd_markers.py` verifies marker removal and in-process worker dispatch; both tests passed under the canonical runner.

Patch **0652** (`a934e39df2`) binds the wrapper directory explicitly in backup/doctor tests. Canonical-runner targeted cases `test_import_skips_profile_dirs_without_config` and `test_profile_alias_status_follows_the_wrapper_the_profile_really_has` both passed. Its safe recovery PID probe is covered in the recovery doc.

## Applying

Apply in series order, after the target-gap and existing test-isolation infrastructure.

## Gotchas

Do not disable the real-home guard to make these tests pass. A sandboxed fixture must exercise the intended branch, not stop at an unrelated earlier refusal.

## plugin?

No; repository tests and lint cleanup.
