# CI diff-read failures and lifecycle guard interpreter coverage

Patches: **3552, 3553**.

## Behavior
- The supply-chain scan steps and the prompt-surface base snapshot step fail (exit 2 with an `::error::`, no `found=` output) when git cannot read the PR diff or base tree, instead of scanning an empty diff and passing. A PR that changes nothing still passes with `found=false`.
- The cron lifecycle guard now also reads script files run via `uv run`, `py`, `pypy[N]`, and programs fed on stdin (`python3 < f.py`, `cat f.py | python3`, `bash < f.sh`, `cat f.sh | bash`), refusing the same gateway-restart commands it already refused for `python3 f.py`.

## How it works
3552 makes `scan_supply_chain.pr_diff` raise `DiffUnavailable` on a non-zero git exit; each subcommand prints an `::error::` and exits 2. `extract_base_snapshot` now runs `git ls-tree --full-tree <base> -- <snapshot>` with `check=True`, so empty output means no snapshot while an unreadable tree or bad SHA fails.

3553 adds `_peel_uv_run` (a transparent wrapper for `uv run` only; `uv pip`, `uv tool run` and `uvx` are not peeled), `_PYTHON_RUNNER_RE` for `py`/`pypy[N]`, `_split_redirections` returning `<`/`<>` stdin files, operand carry-over for a piped `cat` in `_iter_segment_contexts`, and a wider `_PIPE_TO_INTERPRETER`. Python sources reached through `uv run` are not shell-walked.

## Code
- `scripts/ci/pr_diff_report.py`
- `scripts/ci/scan_supply_chain.py`
- `tests/ci/test_repo_guard_checks.py`
- `cron/lifecycle_guard.py`
- `tests/cron/test_lifecycle_guard_interpreter_runners.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/ci/test_repo_guard_checks.py`: `test_supply_chain_scans_fail_when_git_cannot_diff_the_pr`, `test_supply_chain_scans_pass_a_pr_that_changes_nothing`, `test_base_snapshot_extraction_tells_a_missing_snapshot_from_an_unreadable_base`.
- `tests/cron/test_lifecycle_guard_interpreter_runners.py`: `test_other_interpreter_spellings_are_scanned`, `test_program_fed_on_stdin_is_scanned`, `test_mentions_and_data_stay_allowed`, `test_python_source_run_under_uv_is_not_shell_walked`, `test_shell_command_string_flag_is_not_a_script_path`, `test_cat_options_are_not_piped_files`, `test_uv_run_peel_bounds`, `test_stdin_redirect_parsing`, `test_data_sink_piped_into_python_is_not_masked`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The lifecycle guard change is additive; no existing reference is removed.
