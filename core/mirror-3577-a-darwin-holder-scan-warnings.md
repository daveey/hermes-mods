# Darwin holder scan: warn on failing background refreshes

Patches: **3560**.

## Behavior
- A refresh-only Darwin deleted-WAL holder scan pass (one no caller blocked on) that expires or errors now logs one warning per watched base, rate-limited to once per base per 60 s, so failing background refreshes within the 300 s stale window leave a trace.
- A pass a blocking caller waited on does not log the refresh warning again (the caller already logged its own timeout).

## How it works
- 3560 tracks "refresh-only" as `blocked_on` (any caller ever waited) rather than `waiters == 0` at the end, logs before `done` is set, and uses `_DARWIN_FD_REFRESH_WARN_INTERVAL_SECONDS`. It also drops a dead `len(cached) > 2` check in `_darwin_clean_servable_stale`, since the cache value is always a 3-tuple.

## Code
- `hermes_state_dbfile.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_state/test_darwin_fd_scan_stale_while_revalidate.py`: `test_no_waiter_pass_that_expires_logs_one_rate_limited_warning`, `test_no_waiter_pass_that_errors_logs_a_warning`, `test_blocking_waiter_pass_does_not_log_the_refresh_warning`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The tests inject a slow or raising libproc stub, so they run on any OS rather than exercising real Darwin behavior.
