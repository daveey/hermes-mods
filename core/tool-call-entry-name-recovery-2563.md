# tool_call recovers a tool name placed inside an entry's arguments

Patches: **2499, 2500**.

## Behavior

A `tool_call` `calls[]` entry with no top-level `name` used to fail with "requires a 'name'", costing a turn. Models emitted two recoverable shapes: the whole envelope nested under `arguments` (`{"arguments": {"name": X, "arguments": {...}}}`) and the name beside the tool's own params (`{"arguments": {"request": ..., "name": X}}`). Both are now lifted when unambiguous. A genuinely nameless entry now gets an actionable error naming the keys it carried, the names the batch does carry, that names are never inherited, the one-local-tool-per-`tool_call` rule when siblings are local, and that nothing ran. The message keeps the `calls[<n>] requires a 'name'` prefix.

## How it works

- `tools/tool_search_validation.py`: `normalize_tool_call_entries` calls `_lift_misplaced_entry_name(raw_args)` when an entry has no name. `arguments` may be a JSON string.
- Nested envelope (remaining keys are only `arguments`): lifted when the inner name is a registered tool, a `connectors__` name or a bridge name. A bridge name then gets its own "itself a bridge tool" refusal.
- Name beside params: lifted only when the name is a locally registered tool whose served schema declares properties, declares none called `name`, and (patch 2500) every other key is one of that tool's declared params. Otherwise `name` may be a real argument of a different tool, and rerouting would run a tool the model never chose, so the entry stays an error.
- `_declared_parameter_names` reads the served schema (`registry.get_definitions`, else `get_schema`); a lookup failure keeps the error.
- `_missing_name_error` builds the new message. Related: [tool-search-eager.md](tool-search-eager.md).

## Code

`tools/tool_search_validation.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tools/test_tool_call_entry_name.py` calls `normalize_tool_call_entries` and `resolve_underlying_call` directly with fake registered tools: nested envelopes (object, string, no arguments, connector), beside-params lifts and dispatch resolution, refusals for tools declaring `name`, unknown or schema-less tools, foreign keys, non-string/blank names and non-JSON, the bridge-name refusal, and the missing-name messages. Covered entry path: the validation/resolution functions only; an end-to-end model turn is untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- A name beside params is never lifted for tools with no declared properties or for connector names (no local schema).
- Names are never inherited from another entry; a batch of local tools is still refused.

## Plugin

Core change; no plugin required.
