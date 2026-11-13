# execute_code sandbox terminal() returns complete output

Patches: **3261–3263**.

## Behavior
- Inside `execute_code`, a script's `terminal(...)["output"]` carries the full command output from the terminal spill file (not the chat-sized head+tail window with the truncation marker) and `truncated: false`; the spill has no session pipe/cwd marker lines. Applies across the code-execution RPC transports (per the patch notes; tests are single-path).
- When the spill is unreadable or capped, the window stays and `truncated: true` is reported; the same holds when there is no spill at all (marker, note and total-chars fall back to the windowed result). Short output is marked complete.
- A refused command's `pending_approval` result keeps its exact shape (no added `truncated` key).
- The tool schema and stub docstring document the complete-output contract. A `write_file` warning for content containing a truncation notice is described in the 3261 commit message but is untested here.

## How it works
- 3261 post-processes terminal results in `code_execution_rpc`, reading the spill (20 MB cap). 3262 skips pending-approval results. 3263 falls back to the truncation marker when no spill path is readable.

## Code
- `tools/code_execution_rpc.py`
- `tools/code_execution_tool.py`
- `tests/fixtures/prompt_surface/surfaces.json`
- `tests/fixtures/prompt_surface/tools/execute_code.json`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_code_execution_terminal_full_output.py`: `test_sandbox_terminal_returns_complete_output`, `test_spill_file_has_no_shell_markers`, `test_spilled_output_replaces_window`, `test_unreadable_spill_reports_truncated`, `test_capped_spill_reports_truncated`, `test_short_output_is_marked_complete`, `test_pending_approval_result_keeps_its_shape`, `test_window_without_spill_reports_truncated`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- When the spill replaces the window, notes added after collection are not carried (documented in 3263).
