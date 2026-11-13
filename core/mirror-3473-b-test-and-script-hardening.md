# Test-runner seed opt-out parity and MCP refresh test fix

Patches: **3456, 3458**.

## Behavior
- `scripts/run_tests.sh` trims whitespace around `HERMES_PM_ACCOUNT_SEED` before the case-insensitive opt-out match, so values like ` no ` disable tool-store borrowing the same way they disable pm borrowing.
- The pm seed marker is documented as a hint (unlocked read-modify-write; a re-merge is idempotent), and the env var's justification is recorded in the pm docs.
- The MCP tools refresh thread-safety test now reads `agent.tools` and `agent.valid_tool_names` together under the publish lock, removing a CI flake caused by a torn read in the test itself, and asserts the workers finished.

## How it works
3456 moves the check into `_account_seed_enabled` in `scripts/run_tests.sh`, with comments in `pm/packages.py` and `pm/AGENTS.md`. 3458 changes only the test to observe the pair under `_mcp_agent._agent_tools_lock` and join with a generous timeout.

## Code
- `scripts/run_tests.sh`
- `pm/packages.py`
- `pm/AGENTS.md`
- `tests/pm/test_account_seed.py`
- `tests/tools/test_refresh_agent_mcp_tools.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/pm/test_account_seed.py`: `test_run_tests_opt_out_matches_pm`.
- 3458 edits `tests/tools/test_refresh_agent_mcp_tools.py` (the existing refresh thread-safety test) without adding test names.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The seed marker comment and docs changes are not covered by tests.
