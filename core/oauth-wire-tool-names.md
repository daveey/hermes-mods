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
Verified with the full 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105 series in the mirror regression run (October 16, 2026 PT).

## Applying

Apply the ordered patch prefix `0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096` from this mirror (or the complete
`0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105` series) to the fork's documented merge-base in a disposable checkout.
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
