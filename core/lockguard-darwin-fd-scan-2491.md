# Darwin fd scanner for the state lock guard

Patches: **2469–2471**.

## Behavior

On macOS the WAL lock guard's scan of this process's open descriptors no longer hands off the GIL once per descriptor (the `os.listdir` plus `os.fstat` fallback), which stalled other threads. On Darwin arm64 and x86_64 it now lists descriptors with one bounded `proc_pidinfo` call and reads each `(st_dev, st_ino)` through a GIL-held native `fstat`, after proving at import that the results agree with `os.fstat`. Any doubt (unsupported architecture, missing symbol, truncated or malformed listing, mismatched identity) disarms the native path and the guard falls back to the existing directory-listing scan. A host whose `/dev/null` device number is positive (devfs numbering varies) no longer disarms the scanner, and NODEV sockets no longer corrupt the learned device conversion.

## How it works

`hermes_state_lockguard_darwin.build()` returns `(identity, descriptors)` or `None`. It uses `ctypes.PyDLL` (GIL held) for `fstat` (`fstat$INODE64` when present) and `proc_pidinfo(PROC_PIDLISTFDS)`, unpacking `dev_t` at offset 0 and `ino64_t` at offset 8 of the stat buffer and `proc_fdinfo` pairs from the list. A listing that fills the `MAX_FDS * 8` buffer, is empty, or is not a multiple of 8 bytes is rejected so the guard never receives a partial snapshot. CPython builds convert a negative `dev_t` to `st_dev` differently (signed versus uint64), so the conversion is learned lazily: nonnegative devices pass through, and the first consistent negative device (one `os.fstat` handoff, whose answer is authoritative) teaches the conversion; NODEV (-1, AF_UNIX sockets) is learned in a separate cell from ordinary negative devices. `hermes_state_lockguard_fdscan.open_fd_identities()` uses the Darwin listing when available, otherwise the Linux `FDSize` sweep. Related: [wal-holder-scan-single-flight.md](wal-holder-scan-single-flight.md), [kanban-lockguard.md](kanban-lockguard.md).

## Code

`hermes_state_lockguard_darwin.py`, `hermes_state_lockguard_fdscan.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_state/test_lockguard_darwin_safety.py`, `tests/hermes_state/test_lockguard_fd_scan_darwin.py`, `tests/hermes_state/test_lockguard_fd_scan_gil.py`. The Darwin tests are marked macOS-only; the safety suite injects fake native calls to exercise `build()`, and the real-libc tests exercise `fdscan.identity` and `fdscan.open_fd_identities`. No tool, CLI or REST path is claimed beyond the guard's own fd scan; the guard's holder-scan callers are untested here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

The native listing is not an atomic snapshot: descriptors can open or close between the list and the per-fd `fstat`. A process holding at least `MAX_FDS` descriptors falls back to the slower complete scan. Patch 2470 exists because a CI macOS host reported a positive `/dev/null` device that the first version treated as unprovable, failing four native tests; patch 2471 fixes a CPython 3.14 difference where NODEV stays signed.

## Plugin

Core change; no plugin required.
