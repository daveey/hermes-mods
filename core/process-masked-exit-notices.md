# Background completion notices flag masked failures

Patch **1999**: source `5fdff78536d9948ae9c23f2fba32d69519cfd4db`, "fix(process): background notices flag
`; echo EXIT=$?` masked failures [daveey]".

## Behavior

Take a background command such as `hermes chat -q ... > log 2>&1; echo EXIT=$?` that prints `EXIT=1`.
It exits 0, because the final `echo` succeeds. Its completion notice used to read "completed normally (exit
code 0)". Now:

- **Agent notice.** The notice reads `Background process <id> exited 0 but output shows failure (exit code 0)`.
  A `Warning: …` line follows, saying that exit code 0 belongs to the trailing `echo ...$?`, that the wrapped
  command printed status N, and that the run should be treated as FAILED.
- **UI title.** The display title becomes "Background Process Exited 0 but Output Shows Failure: <cmd>". It
  previously said "Finished".
- **Exit code unchanged.** The exit code itself is never changed. The note is advice only.
- **When it fires.** The status-echo shape is a trailing `; echo|printf …$?` (or `${?}`) as the last
  statement. It fires only when the output's last whole-line printed status is nonzero. These forms count:
  `EXIT=1`, `exit=2`, `rc=1`, `exit code: 3`, `STATUS=127`, `ret_code=…` (case-insensitive, optional quotes).
  - The status line is searched for in the output's *tail* window (the last `_SCAN_CHARS` characters), so a
    status line at the end of long output is found. The failure-shape check still reads the leading window.
  - The status must stand alone on its line. A status in the middle of a line doesn't count.
  - The last status line wins.
  - Status 0 isn't flagged.
  - Status text without the echo wrapper isn't flagged.
  - Commands whose first token is a read-only head (`grep`, `cat`, `echo` …) are still never flagged.
- **`; true` and `; :`.** A trailing `; true` or `; :` joins the existing masking shapes (`| tail`, `|| echo`).
  It's flagged only together with a failure shape in the output, as those are.
- **Where the check runs.** It runs on the untrimmed output, before a subagent-owned notice is trimmed to its
  tail. It runs only for a plain `exited` completion with exit code 0. `killed`, `lost` and other completion
  reasons, and nonzero exits, are unchanged.

## How it works

- `tools/terminal_hints.py`:
  - `_STATUS_ECHO` matches the command and `_PRINTED_STATUS` matches the status lines.
    `_printed_nonzero_status` returns the last status found.
  - `annotate_masked_success` checks the status-echo shape first, then falls back to the existing
    failure-shape × masking-shape check.
  - A new `_MASKING_SHAPES` entry covers a trailing `; true` / `; :`.
- `tools/process_registry_notifications.py`:
  - `_masked_failure_note(evt)` gates on `completion_reason == "exited"` and `exit_code == 0`, then calls
    `annotate_masked_success(command, output)`. Any exception yields no note.
  - `format_process_notification` computes the note before trimming and passes it to
    `_completion_status`, which then returns "exited 0 but output shows failure". It also prepends the
    `Warning:` line.
  - `process_completion_display_text` swaps the "Finished" title.

## Code

`tools/terminal_hints.py`, `tools/process_registry_notifications.py`.

## Tests

I didn't run these tests for this document. They're listed from the source diff.

- `tests/tools/test_process_notification_masked_exit.py`:
  - `test_incident_notice_warns_instead_of_completed_normally`
  - `test_incident_display_title_not_finished`
  - `test_status_zero_still_completed_normally`
  - `test_readonly_grep_not_flagged`
  - `test_long_subagent_output_checked_before_trim`
  - `test_long_output_status_line_at_end`
  - `test_nonzero_exit_unchanged`
  - `test_killed_reason_unchanged`
  - `test_lost_reason_not_reinterpreted`
- `tests/tools/test_terminal_hints.py::TestStatusEchoWrapper`:
  - `test_incident_command_flagged_with_status`
  - `test_status_zero_not_flagged`
  - `test_readonly_head_mentioning_status_not_flagged`
  - `test_status_variants`
  - `test_status_line_beyond_head_window_still_flagged`
  - `test_status_text_without_echo_wrapper_not_flagged`
  - `test_status_mid_line_not_flagged`
  - `test_last_status_line_wins`
  - `test_trailing_true_with_failure_shape`
  - `test_existing_pipe_and_or_cases_unchanged`

The commit message says 9 new tests were red before the change and green after, and that the
notifier/hints/terminal/process_registry files passed (653 + 568 tests). These are the implementer's results.
I didn't reproduce them.

**Entry paths:** background completion notices and display titles are tested through the notification
formatters. `annotate_masked_success` is also what foreground `terminal` results call. The new status-echo
and `; true` shapes reach that foreground path only through the shared function. No foreground `terminal`
tool test or `execute_code` test exercises them, so those paths are untested.

## Applying

Apply with the series (README § Applying). The two edits are self-contained and depend only on the existing
`annotate_masked_success` / `_MASKING_SHAPES` structure.

## Gotchas

- **Only plain `exited` completions.** A masked failure in a job that was killed, or that the backend
  lost, gets no warning.
- **Last status line only.** If a command prints a nonzero status line and then a later `EXIT=0` line, it
  isn't flagged.
- **Only the trailing statement.** `cmd; echo EXIT=$?; other` doesn't match the status-echo shape: the
  `echo` must be the trailing statement.

## Plugin boundary

Background completion notices are formatted inside the process registry
(`format_process_notification`, `process_completion_display_text`). This patch adds no hook there, and a
plugin can't reword the notice before the agent or UI sees it. So this stays in core.
