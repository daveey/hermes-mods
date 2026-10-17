# Keep chosen tools out of the tool_search bridge, plugin tools included

Patches:

- `patches/0114-feat-tool_search-tools.tool_search.eager-keeps-named.patch`: tools. New config key
  `tools.tool_search.eager` lists tools that are always sent with their full schema, never deferred.

What you see: tools you use all the time (for example `process_manage`, or a plugin tool like `cross_session`) are
in the agent's tool list directly. Without this the agent sees only their names in the `tool_search` catalog and
has to search/describe them first, or misses them. Stock Hermes can make a core tool eager by dropping it from
`tools.tool_search.defer`, but plugin and MCP tools always defer and no setting changes that.

## Behavior

- **Default `[]`**: upstream behavior.
- A name in `eager` is never deferrable: it is not deferred even when it is a plugin/MCP tool, and it wins over a
  `defer` entry for the same name.
- An eager tool is a direct tool, so the bridge refuses it like any core tool (`tool_call` returns the
  not-deferrable error that points at the direct call) and it drops out of the session's deferrable scope.
- A non-list value logs a warning and is ignored.

## How it works

- `tools/tool_search.py`: `ToolSearchConfig.eager_tools` (parsed from `eager`); `is_deferrable_tool_name(name,
  defer_tools, eager_tools)` returns False first for eager names; `classify_tools` takes `eager_tools`; every call
  site (`_deferrable_in`, `assemble_tool_defs`, `dispatch_tool_describe`, `scoped_deferrable_names`,
  `resolve_underlying_call`) passes the config's `eager_tools`.
- `hermes_cli/config_defaults.py`: adds `tools.tool_search.eager: []` with a comment.

## Tests

`tests/tools/test_tool_search.py`, class `TestEagerTools`:

- `test_eager_config_parses_and_defaults_empty`: default empty, list parsing/stripping, scalar warned and ignored.
- `test_plugin_tool_defers_unless_eager`: a registered plugin tool defers by default (control) and not when eager.
- `test_eager_wins_over_defer_for_core_tool`: `process_manage` in both lists stays direct.
- `test_assembly_keeps_eager_plugin_tool_visible`: `assemble_tool_defs` keeps the eager plugin tool and defers the
  other one.
- `test_eager_tool_is_refused_by_tool_call_bridge`: `resolve_underlying_call` refuses it and
  `scoped_deferrable_names` excludes it.

Live check (not an automated test): a `hermes chat -q` session run from this commit with
`eager: [process_manage, drive_preview, cross_session]` reported all of process_manage, todo_list and
cross_session as direct; on the previous core cross_session was deferred.

## Applying

Standalone; touches only `tools/tool_search.py`, its test file and `config_defaults.py`.

## Gotchas

- Each eager tool's schema is sent on every turn; keep the list to tools used in most sessions.
- Eager only changes deferral. The tool must still be in the session's toolsets (e.g. `drive_preview` exists only
  in Desktop sessions).

## Plugin?

No. Deferral is decided in core `is_deferrable_tool_name`, and no plugin hook can change it.
