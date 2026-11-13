# execute_code: cap the spill a remote file-RPC terminal result inlines

Patches: **3348, 3349**.

## Behavior
- On remote file-RPC backends, a terminal() result inlines at most 1 MiB of the JSON-encoded spill; larger output keeps the window with `truncated: True` and `full_output_path`. The local socket transport keeps its 20 MB cap.
- The cap bounds the encoded JSON, not just the raw spill, since escaping can double newline-dense text.
- The tool doc line states that a long output comes raw from the spill.

## How it works
- 3348 adds the cap in `tools/code_execution_rpc.py` and updates the doc line and prompt-surface fixtures.
- 3349 measures the encoded result instead of the raw spill.

## Code
- `tools/code_execution_rpc.py`
- `tools/code_execution_tool.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_code_execution_terminal_full_output.py`: `test_remote_file_rpc_caps_shipped_full_output`, `test_remote_file_rpc_ships_small_full_output`, `test_terminal_doc_line_names_what_the_spill_omits`, `test_remote_file_rpc_bounds_the_escaped_result`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Base64 of the capped result can reach about 1.4 MB.
