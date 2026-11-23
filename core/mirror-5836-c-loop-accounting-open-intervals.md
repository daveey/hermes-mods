# Metrics: open and abandoned intervals in loop accounting (mirror 5712–5713, 5715)

## Behavior
- Thread accounting snapshots include still-open intervals via an open-interval registry overlaid at the native thread sampling boundary, avoiding negative residuals when a scope crosses the first snapshot.
- Incomplete open boundaries are marked invalid (`open_accounting_complete`) so consumers can reject them; this group does not prove every downstream collector enforces that requirement; tick-only or missing CPU never infers an open interval.
- Releasing a lease discards the open token without booking a pass; foreign-thread closes retire the interval before refusing CPU booking.

## How it works
`loop_accounting_open.py` plus `loop_accounting.py` and `host_loop_lease.py`; entry, close and sampling are serialized.

## Code
- `hermes_cli/loop_accounting.py`
- `hermes_cli/loop_accounting_open.py`
- `hermes_cli/host_loop_lease.py`

## Tests
Static source evidence (not executed for this documentation audit):

`tests/hermes_cli/test_loop_accounting_open_boundary.py` checks scope-crossing samples, nested union, no counter booking and incomplete boundaries. `test_loop_accounting_open_native.py::test_native_sampling_serializes_scope_close` checks serialization and `test_failed_precise_clock_keeps_raw_stats_but_invalidates_open_boundary` checks fallback. `test_loop_accounting_lease_retirement.py::test_discarded_lease_does_not_account_future_unscoped_cpu` checks abandonment. Downstream collector rejection of incomplete samples is not demonstrated by these cases (untested).

## Applying
Apply the listed patches in ordinal order to the files under Code, together with their test files.

## Gotchas
- Raw native failure telemetry is retained.

## plugin?
No. Core change.
