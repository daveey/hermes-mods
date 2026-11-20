# Warm-backend observer for late update pauses

Mirror entries 4960, 4961, 4962.

## Behavior

A successor backend can finish its boot scan while a predecessor resumes its first pause in place; if that predecessor pauses
again before exiting, the old empty-scan exit (or fixed 600 s deadline) left nobody to adopt its durable pause marker.
The paused-only recovery observer now stays alive as passive infrastructure:

- backs off to 30 s when no eligible pauses exist and keeps the 5 s cadence while pauses are pending;
- reloads the auto-continue config every iteration, skips skewed runtimes, and survives scan or config failures;
- exponentially backs off unscheduled attempts up to 300 s with a warning;
- normal crash recovery stays boot-only; writer, freshness, attempt, Stop and `session.resume` admission checks are unchanged;
- an optional stop event makes observer teardown explicit for fixtures.
- 4962: observer import failures are retried and the observer recovers to the fast cadence afterwards; a boot-scan failure still starts the observer.

## How it works

`tui_gateway/startup_resume.py` (`_observe_pauses`, `_paused_markers_remain`) loops with the cadence logic above, taking a `stopped` threading event.

## Code

`tui_gateway/startup_resume.py`.

## Tests

Source tests (not executed here): `test_startup_resume_late_pause`, `test_startup_resume_observer_guards`
(`test_observer_recovers_to_fast_cadence`, `test_boot_scan_failure_still_starts_observer`), `test_update_pause_recovery`, `test_session_outcome_restarts`.

## Applying

Use the full segmented recipe in the core mods [README](README.md) ("Applying"); the three entries touch one file and apply in order.

## Gotchas

- The observer is a long-lived thread; tests must pass a stop event or they leak it.
- The outcome classifier reporting an interrupted tool tail is correct and not related to this fix.
