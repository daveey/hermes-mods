# Advertised core-update drain holds expire while the writer lives

Patches: **2348–2349**.

## Behavior

Previously a live writer could keep an advertised drain hold forever. For records advertising `max_s`, an expired lease now releases the hold even while the writer lives, an invalid lease cannot hold dispatch indefinitely, and an expired profile hold does not hide an active root hold. Machine drain telemetry reports the same state.

## How it works

`agent/core_update_drain.py` evaluates lease age independently of writer liveness; the telemetry consumers apply the same rule so a hold is reported inactive when invalid or expired.

## Code

`agent/core_update_drain.py`; the telemetry alignment in 2349 is test-side (`tests/hermes_cli/test_machine_drain_metrics.py`).

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/agent/test_core_update_drain.py` (live writer lease expiry, invalid lease, expired profile hold vs active root, same tick claims without active hold) and `tests/hermes_cli/test_machine_drain_metrics.py`.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

A slow but live writer's work is no longer protected once its advertised lease expires; renewals must happen inside the lease. Legacy records without `max_s` retain their PID-only lifetime; this patch does not impose a timeout on them.

## Plugin

Core change; no plugin required.
