# Lifecycle guard: scan files run as `python3 <file>`

Patches: **3437**.

## Behavior
- A runner file containing a gateway lifecycle command is refused when invoked as `python3 <path>` (also `python`, `python3 -u`, `/usr/bin/env python3`, and sudo/timeout/env wrappers), matching the existing refusal for `<path>` and `bash <path>`.
- `-c`, `-m` and `-` run no file; `-W`/`-X` take a value; an operand after `--` is the script; stream and FIFO operands are not opened.
- An unreadable or oversized interpreter-run file is allowed (not a refusal); only a lifecycle command found inside blocks. The scan is charged to the walk budget, and a file already shell-walked is not rescanned or recharged as Python.

## How it works
3437 treats interpreter-run files like a `.py` cron script in `check_gateway_lifecycle`: lifecycle regex with data-sink masking, never the shell reference walk. Remote-backend files are covered by a test.

## Code
- `cron/lifecycle_guard.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_lifecycle_guard_python_interpreter.py`: `test_interpreter_run_file_with_lifecycle_command_is_refused`, `test_non_file_interpreter_runs_do_not_read_the_operand`, `test_bare_wrapper_has_no_interpreter_operand`, `test_operand_after_double_dash_is_the_script_even_if_dashed`, `test_dash_operand_is_stdin_not_a_file_named_dash`, `test_stream_operand_is_not_a_script_file`, `test_fifo_operand_is_skipped_without_opening`, `test_shell_run_of_a_device_still_fails_closed`, `test_python_source_is_not_shell_walked`, `test_python_source_data_sink_mention_is_masked`, `test_unscannable_python_runner_is_allowed_not_refused`, `test_python_scan_is_charged_against_the_walk_budget`, `test_oversized_python_file_only_mentioned_in_inert_heredoc_is_allowed`, `test_python_file_only_mentioned_in_inert_heredoc_is_still_scanned`, `test_python_scan_does_not_hide_a_later_shell_walk_of_the_same_file`, `test_shell_walked_file_is_not_rescanned_as_python`, `test_shell_walked_file_is_not_charged_again_as_python`, `test_remote_backend_python_file_is_scanned`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Scanning is textual: a lifecycle command built dynamically inside the Python file is not detected.
