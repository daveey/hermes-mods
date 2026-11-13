# Terminal masked-success hints

Patches: **3124, 3134**.

## Behavior
- `process_manage` `log` of an exited session carries `exit_code`, `completion_reason` and `termination_source` like poll/wait, so a masked missing command is flagged there too.
- "command not found" and "failed to run command" count as failures only at a `<program>: ` line head; quoted content (for example a piped `git show`) is not flagged.
- The missing-command hint keeps command paths containing spaces and flags `/usr/bin/env` shebang errors.

## How it works
- 3124 adds the exit fields to the exited-session log in `process_registry` and anchors the `_FAILURE_SHAPES` patterns.
- 3134 extends the hint extraction for spaced paths and env-shebang errors (no tests named in this patch beyond the shared file).

## Code
- `tools/terminal_hints.py`
- `tools/process_registry.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_terminal_hints.py`: `test_quoted_missing_command_message_not_flagged`, `test_shell_and_wrapper_line_heads_still_flagged`, `test_poll_result_hint_reads_output_preview`, `test_log_of_exited_session_gets_hint`, `test_log_of_running_session_has_no_exit_fields`.
- The 3134 behaviors (spaced paths, env shebang) have no new named test in the patch: untested by names read.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
