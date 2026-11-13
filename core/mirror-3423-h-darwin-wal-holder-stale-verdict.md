# macOS WAL-holder scan: serve an expired clean verdict while refreshing

Patches: **3395**.

## Behavior
- On macOS the deleted-WAL holder scan (libproc) could outrun its 5 s budget under load, so every writable SessionDB open after each 30 s clean-verdict TTL blocked for 5 s. An expired clean verdict whose key (sidecar identity including birth time) is unchanged, and whose pass is younger than 300 s, is now returned immediately while a single-flight pass refreshes it.
- A holder voids any earlier clean verdict, so a positive result is never served stale. A changed key, no prior verdict, or a verdict older than 300 s keeps the blocking join and fail-open behavior. Linux behavior is unchanged (synchronous `/proc` walk).

## How it works
- 3395 changes `_iter_darwin_sidecar_holders` in `hermes_state_dbfile.py` with `_DARWIN_FD_STALE_CLEAN_MAX_SECONDS` (300 s); it starts or joins the single-flight pass without waiting.

## Code
- `hermes_state_dbfile.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_state/test_darwin_fd_scan_stale_while_revalidate.py`: `test_expired_same_key_verdict_returns_at_once_and_refreshes`, `test_changed_key_still_blocks_and_reports_a_holder`, `test_positive_verdict_is_never_served_stale`, `test_verdict_older_than_stale_bound_blocks`, `test_no_prior_verdict_blocks`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- For up to 300 s a clean verdict may be served stale if a holder appears with the same sidecar key; the refresh then voids it.
