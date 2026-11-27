# Fact change feed: bounded failure logging and busy-open tolerance

Patches: 5953, 5954, 5955, 5961, 5964, 5965.

## Behavior

- A failing capture no longer logs a traceback on every retry. A streak logs one traceback at its start; each new exception type in the streak is traced once, so a real defect after a busy start stays visible.
- Busy/locked SQLite opens are treated as expected contention: a busy failure on the first attempt logs at DEBUG, never with a traceback. Persisting failures warn at max backoff and again every fixed number of attempts.
- The feed health callback follows a similar rule: its first failure of each exception type in a streak is traced once, repeats log one line (WARNING at the reminder points, DEBUG otherwise). The busy exemption itself is applied to capture failures only. The busy-detection helper tolerates foreign SQLite error-code values.
- The capture's own read-only open now waits out a brief busy registry within the capture's time budget (default 20 ms) instead of failing at once; snapshot reads then run with busy_timeout reset to 0 so a busy read fails fast into the cheap feed retry rather than a paged re-read.

## How it works

fact_feed tracks a per-source failure streak (attempt count, exception types seen) and picks log level and traceback from it. fact_capture opens the board read-only with timeout equal to the capture budget (retry_open sleeps in short slices and holds no lock while waiting), then resets the connection's busy timeout.

## Code

Paths touched by these entries (non-test):

- `hermes_cli/fact_capture.py`
- `hermes_cli/fact_feed.py`

## Tests

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `tests/hermes_cli/test_fact_capture.py`
- `tests/hermes_cli/test_fact_feed.py`
- `tests/hermes_cli/test_moa_preset_deletion.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Formatting tracebacks under the shared logging handler lock can stall an event loop; that is the reason tracebacks are rate-limited rather than just demoted.
- The contention test releases the holder from inside the open's busy wait to avoid timer races; the fixture's frozen clock is kept so a loaded runner cannot trip the 20 ms deadline.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes the fact feed and capture open path inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
