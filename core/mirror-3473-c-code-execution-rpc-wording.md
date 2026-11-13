# Code-execution RPC: accurate wire-bytes and oversize-result wording

Patches: **3462–3463**.

## Behavior
- The oversize tool-result error no longer tells every tool that "any change the call made stands". It now says that if the call changed anything, that change stands and should not be repeated to get the result.
- The `_wire_bytes` comment and docstring now state precisely what happens to a high+low surrogate pair: this only arises from non-file-read results (e.g. MCP or plugin tools), and on the remote side the pair becomes one astral character. The UTF-16 meaning survives; the code-point sequence does not (length 2 becomes 1).
- No functional change beyond the error wording.

## How it works
3462 rewrites the comment and the conditional side-effect clause in `tools/code_execution_rpc.py` and adds an assert for the new wording. 3463 corrects the "lossless join" claim, adds the caveat to the docstring, and drops a wording-only absence assert (a change detector) from the test.

## Code
- `tools/code_execution_rpc.py`
- `tests/tools/test_code_execution_terminal_full_output.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_code_execution_terminal_full_output.py`: `test_remote_file_rpc_bounds_a_large_non_terminal_result` (existing test; the patches add and then trim wording asserts in it; the name appears only in hunk context).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The surrogate-pair behavior is documented only; it is untested.
