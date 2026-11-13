# Update shutdown: spent cron grace is not renewed

Patches: **3036**.

## Behavior
- During an update restart, a checkpoint-cron cut whose restart grace is already spent no longer has the 30 s cron floor restored by stop; an unfenced cut honors the spent grace. Reading the post-snapshot intent fails closed.

## How it works
3036 adds helpers in `gateway/restart.py` and uses them in `gateway/run_shutdown.py`.

## Code
- `gateway/restart.py`
- `gateway/run_shutdown.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_instant_update_cron_drain_latency.py`: `test_cron_floor_does_not_restore_spent_update_grace`, `test_post_snapshot_intent_read_is_fail_closed`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
