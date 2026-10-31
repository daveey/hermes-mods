# Timestamp and revision headers for managed worker stderr

Patch: 1366 (`f06cd838bbc`).

## Behavior

Before a worker's first stderr line, the drain prepends one header:
`[pid N] @ <YYYY-MM-DDTHH:MM:SSZ> core=<10-character SHA>`.
Subsequent stderr lines keep the existing `[pid N] ` prefix, allowing parsers
to retain their per-worker grouping while relating crash blocks to deployments.
The regression checks two worker drains produce exactly one header each.

## How it works

`drain_worker_stderr` combines the header and first output line in one append
to the existing `~/.hermes/logs/managed-worker.log` path (profile home as resolved
by the existing logger). Header time is the drain's first-line time, not a
guaranteed exact crash timestamp. Empty streams do not emit a header (untested).
`_core_short_sha` prefers `code_skew._boot_fingerprint`; otherwise it caches a
file-based fingerprint lookup. It never shells out to git. An unavailable
fingerprint yields `unknown`; fallback/cache/error paths are untested by the new test.

## Code

- `gateway/session_managed_worker_log.py`: `_core_short_sha`, `_header`,
  and `drain_worker_stderr`.

## Tests

`tests/gateway/test_managed_worker_stderr_log.py::test_each_worker_block_starts_with_one_utc_and_core_sha_header`
uses two streams/pids and a stubbed boot fingerprint, checks parseable UTC times
within the observation window, the short SHA, and unchanged ordinary line prefixes.
The test was inspected, not executed for this documentation task.

## Applying

Apply 1366 over the existing managed-worker stderr logger in series order.
No new configuration is required; existing worker processes do not gain retroactive headers.

## Gotchas

The guarantee is one attempted header per drain invocation, not a global PID
registry. Log rotation or write errors can still remove/lose a header; those
interactions are untested by the added regression.

## Plugin side

The companion coach `signals.py` parser consumes headers for earliest/latest
crash time and core SHA details. It sets `at` to the latest crash only when every
PID is stamped. This plugin behavior is supplied context, not tested by this core patch.
