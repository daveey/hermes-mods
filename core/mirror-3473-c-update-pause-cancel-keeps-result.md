# Update-pause cancel keeps the cancelled call's own result

Patches: **3470**.

## Behavior
- When a mid-tool core-update pause cancels a running tool call, the call's own text result is kept below the interrupted stamp instead of being replaced by the bare stamp. A resume handle (a job id at the start, a session id at the end) therefore survives, so the resumed turn does not poll with the wrong id.
- Long results keep head 1536 + tail 512 characters, closed by an end line so a kept "[Command interrupted]" tail is not rewritten away by replay cleanup.
- Empty or non-text results, and the executor's own "[Tool execution cancelled/skipped -" text, still get the bare stamp.
- Stamp wording changes from "re-run if still needed" to "check its outcome before re-running" for both the Desktop pause and the Kanban restart continuation.

## How it works
3470 adds `with_own_result` to `update_pause_cancel.committed` in `agent/update_pause_cancel.py`, and updates the stamp text in `tui_gateway/update_pause.py` and `gateway/session_kanban_continue.py`.

## Code
- `agent/update_pause_cancel.py`
- `tui_gateway/update_pause.py`
- `gateway/session_kanban_continue.py`
- `tests/tui_gateway/test_update_pause_mid_tool.py`
- `tests/gateway/test_kanban_restart_continuation.py`
- `tests/gateway/test_update_pause_composed.py`
- `tests/tools/test_update_survivable_processes.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_update_pause_mid_tool.py`: `test_a_cancelled_call_with_no_text_result_gets_the_bare_stamp`, `test_any_other_result_of_a_cancelled_call_is_kept_below_the_stamp`, `test_a_cancelled_calls_resume_handle_survives_the_stamp`, `test_a_long_own_result_keeps_its_head_and_tail_within_about_2kb`, `test_a_kept_interrupted_command_output_does_not_read_as_interrupted_on_replay`.
- The other three test files only have their expected stamp wording updated.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Results between the head and tail limits are truncated, so a handle in the middle of a long result is lost.
