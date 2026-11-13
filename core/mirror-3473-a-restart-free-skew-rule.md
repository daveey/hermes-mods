# Restart-free code-skew rule: submodules, resolved git, no cached transient failures

Patches: **3426**.

## Behavior
- A submodule (gitlink) bump counts as a code change that needs a restart, even when git config hides submodule diffs; a gitlink bump plus tests-only changes no longer reads as restart-free.
- When git gives no answer (not found, OS error, timeout, unexpected exit code), the verdict is "unknown", not "skewed". `code_skew` caches only True/False per (boot, disk) pair, so one timed-out git no longer pins a pair as skewed for the life of the process.
- git is located through the platform resolver rather than a bare `git` on the ambient PATH; no git found means no verdict.
- After a tests/docs/CI-only drift, an ImportError is no longer labelled a code-skew import mix that needs a restart.

## How it works
3426 lists the range with `--ignore-submodules=none --submodule=short`. `range_restart_verdict` returns None on git non-answers; `range_restart_free` keeps its bool contract (None becomes False). `note_import_error` applies the same restart-free rule.

## Code
- `hermes_cli/restart_ignore_paths.py`
- `gateway/code_skew.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_restart_ignore_paths.py`: `test_a_gitlink_bump_counts_even_when_config_hides_submodules`, `test_git_is_resolved_through_locate_command`, `test_a_transient_git_failure_is_not_cached_as_skew`, `test_import_error_after_a_restart_free_drift_is_no_skew_mix`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
