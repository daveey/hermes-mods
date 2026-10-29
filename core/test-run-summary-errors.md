# The parallel run summary counts setup errors

Patch **0689** (`ed23639b30`).

## Behavior

`scripts/run_tests_parallel.py`'s summary line appends `, N errored` when pytest reported setup/teardown errors. Exit code is unchanged (still non-zero when a file errors). A run that only passes and fails still prints the old `N tests passed, M failed` line with no errored clause.

## How it works

`_on_done` already folded pytest's `errors` into `tests_collected` and summed only `failed` for the summary. The summary now also sums `errors` and appends the clause when that sum is non-zero. Signal-death crash notes are unchanged.

## Code

`scripts/run_tests_parallel.py`.

## Tests

`tests/scripts/test_run_tests_parallel.py::test_setup_errors_are_counted_on_the_summary_line` runs a file whose fixture errors (`test_a`, `test_b` depending on `broken`) and asserts the summary names the errors. This mirror did not re-run it.

The terminal `scripts/run_tests.sh` entry point is untested here. Only the parallel runner's summary line is covered.

## Applying

`git am -3` 0689. Script plus its unit test.

## Gotchas

- A green-looking "0 failed" line from before this patch can still be a setup error. Read the errored clause, or the exit code.
- Errors are not added into the failed count. They are a separate clause.

## plugin?

No.
