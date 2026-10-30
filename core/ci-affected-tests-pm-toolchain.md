# Affected-test planning widens PM toolchain changes to the full Python suite

Patches: 1270

- `patches/1270-fix-ci-run-full-Python-suite-for-PM-toolchain-change.patch`

## Behavior

Shared PM interpreter/toolchain inputs now select the full Python suite instead of a narrow import-derived selection. A change only to `pm/lock.json` emits full mode, every configured slice and an empty selected-files list through the planner CLI. Unrelated PM artifact-mirror data retains the smoke selection.

## How it works

The planner's `WIDE` path patterns add `pm/lock.json`, `pm/environments.py`, `pm/testenv.py`, `scripts/_activation.sh`, and `scripts/ci/setup_toolchain.py`. Existing runner, environment launcher, planner, worker and setup-pm action paths stay broad. The matching path appears in the full-plan reason.

## Code

`scripts/ci/affected_tests.py` and `tests/ci/test_affected_tests.py`.

## Tests

Included tests, not re-run for this documentation update:
- `test_shared_toolchain_changes_run_the_full_suite` checks full mode, matching reason, no selected files and `MAX_SLICES` for the parametrized shared paths.
- `test_all_activation_inputs_require_full_suite` checks activation inputs against the planner.
- `test_lock_only_interpreter_bump_emits_full_cli_plan` creates git revisions with only a lock interpreter change, checks the changed-file list and invokes `main` to verify GitHub output and files output.
- `test_unrelated_pm_data_keeps_narrow_selection` preserves the narrow smoke result for `pm/artifact-mirror.json`.

An actual hosted CI run and execution of the selected full suite are untested by this documentation update; the patch tests selection, not the suite itself.

## Applying

Apply the complete numbered series at its documented merge-base with `git am -3 --empty=keep`; see `README.md`. Keep this with the affected-test planner and PM activation machinery it extends.

## Gotchas

This is a closed set of shared toolchain paths, not a blanket full-suite rule for everything under `pm/`. Full mode is a planning result; it is not proof that downstream CI completed.
