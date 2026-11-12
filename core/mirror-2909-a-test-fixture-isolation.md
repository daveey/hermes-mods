# Race-resistant test fixtures and module-local platform seams

Patches: **2733, 2755, 2761, 2770**.

Related context: [test-stability-2678.md](test-stability-2678.md).

## Behavior

Required-extras collection probes no longer appear temporarily inside the repository's `tests/` tree, browser ownership fixtures wait for visible stand-in argv, and dashboard kill-grace fixtures publish complete PID files atomically. Darwin lockguard and repair tests patch local seams rather than mutating process-wide stdlib functions used by pytest itself.

## How it works

2733 creates the probe under `tmp_path`, loads real `tests.conftest` via `-p tests.conftest`, and pins the repository pyproject/rootdir. This prevents a concurrent tree scan from reading a just-deleted probe; the listed-fails case still proves the plugin hook. 2755 waits until the fake daemon stand-in exposes the expected argv before testing ownership. 2761 writes PID data to a staging file then renames it, avoiding an empty file observation from a starved child. 2770 introduces `_open`, `_close`, `_fstat`, `_machine`, `_load_libc` in `hermes_state_lockguard_darwin.py`, and `_monotonic` in repair snapshot deadlines; fixtures patch those names instead of shared `os`, `platform`, `ctypes` or `time` functions. Production calls still delegate to the same operations.

## Code

- `hermes_state_lockguard_darwin.py`
- `hermes_state_repair.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_dashboard_procs_kill_grace.py` (fixture/assertion changes; no test definition name appears in this diff).
- `tests/hermes_state/test_lockguard_darwin_safety.py`: `test_fakes_leave_shared_stdlib_untouched`, `test_runtime_dev_conversion`, `test_unsupported_architecture_disarms`, `test_probe_not_listed_disarms`.
- `tests/hermes_state/test_lockguard_fd_scan_darwin.py` (fixture/assertion changes; no test definition name appears in this diff).
- `tests/hermes_state/test_state_db_repair_non_destructive.py` (fixture/assertion changes; no test definition name appears in this diff).
- `tests/test_conftest_required_extras.py`: `test_collection_through_conftest`.
- `tests/tools/test_browser_use_cli_owned_daemon.py` (fixture/assertion changes; no test definition name appears in this diff).

Coverage is limited to the entry points and fixture controls above; live services, deployment and user-interface behavior are not established by this documentation task.

## Applying

Use the [README full replay procedure](README.md#applying). Apply these patches in published ordinal order with the intervening series, replay bridges and mandatory post-replay repair described there. They are incremental changes, not independently certified cherry-picks. This document does not claim that the full mirror replay or feature suites ran during the documentation pass.

## Gotchas

These changes improve test isolation, not runtime timeout or ownership policy. 2770 does touch production modules solely to provide seams. Linux fixture coverage of Darwin APIs is not a native macOS run. The browser and dashboard changes retain the underlying ownership/kill-grace assertions; a longer sleep alone is not the synchronization contract.

## Plugin status

Core/CI implementation; no plugin is added or required. A plugin cannot replace the shared database, admission, worker or workflow invariants documented here.
