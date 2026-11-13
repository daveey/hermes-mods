# Bounded worker test slots and CI wiring

Patches: **3068, 3077, 3078, 3079, 3080**.

## Behavior
- Test runs on a worker use a bounded number of host slots with a verified lock receipt; slot errors fail closed, a held lock is never reclaimed, and an inherited controller still caps xdist.
- Runner jobs are capped to the worker budget.
- CI fixtures and change classification cover the budget imports, and budget changes route to the standalone macOS state workflow (workflow wiring untested).

## How it works
- 3068 adds `scripts/worker_test_budget.py` and wires it into the host-slot and parallel runners and `tests/conftest.py`.
- 3077 gives runner probe fixtures the budget imports; 3078 extends CI classification and policy isolation; 3079 adds the workflow path trigger; 3080 aligns admission test doubles with the final-gate keywords.

## Code
- `scripts/worker_test_budget.py`
- `scripts/run_tests_host_slots.py`
- `scripts/run_tests_parallel.py`
- `scripts/ci/classify_changes.py`
- `tests/conftest.py`
- `.github/workflows/hermes-state-macos.yml`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/scripts/test_worker_test_budget.py`: `test_worker_fixed_host_domain`, `test_receipt_requires_live_exclusive_lock`, `test_inherited_controller_still_caps_xdist`, `test_worker_slot_errors_fail_closed`, `test_worker_never_reclaims_held_inode`, `test_receipt_in_real_child_and_bad_identities`, `test_runner_jobs_capped_to_worker_budget`.
- `tests/hermes_cli/test_kanban_per_profile_cap.py`, `tests/ci/test_flaky_kanban.py`, `tests/hermes_cli/test_machine_admission_metrics.py`: fixture adjustments only.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
