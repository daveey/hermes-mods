# Package manager: borrowing homes and tool-store pins

Patches: **3255, 3268–3269, 3274**.

## Behavior
- A launch under a `HERMES_HOME` that borrows the owner's checkout (a temporary or per-task home) runs the owner's committed dependency environment instead of syncing a full one or refusing with "no dependency environment is committed": the owner's generation is leased in place (read-only; the owner's facts and input stamps are never written) when the borrower has no selection of its own and the stamp for its first-sync request and plugin members equals the owner's. A borrower with other plugins/extras, or no owner generation, keeps syncing its own or refuses as before.
- A borrowing home still compiles bytecode eagerly when `PYTHONDONTWRITEBYTECODE` is set in the process environment (as in the container image), since lazy bytecode would otherwise never be written and every start would recompile.
- Docs-only: the borrower's decline of an optional default package is sticky until a successful same-target `hermes pm install NAME` (a `--target` install to another target leaves it); `run_tests.sh` opens the store read-only.

## How it works
- 3274 adds `pm.environments.borrowed_venv`, a `borrowed` launch-probe operation, `borrowed_venv_is_current` in `pm.install`, and leasing in `activate_dependencies`. 3255 reads `PYTHONDONTWRITEBYTECODE` from `os.environ` (the PythonEnvironment env drops `PYTHON*` keys). 3268/3269 are docstring and script-mode changes.

## Code
- `pm/environments.py`
- `pm/environment.py`
- `pm/install.py`
- `hermes_cli/launch_probe.py`
- `hermes_cli/venv_sync.py`
- `scripts/tool_store_pins.py`
- `scripts/run_tests.sh`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/pm/test_account_seed.py`: `test_borrowing_home_compiles_eagerly_when_lazy_bytecode_is_never_written`.
- `tests/hermes_cli/test_borrowed_home_launch.py`: `test_a_borrowing_launch_runs_the_owners_generation_when_it_serves`, `test_the_owner_check_needs_an_owner_generation_before_probing`, `test_borrowed_activates_then_reports_the_owner_generation_verdict`, `test_a_borrower_resolves_the_owners_generation_read_only`, `test_an_owner_record_outside_its_own_state_is_not_borrowed`, `test_the_owners_generation_serves_a_borrower_with_the_same_request`, `test_a_borrower_with_a_selection_of_its_own_keeps_it`, `test_an_owner_generation_missing_requested_extras_does_not_serve`, `test_a_store_python_borrower_activates_the_owners_generation`.
- 3268 and 3269 (docstring/script mode) have no tests.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A long-lived borrower root keeps skipping an optional default package the owner's store later gains, until a same-target `pm install NAME` clears the decline.
