# Value-free writer provenance and bounded WAL descriptor scanning

Patches: **2219–2220, 2230**.

## Behavior

Slow state writes identify bounded code callsites, callback identity and settlement stage on success or failure without logging SQL, parameters or exception values. WAL lock-guard descriptor scans avoid per-descriptor GIL handoffs on supported Linux runtimes.

## How it works

Write diagnostics run after commit/rollback in a `finally` block. They use sanitized code metadata, not callable representations or mutable names; opaque callable objects stay opaque. Diagnostic logging failures do not turn committed writes into errors.

The lock guard uses an import-time self-checked libc `fstat` path through `ctypes.PyDLL`. It reads the allocated descriptor-table size and scans while retaining the GIL. Unsupported runtimes, failed checks and tables above 8192 entries fall back to descriptor listing.

## Code

`hermes_state.py` and `hermes_state_write_diagnostics.py` handle provenance. `hermes_state_lockguard.py` and `hermes_state_lockguard_fdscan.py` handle descriptor identities and scanning.

## Tests

Source coverage, **not run in this mirror**, under `tests/hermes_state/`:

- `test_write_diagnostics.py`: wrapper attribution, rollback and broken logging.
- `test_write_diagnostics_settlement.py`: uncertain commit outcomes never replay; bulk and row-count wrappers retain provenance.
- `test_write_diagnostics_callback.py`: bound functions, immutable code identity and opaque callables.
- `test_lockguard_fd_scan_gil.py`: native identity parity, fallback, exact descriptor coverage, oversized tables and a busy-thread latency bound.

## Applying

Use the [README full replay procedure](README.md#applying), not an isolated patch selection.

## Gotchas

The native scan is restricted to Linux x86_64/aarch64 and enabled only after runtime checks. Its tests skip unsupported native paths. These changes improve observability and lock-guard scanning; they do not eliminate SQLite contention.

## Plugin

Core database transaction and WAL lock-safety internals; no plugin setup.
