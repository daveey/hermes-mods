# Cron: deferred restart rerun stays pending

Patches: **3690**.

## Behavior
- When the draining owner refuses a pending restart rerun (`defer_unadmitted`), the re-armed rerun is still recognised as pending.
- Such a rerun is held while the host is not fully awake (DarkWake) and fires on full wake.
- A one-shot rerun held past `ONESHOT_GRACE_SECONDS` is no longer retired by the one-shot grace gate.

## How it works
3690: the kept marker previously stored only the rerun's original `at`, but re-armed `next_run_at=now`, so `pending_rerun` (which compares `next_run_at` to the marker) read False. The marker now also records the re-arm instant as `fire_at`; `pending_rerun` matches `next_run_at` against `fire_at` when present, else `at`. `at` keeps its meaning for `_skip_reason`. Test fixtures now stub `host_fully_awake` to True so existing rerun tests do not depend on host power state.

## Code
- `cron/restart_rerun.py`
- `tests/cron/test_restart_cut.py`
- `tests/cron/test_restart_rerun.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_restart_rerun.py`: `test_deferred_pending_rerun_stays_held_and_survives_the_oneshot_grace`.
- `tests/cron/test_restart_cut.py`: fixture change only, no new test names.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Depends on the earlier restart-rerun implementation in the series.
