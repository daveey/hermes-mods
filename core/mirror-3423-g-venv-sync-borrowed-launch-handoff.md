# Borrowing launch hands its verdict across the store-Python relaunch

Patches: **3308**.

## Behavior
- A launch from a borrowing home that is served by the owner's committed generation no longer pays the currency probe and the owner probe (each bounded at 5 s) twice when it re-execs into the store Python.

## How it works
- 3308 sets the `_handoff` in `_prepare_borrowed_launch` for a served borrower and teaches `pm/environments.py` to run the owner's generation without rechecking. A borrower that synced its own generation hands nothing on.

## Code
- `hermes_cli/venv_sync.py`
- `pm/environments.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_borrowed_home_launch.py`: `test_a_served_borrowing_launch_hands_its_verdict_across_the_relaunch`, `test_a_borrowing_launch_that_synced_its_own_hands_nothing_on`.
- `tests/pm/test_borrowed_owner_environment.py`: `test_a_served_borrowers_relaunch_runs_the_owners_generation_without_rechecking`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
