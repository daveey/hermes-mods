# Kanban restart continuation keeps a stopped call's own result

Patches: **3494**.

## Behavior
- When a Kanban run is continued after a restart, a tool call that was stopped keeps its own text output (for example a killed command's "[Command interrupted]" output, a job id or an infrastructure apply log) below the interrupted stamp, instead of being replaced by the bare stamp.
- The kept result is closed by an end line, so replay cleanup and a retried prepare no longer treat it as interrupted.
- Executor cancel/skip text and rows appended for unanswered calls still get the bare stamp.

## How it works
3494 makes `_stop_result_rows` in the continuation code return (row id, content) pairs. Each rewritten row is built with `agent.update_pause_cancel.with_own_result(stamp, own)`, which keeps the call's own result (head 1536 + tail 512 characters) under the stamp. This mirrors an earlier fix for the Desktop update-pause cancel.

## Code
- `gateway/session_kanban_continue.py`
- `tests/gateway/test_kanban_restart_continuation.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_kanban_restart_continuation.py`: `test_a_killed_commands_own_output_survives_below_the_update_text`.
- Cancel/skip text and unanswered-call rows keeping the bare stamp: untested in these patches.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Long own results are truncated to head 1536 + tail 512 characters.
