# Per-file test peak RSS: report, warn, and bring one file back under budget

Patches: 1686 1698 1699

- `patches/1686-daveey-run_tests_parallel-report-per-file-peak-RSS-w.patch`: the runner reports per-file peak RSS and
  warns on files over a threshold.
- `patches/1698-daveey-tests-bring-test_tui_gateway_server.py-peak-R.patch`: three fixes that cut
  `tests/tui_gateway/test_tui_gateway_server.py` from about 550 MB to about 390 MB peak RSS (figures from the commit
  message, measured on the Linux CI host).
- `patches/1699-daveey-tests-guard-line-on-the-hub-state-isolation-t.patch`: test-only follow-up; adds the
  unittest-discovery guard to the new isolation test and compacts 1698's stub to keep the server test file at its line
  cap.

Related: [test-runner-host-slots.md](test-runner-host-slots.md) and
[test-run-summary-errors.md](test-run-summary-errors.md) cover other parts of `scripts/run_tests_parallel.py`'s summary.

## Behavior

**1686 (runner).**

- `scripts/run_tests_parallel.py` reaps each per-file pytest child with `os.wait4` and records its `ru_maxrss` (the
  peak of the child and every descendant it reaped). The max over a file's attempts is kept.
- The per-file time distribution prints `Top N peak RSS:` (N = up to 5) after `Top 10 slowest:`, then one
  `⚠ high-memory test file: <path> peaked at <M> MB RSS (> <T> MB, HERMES_TEST_FILE_RSS_WARN_MB)` line per file over
  its threshold. A file exactly at the threshold does not warn.
- Threshold: `HERMES_TEST_FILE_RSS_WARN_MB` (default 512). Empty, unparsable or non-positive values keep 512 rather
  than disabling the check. A file can raise its own threshold with a line `# run_tests_parallel: rss-warn-mb=<N>`.
- Warning only: the exit code is unchanged. Nothing is printed when no file was measured (Windows has no `os.wait4`).
- `scripts/run_tests_parallel_rss.py` is added to the Python test-harness paths in `scripts/ci/classify_changes.py`,
  so changing it selects the macOS state lane like the other runner files.

**1698 (one heavy file).**

- `tests/conftest.py` sets a process-wide `HERMES_HUB_STATE` to a temp path that never exists
  (`hermes-test-no-hub-state-<pid>`). The per-test fixture already pointed it at an empty dir, but background threads a
  test leaves running read it after that monkeypatch is undone, and loaded the host's real hub replica (about 40 MB of
  rows per read).
- `plugins/dashboard_auth/basic`: `_DUMMY_HASH` (verified against for unknown usernames so "no such user" and "wrong
  password" take comparable time) is now a fixed, well-formed `scrypt$N$r$p$<zero salt>$<zero key>` string with the
  live cost parameters, instead of `hash_password(...)` at import. Import no longer runs a 16 MiB scrypt (plugin
  discovery re-imported it about 150 times in that test file). Verifying against it still runs one full-cost scrypt,
  and no password derives the all-zero key, so it never verifies.
- `test_interrupt_error_after_tts_stop_still_resumes_wake` stubs `server._get_compute_host_supervisor`; it previously
  built a real `HostSupervisor` and spawned a compute-host process that outlived the test.

## How it works

- `run_tests_parallel_rss.reap_with_rusage(proc)` replaces the Popen instance's POSIX reaper `_try_wait` with one
  that calls `os.wait4` and stores the rusage on `proc._hermes_rusage`. It patches the instance rather than
  subclassing, so tests that substitute a fake `subprocess.Popen` keep working; it is a no-op without `os.wait4` or
  `_try_wait`. A `ChildProcessError` returns `(pid, 0)`.
- `maxrss_bytes` normalizes `ru_maxrss`: bytes on macOS (`darwin`), KiB elsewhere.
- `_run_one_file_attempt` calls `reap_with_rusage` right after `Popen` and `record_peak_rss(file,
  proc_peak_rss_bytes(proc))` in its `finally`. `print_peak_rss_report(repo_root)` runs after the slowest-files list.
  `PEAK_RSS` is guarded by a lock because attempts run on worker threads.

## Code

1686: `scripts/run_tests_parallel_rss.py` (new), `scripts/run_tests_parallel.py`, `scripts/ci/classify_changes.py`;
copies of the new helper added to the runner test fixtures in `tests/ci/test_flaky_kanban.py`,
`tests/ci/test_macos_state_routing.py`, `tests/scripts/test_run_tests_parallel.py`,
`tests/scripts/test_run_tests_parallel_slow_cases.py`.
1698/1699: `tests/conftest.py`, `plugins/dashboard_auth/basic/__init__.py`,
`tests/tui_gateway/test_tui_gateway_server.py`, `tests/test_conftest_hub_state_isolation.py` (new),
`tests/plugins/dashboard_auth/test_basic_dummy_hash.py` (new).

## Tests

`tests/scripts/test_run_tests_parallel_rss.py` (1686; raises its own threshold to 1024 MB with the marker):

- `test_maxrss_is_bytes_on_macos`, `test_maxrss_is_kib_elsewhere` (`linux`, `freebsd14`).
- `test_warn_threshold_env`: default 512, override 300, garbage and `0` keep 512.
- `test_report_lists_top_five_and_warns_only_over_threshold`: ordering, top 5, 512 MB at the threshold does not warn.
- `test_file_marker_raises_that_files_threshold`, `test_no_measurements_print_nothing`.
- `test_rusage_popen_records_the_childs_peak` (POSIX): a real child allocating 64 MB is measured at ≥ 64 MB.
- `test_runner_warns_about_a_600mb_file_and_keeps_exit_code` (POSIX): runs the real runner on a probe dir with a
  600 MB test and a light test; exit 0, `Top 10 slowest:` and `Top 2 peak RSS:` printed, exactly one warning naming
  `test_hog.py`.
- `tests/ci/test_macos_state_routing.py`: the new helper path routes to the macOS state lane (one added parameter).

1698/1699:

- `tests/test_conftest_hub_state_isolation.py::test_hub_state_outside_the_test_scope_is_never_the_real_replica`:
  after `monkeypatch.undo()`, a thread sees a non-default, nonexistent `HERMES_HUB_STATE` and
  `kanban_session_activity._hub_db_path()` returns `None`.
- `tests/plugins/dashboard_auth/test_basic_dummy_hash.py`: `test_importing_the_plugin_runs_no_scrypt`,
  `test_dummy_hash_carries_the_live_cost_parameters`, and
  `test_unknown_username_still_pays_one_full_cost_scrypt_and_is_refused` (three passwords, including the old dummy
  password and empty): exactly one scrypt at live cost, login refused, `_verify_password` false.
- The commit message says both new test files were RED on the old code.

The mirror pass did not rerun these tests.

Untested: the 550 → 390 MB figures for `test_tui_gateway_server.py` (measured, not asserted by any test); the
compute-host stub in `test_interrupt_error_after_tts_stop_still_resumes_wake` (no test asserts no process is spawned);
the Windows no-op path of `reap_with_rusage`; timing parity between unknown-user and wrong-password logins (only the
scrypt call count and parameters are checked).

## Applying

Apply 1686, then 1698 and 1699, in series order. 1686 changes `scripts/run_tests_parallel.py` and expects the
host-slot helper (`scripts/run_tests_host_slots.py`) to exist. 1699 expects the unittest-discovery guard convention
from earlier test-isolation patches. No runtime configuration besides `HERMES_TEST_FILE_RSS_WARN_MB`.

## Gotchas

- Peak RSS includes reaped descendants. A file that spawns a big subprocess is charged for it; raise its threshold
  with the marker if that is intended.
- Any test-runner fixture that copies `scripts/run_tests_parallel.py` into a temp root must also copy
  `run_tests_parallel_rss.py`, or the runner import fails.
- A test that needs a real hub replica must set `HERMES_HUB_STATE` itself; the suite default never exists.
- `_DUMMY_HASH` must keep matching the live scrypt parameters if they change; the parameter test checks this.

## Plugin?

No. The runner and the suite conftest are core test infrastructure. 1698 touches the bundled `dashboard_auth/basic`
plugin inside core, not a hermes-mods plugin.
