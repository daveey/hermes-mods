# Session-start toolset allowlist (0477, 0486)

Source commit `54178461e4` on fork main (`feat(session-start): pin a closed toolset allowlist on a durable start for the whole session lineage`).

## Behavior

A trusted in-process resolver may return `StartIntent(..., toolsets=['runners', 'clarify', 'todo'])`. The chat that start creates is built with exactly those toolsets, and keeps them for every later agent build of that session: first turn, resume, reopen after restart, compression rotation and branches. Nothing in the session (config changes, `tools.configure`, MCP reload, between-turn refresh) widens it, and RPC clients never supply it: `session.start_request` still takes only `{profile, namespace, start_request_id}`.

Validation is closed: 1–64 distinct, known, concrete toolset names; `all` and `*` are refused; unknown names raise `ValueError` at `StartIntent` construction, so the start fails before any session exists. `toolsets=None` (default) is unchanged behaviour and keeps the old idempotency digest, so old retries stay valid. A non-empty allowlist is part of the digest (a retry with a different list is a conflict; order does not matter, the list is stored sorted).

The tool surface that results from `['runners','clarify','todo']` is the `runners_*` tools, `clarify` and `todo_list` plus the `tool_search`/`tool_describe`/`tool_call` bridge, which is itself scoped to that set. No terminal, execute_code, file, browser, delegate_task, kanban, cronjob or cross_session tools exist in the session, and the agent loop refuses any other tool name.

`session.start_request` results gain `toolsets` (empty list when the start had none) so a collector can read back what was pinned.

## How it works

- `tui_gateway/session_start_requests.py`: `StartIntent.toolsets`, `_validate_toolsets`, additive `toolsets_json` column, `StartStore.session_toolsets_for(db, session_id)` which walks `parent_session_id` to the root start row. No ledger/column means no pin; any other lookup error propagates.
- `tui_gateway/server.py::_make_agent`: looks the pin up for `session_id or key` and passes it as `enabled_toolsets`; stamps `agent._toolset_pin`. A failed lookup refuses the build rather than building a wider agent.
- `tools/mcp_tool_agent.py::_resolve_refresh_toolsets`: a pinned agent ignores any enabled-toolset override, which covers `/reload-mcp`, `tools.configure` and the between-turn capability refresh.
- `tui_gateway/contracts/sessions.py` and `apps/shared/src/gateway-contract.{generated.ts,openrpc.json}`: `toolsets` on the result.

## Code

- tui_gateway/session_start_requests.py, tui_gateway/server.py, tools/mcp_tool_agent.py, tui_gateway/contracts/sessions.py, apps/shared/src/gateway-contract.*
- tests/tui_gateway/test_start_toolsets.py

## Tests

24 tests in `test_start_toolsets.py`, written first (22 failed before the implementation, all for the missing feature): validation (empty, string, unknown, `all`, `*`, non-string, duplicates, too many, dict), immutability, absent allowlist keeps the old digest and `[]` status, durable/idempotent/conflicting retries, pin follows compression, branch and a re-opened database, unrelated sessions unpinned, missing ledger table, upgrade of an existing ledger, tool surface and scoped bridge excluding terminal/execute_code/delegate_task/files/kanban/cronjob/cross_session/browser, agent loop refusing names outside the surface, `_make_agent` using only the pin for the original and the compressed session, unchanged build without a pin, build refused when the lookup fails, refresh unable to widen a pinned agent, refresh unchanged without a pin.

Also run: session-start-requests (33), start-metadata, compute-host hydration, contract generation currency and `test_refresh_agent_mcp_tools` all pass. Not claimed: a real model emitting a tool call outside the allowlist (see the runners F2 live evidence).

## Applying

Apply patch 0477 after 0471. This mirror trails fork main (other commits since 0471 are not mirrored), so apply by cherry-picking `54178461e4` onto the fork, or take fork main at or after that commit. Deployment is `hermes update`; an already-running backend keeps the old code until it is restarted at an idle moment.

## Follow-up 0486

Source commit `4af6ea3a81` (`fix(session-start): no session store means no toolset pin, not a crash`). `_make_agent` looks the pin up on `_get_db()`, which is `None` when state.db cannot open (the gateway deliberately runs on without state.db features). `StartStore.session_toolsets_for(None, ...)` raised `AttributeError`, so every agent build without a store crashed. It now returns `None` (no ledger, so nothing pinned); a lookup failure on a real store still propagates and refuses the build. Test: `test_no_session_store_means_no_pin_and_the_build_proceeds` in `tests/tui_gateway/test_start_toolsets.py` (executed: passes).

## Gotchas

- Plugin toolsets validate against loaded plugins, so a plugin that registers the toolset (for example `runners`) must be loaded in the process that resolves the intent.
- The pin protects the Hermes session only; it does not constrain what the allowed tools themselves do. The runners tools go through the management broker, which enforces its own policy.
- Sessions that already exist before this lands are unpinned; only starts created after it carry an allowlist.
- `delegate_task` children are impossible because the tool is absent; if you add a toolset that can spawn agents to an allowlist, the children inherit the parent's `enabled_toolsets` rules, not a pin.
