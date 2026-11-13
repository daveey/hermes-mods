# execute_code remote file-RPC result bounds and encoding

Patches: **3386, 3403**.

## Behavior
- Every remote file-RPC result is bounded by what actually ships: the terminal encoded cap and the non-terminal cap are measured with the same encoder that sends the bytes.
- A lone surrogate in an RPC result (for example from a file read that decoded undecodable bytes) ships as its `\uXXXX` escape inside the JSON string, instead of raising in the sender and leaving the poller or local socket thread dead.
- Files shipped to the remote (script, tools module, kernel runner, env file) are encoded strict UTF-8 and raise before writing, as before.
- An oversize result error now says the tool ran and only its result was dropped, so a side-effecting call is not repeated.

## How it works
- 3386 adds `_wire_bytes` in `tools/code_execution_rpc.py`, used by `_remote_write`, the local socket's send path and the cap measurement.
- 3403 limits the escape behavior to RPC JSON responses (`rpc_result=True`), restores strict encoding for shipped files, scopes the docstring, and rewrites the oversize error text.

## Code
- `tools/code_execution_rpc.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_code_execution_terminal_full_output.py`: `test_remote_file_rpc_ships_a_lone_surrogate`, `test_remote_terminal_cap_measures_what_ships`, `test_remote_file_rpc_bounds_a_large_non_terminal_result`, `test_local_socket_ships_a_lone_surrogate`, `test_file_ship_refuses_a_lone_surrogate`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The round-trip claim is exact for lone surrogates only (noted in the code comment).
