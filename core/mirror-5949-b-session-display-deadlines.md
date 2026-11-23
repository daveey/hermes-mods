# Consume elapsed session display deadlines

Mirror ordinal: **5935**.

## Behavior

A session-source capture does not schedule another immediate capture merely because a display boundary has already elapsed. Future display deadlines still schedule refreshes; native source hints, capture cooldown and periodic reconciliation remain in place.

## How it works

`SessionsSource.refresh_at` computes the wall-clock delay before taking the source lock. A nonpositive delay returns without changing `_refresh`: the current capture has already observed that boundary. A future delay is converted to a monotonic deadline and combined with the existing refresh deadline using `min`.

This prevents an active annotation whose display boundary has passed from feeding its own publication back into an immediate capture loop.

## Code

- `hermes_cli/sessions_native_source.py`: `SessionsSource.refresh_at`.
- `tests/hermes_cli/test_sessions_native_source.py`: fake-clock regression.

Paths here refer to the Hermes source tree carried by the patch series, not executable modules in this mirror.

## Tests

**Source coverage only; not executed in this mirror.**

`test_elapsed_display_deadline_does_not_spin_idle_minute` in `tests/hermes_cli/test_sessions_native_source.py` has the reader repeatedly supply an expired display deadline. It captures and commits through a simulated idle interval, requires every returned deadline to be in the future, and bounds the number of captures. The existing `test_time_boundary_and_total_bytes` is adjacent future-boundary coverage.

A live HTTP session-list request, installed Desktop rendering and real idle-resource behavior are **untested here**. Live deployment is unproved; the source regression is not a deployment receipt.

## Applying

Use the **whole segmented recipe in [README Applying](README.md#applying)**, including its integration bridges and repair/verification steps. This is an append-only, dependent mirror series; do not apply this ordinal as a standalone mail or rewrite published patches.

## Gotchas

- An expired boundary is consumed, not moved to the current time. Clamping it to zero recreates the immediate wake loop.
- This change does not remove source reconciliation or certify that all annotation producers retire active state promptly.
- The fake-clock capture bound is a test invariant, not a live performance measurement.

## Why not a plugin

The refresh deadline belongs to the core session-source scheduler. A plugin could suppress a display but cannot safely repair the source owner's publication/deadline feedback without changing its scheduling contract.
