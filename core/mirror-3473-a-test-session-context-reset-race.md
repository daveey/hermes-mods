# Test fixture: session-context reset waits for an in-flight import

Patches: **3432**.

## Behavior
- The autouse test fixture that resets session context vars no longer fails with "partially initialized module ... reset_session_vars" when another thread is still importing `gateway.session_context`.
- Storage-health latch tests no longer leak background incident-reporter threads.

## How it works
3432 makes `tests/_fixtures/session_context_reset.py` fetch the module via `importlib.import_module` when it is already in `sys.modules`, which waits on the module import lock. The latch test's autouse fixture stubs `schedule_incident`.

## Code
- `tests/_fixtures/session_context_reset.py`
- `tests/hermes_state/test_storage_health_latch.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/test_conftest_session_context_reset.py`: `test_reset_waits_for_an_import_in_flight_on_another_thread`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Test-infrastructure only; no runtime behavior changes.
