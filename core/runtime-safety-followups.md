# Runtime safety follow-ups

## Behavior

- 0272: timed-out condition-wake probes use portable process-tree termination, retaining direct process kill as a fallback.
- 0280: a deferred-tool lookup for a uniquely matched configured but disabled MCP server reports that configuration state instead of suggesting alternate names. It neither enables nor invokes the server.
- 0283: delegated-child restrictions remain in force when process ancestry cannot be determined. A complete walk proving detachment may clear leaked markers, while preserving explicit board location.
- 0285: the test-only live Git-config guard retains its original PATH through teardown, even when a test clears PATH before monkeypatch restoration.

## How it works

Wake cleanup delegates to `agent.deadline.kill_process_tree` with the platform's default hard kill. MCP diagnostics load configuration and sanitize server names; failure leaves authorization unchanged. Delegation treats missing parent lookup, self-parent loops and truncated ancestry walks as unknown, not detached. The Git-config fixture captures PATH at module import and passes it to its captured Popen implementation.

## Code

- `hermes_cli/kanban_wake_check.py`
- `tools/tool_search_validation.py`
- `agent/delegation_context.py`
- `tests/conftest.py`

## Tests

On the applied public series, the canonical runner passed:

- `tests/hermes_cli/test_kanban_wake_cleanup.py`: 3 tests.
- `tests/tools/test_tool_search_disabled_mcp.py`: 4 tests.
- `tests/agent/test_delegate_lineage_fail_closed.py`: 6 tests.
- `tests/test_live_git_config_guard.py`: 12 tests.

These are focused Python regressions on Linux, not a live Windows process-kill, MCP connection, Desktop or cross-host integration demonstration.

## Applying

Apply the ordered series through 0285, not these patches independently. The full 260-patch series applied with `git am -3 --empty=keep` at merge-base `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`.

## Gotchas

Unknown process ancestry deliberately retains restrictions; unavailable inspection is not proof that a delegated process became a top-level agent. The MCP message is diagnostic, not an authorization grant. The PATH change affects the test guard only, not runtime command selection.

## Why core rather than a plugin?

These changes repair existing core cleanup, deferred-tool validation, delegation identity and test isolation seams; no new plugin surface or installation is introduced.
