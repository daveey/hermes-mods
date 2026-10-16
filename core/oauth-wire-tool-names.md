# OAuth wire names in tool bridges

Patch: `0096`.

## Behavior

`tool_call` and `tool_describe` accept the Anthropic OAuth wire spelling
`mcp__<tool>` when it resolves to a registered tool. Copied wire names no longer
produce misleading unknown-tool errors and repeated searches. A directly listed
tool still cannot run through the deferred bridge: the correction names the
canonical tool, asks for a direct call, and explicitly says it was NOT invoked.

## How it works

`canonical_bridge_tool_name` preserves registered names, bridge names, and
`connectors__` names first. For an otherwise unresolved `mcp__<bare>` it tries
registered `mcp_<bare>`, then `<bare>`, then OAuth reverse aliases imported from
`agent.anthropic_adapter`. Bare aliases are also supported: `chat_history_lookup`
and `mcp__chat_history_lookup` resolve to registered `session_search`.
Unresolvable names remain unchanged. Normalization happens before bridge-call
validation; `tool_describe` first honors exact current-tool and remote-schema
matches before canonicalizing.

## Code

- `tools/tool_search_validation.py`: `canonical_bridge_tool_name`,
  `normalize_tool_call_entries`, and `not_deferrable_error`.
- `tools/tool_search.py`: `dispatch_tool_describe` fallback lookup.
- `tests/tools/test_tool_call_wire_names.py`: regression coverage.

## Tests

```sh
scripts/run_tests.sh tests/tools/test_tool_call_wire_names.py
```

Covers deferred wire names, direct-tool corrections, preserved registered MCP and
connector names, unknown names, aliases, bridge recursion rejection, and describe.
Verified with the full 0001–0105 series in the mirror regression run (October 16, 2026 PT).

## Applying

Apply the ordered patch prefix `0001–0096` from this mirror (or the complete
`0001–0105` series) to the fork's documented merge-base in a disposable checkout.
This feature is patch `0096`; isolated application is not claimed or tested here.
Run the tests above from the resulting Hermes source root, not this mods repository.

## Gotchas

- This is name resolution, not permission elevation or automatic tool discovery.
  A tool must be registered; unknown wire names still fail.
- `mcp__tool_call` remains a forbidden bridge-within-bridge call.
- Exact registered MCP names are never stripped merely because they start with
  `mcp__`. The top-level transport name conversion is not changed by this patch.

## Plugin?

No. This repairs core bridge argument validation and schema lookup before plugin
handlers can execute.
