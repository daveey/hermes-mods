# Test-only follow-ups: upstream sync and lifecycle guard rc-file rows

Patches: **3719, 3720**.

## Behavior

No runtime behavior changes; both patches only touch tests.

- 3719: fork tests follow upstream signature changes after a sync: a counting `_seed_tree` wrapper accepts upstream's extra cache `root` argument, and a delegation route-conflict stub accepts `explicit_base_url` (upstream passes `delegation.base_url` through as `explicit_base_url`, and the stub's resolver keeps it).
- 3720: lifecycle guard tests now pin two rc-file cases in `_references_at` that had surviving mutants: an rc file right before the script operand or `-c` (`bash --rcfile R clean.sh`, `bash --init-file R -c 'echo hi'`) is refused, and a trailing value-less `--rcfile` does not fail the referenced-script walk (no "referenced-script walk failed" log record) and stays `(False, None)`.

## How it works

3719 adjusts test helper signatures. 3720 adds three refused rows to `test_shell_specific_option_values_find_the_script` and a new test for trailing value-less `--rcfile` forms (`bash --rcfile`, `bash -O extglob --rcfile`).

## Code

- `tests/pm/test_account_seed.py`
- `tests/tools/test_delegate_route_conflict.py`
- `tests/cron/test_lifecycle_guard_interpreter_runners.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_lifecycle_guard_interpreter_runners.py`: `test_trailing_valueless_rcfile_does_not_fail_the_walk`, plus new rows in `test_shell_specific_option_values_find_the_script`.
- `tests/pm/test_account_seed.py` and `tests/tools/test_delegate_route_conflict.py`: existing tests edited only; no new test names.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas

- 3719 depends on the upstream sync that introduced the `root` argument and `explicit_base_url`; applied without it, the adjusted tests are meaningless.
