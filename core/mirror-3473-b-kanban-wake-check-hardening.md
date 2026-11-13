# Kanban wake-check tick and CI sweep hardening

Patches: **3455**.

## Behavior
- `run_due_wake_checks(wait=True)` logs any exception from a pool future (WARNING with traceback) and still returns its result dict, instead of letting it escape the tick.
- A failure closing the DB after a successful CI-progress sweep is logged as a close failure ("closing ... failed after the sweep") and the sweep's woken ids are still returned, rather than being logged as a failed sweep and dropped.

## How it works
3455 adds a trailing `except Exception` around `fut.result` in `kanban_wake_check.py` and guards `conn.close()` separately in `kanban_ci_progress._sweep_db`.

## Code
- `hermes_cli/kanban_wake_check.py`
- `hermes_cli/kanban_ci_progress.py`
- `tests/hermes_cli/test_kanban_ci_progress.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_ci_progress.py`: `test_a_raising_pool_callable_does_not_escape_the_wake_check_tick`, `test_close_failure_after_a_sweep_keeps_the_result_and_says_close`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
