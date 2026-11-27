# SQLite raw-reader reclamation cadence and GIL-held lockguard scans

Patches: 5985, 5986, 6117, 6119, 6120.

## Behavior

- Raw-reader descriptor reclamation is amortized for active main-file descriptors (fstat sweeps happen on a cadence instead of every header read), while sidecar (WAL/SHM) and retired-descriptor reclamation stay eager on every ordinary raw read.
- Where the native sweep is available and within its descriptor-table bound, the board lifecycle lock's stranded-sidecar scan uses GIL-holding libc readlink and path resolution uses GIL-holding realpath. This reduces repeated GIL yields while holding the lock; it does not guarantee absence of BoardLifecycleBusy. Unsupported or out-of-bound scans and unavailable native path resolutions fall back to the ordinary OS readers.
- Non-strict native path identity is preserved: dangling symlinks and dot components resolve like non-strict POSIX realpath (stack and cycle memo) rather than appending a strict-failure suffix.

## How it works

sqlite_safe_read keeps a sweep budget counter; hermes_state_lockguard_fdscan uses ctypes PyDLL handles; kanban_generation and kanban_lockguard route through them.

## Code

Paths touched by these entries (non-test):

- `hermes_cli/kanban_generation.py`
- `hermes_cli/kanban_lockguard.py`
- `hermes_cli/sqlite_safe_read.py`
- `hermes_state_lockguard_fdscan.py`

## Tests

Entry-path behavior above is source-reviewed but **untested in this review**. The files below identify source regression targets, not proof that this review exercised those entrypoints.

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `tests/hermes_cli/test_kanban_lifecycle_lock_syscalls.py`
- `tests/hermes_cli/test_sqlite_pread_sidecar_cadence.py`
- `tests/hermes_cli/test_sqlite_pread_sweep_budget.py`
- `tests/hermes_state/test_lockguard_fd_scan_gil.py`
- `tests/hermes_state/test_lockguard_realpath_missing.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Amortizing sidecar reclamation was tried and corrected: unlinked WAL/SHM descriptors must be released eagerly or writer-open scans fail closed.
- Tests warm the scan-module imports so the timing bound measures the scan, not a cold import.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes the SQLite access layer and board lock guard inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
